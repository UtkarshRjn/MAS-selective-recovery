"""Run the six provenance shapes on the online monitor.

Each step writes one claim. A corrupt step emits ``BAD`` once. Later steps may
run on that provisional value. Verification of an output waits until its inputs
are verified, so a downstream claim is not committed on top of a bad input.
``online`` and ``coarse`` then keep a step that never read the fault. ``suffix``
reruns every later step. ``restart`` reruns the whole shape.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from .contract import AgentAttempt, EvidenceStatus, Plan, TaskNode
from .graphs import TEMPLATES, clean_values, step_value
from .llm import chat_async
from .monitor_runtime import MonitorRuntime
from .scheduler import Holds, OverlapClock, Scheduler, run_mock

GRAPH_POLICIES = ("online", "support", "coarse", "suffix", "restart")


def graph_plan(template: str) -> Plan:
    steps = TEMPLATES[template]
    read_by = {src for _key, reads in steps for src in reads}
    nodes = tuple(
        TaskNode(
            key,
            key,
            () if key in read_by else (key,),
            tuple(f"claim:{src}" for src in reads),
            (f"claim:{key}",),
        )
        for key, reads in steps
    )
    edges = tuple((src, key) for key, reads in steps for src in reads)
    goals = tuple(node.task_id for node in nodes if node.goals)
    return Plan(1, nodes, edges, goals)


@dataclass(frozen=True)
class GraphReport:
    template: str
    fault: str
    policy: str
    success: bool
    calls: int
    repeated: int
    preserved: tuple[str, ...]
    rerun: tuple[str, ...]
    blocked: int = 0
    stale: int = 0
    mismatches: tuple[str, ...] = ()


def _produce(monitor: MonitorRuntime, snapshot: dict, attempt: AgentAttempt):
    node = monitor.plan.node(attempt.task_id)
    if node is None:
        for plan in monitor.plans.values():
            node = plan.node(attempt.task_id)
            if node is not None:
                break
    assert node is not None
    logical = attempt.agent_id
    inputs = [snapshot[claim_id] for claim_id in node.reads]
    if monitor.faults_enabled and logical == monitor.graph_fault:
        value = "BAD"
    else:
        value = step_value(logical, inputs)
    return {node.writes[0]: value}


def _latest(monitor: MonitorRuntime, claim_id: str):
    live = [
        item for item in monitor.graph.versions(claim_id)
        if item.verification_status == EvidenceStatus.VERIFIED
        and item.producer_attempt not in monitor.graph.obsolete_attempts
    ]
    if not live:
        return None
    return max(live, key=lambda item: item.version)


def _done(monitor: MonitorRuntime, clean: dict[str, str]) -> bool:
    if monitor.unresolved:
        return True
    for key, value in clean.items():
        evidence = _latest(monitor, f"claim:{key}")
        if evidence is None or evidence.value != value:
            return False
    return True


def _repair_messages(key: str, inputs: list[str]) -> list[dict[str, str]]:
    from .agents import dumps

    return [
        {
            "role": "system",
            "content": (
                'Reply with one JSON object {"value": "..."}. '
                "If INPUTS_JSON is empty, value is the KEY_JSON string. "
                "Otherwise value is the key, then '(', then the inputs joined by commas, then ')'."
            ),
        },
        {
            "role": "user",
            "content": f"KIND_JSON: {dumps('graph')}\nKEY_JSON: {dumps(key)}\nINPUTS_JSON: {dumps(inputs)}",
        },
    ]


def _propose_graph(llm, template: str, claim_id: str, clean: dict[str, str]):
    key = claim_id.removeprefix("claim:")
    reads = dict(TEMPLATES[template])[key]
    out = llm.chat("corrector", _repair_messages(key, [clean[src] for src in reads]))
    return str(out.get("value", "")).strip().replace(", ", ",")


async def _produce_with_llm(llm, monitor: MonitorRuntime, snapshot: dict, attempt: AgentAttempt):
    node = monitor.plan.node(attempt.task_id)
    if node is None:
        for plan in monitor.plans.values():
            node = plan.node(attempt.task_id)
            if node is not None:
                break
    assert node is not None
    logical = attempt.agent_id
    if monitor.faults_enabled and logical == monitor.graph_fault:
        return {node.writes[0]: "BAD"}
    inputs = [str(snapshot[claim_id]) for claim_id in node.reads]
    out = await chat_async(llm, "graph", _repair_messages(logical, inputs))
    value = str(out.get("value", "")).strip().replace(", ", ",")
    return {node.writes[0]: value}


async def run_graph_case(
    template: str,
    fault: str,
    policy: str = "online",
    *,
    llm=None,
    corrections: dict | None = None,
    correction_budget: int = 2,
) -> GraphReport:
    if policy not in GRAPH_POLICIES:
        raise ValueError(f"unknown policy {policy}")
    if template not in TEMPLATES:
        raise KeyError(template)
    clean = clean_values(template)
    if fault not in clean:
        raise KeyError(fault)
    plan = graph_plan(template)
    truth = {f"claim:{key}": value for key, value in clean.items()}
    supplied = {f"claim:{fault}": clean[fault]} if llm is None and corrections is None else (corrections or {})
    monitor = MonitorRuntime(
        plan,
        worker_count=2,
        policy=policy,
        correction_budget=correction_budget,
        truth=truth,
        corrections=supplied,
        allow_provisional=True,
    )
    monitor.graph_fault = fault
    monitor.defer_input_verification = True
    if llm is not None:
        monitor.proposer = lambda claim_id, llm=llm, template=template, clean=clean: _propose_graph(
            llm, template, claim_id, clean
        )
    producers = {
        node.agent_id: (_produce if llm is None else (lambda m, s, a, llm=llm: _produce_with_llm(llm, m, s, a)))
        for node in plan.task_nodes
    }
    clock = OverlapClock()
    holds = Holds(set())

    async def runner(runtime: MonitorRuntime, attempt: object, node: TaskNode) -> None:
        await run_mock(
            runtime,
            attempt,  # type: ignore[arg-type]
            node,
            producers=producers,
            actions={},
            clock=clock,
            holds=holds,
        )

    scheduler = Scheduler(monitor, runner, concurrency=max(4, len(plan.task_nodes)))
    await monitor.start()
    schedule = asyncio.create_task(scheduler.run())
    # A model call is silent on the progress condition, so the wait has to
    # outlast one completion. The mock path stays on the short bound.
    limit = 180 if llm is not None else 5
    try:
        await monitor.wait_until(lambda item: _done(item, clean), timeout=limit)
        async with monitor.lock:
            monitor.settled = True
        await monitor._notify(["progress"])
        await asyncio.wait_for(schedule, limit)
    finally:
        if not schedule.done():
            async with monitor.lock:
                monitor.settled = True
            await monitor._notify(["progress"])
            await asyncio.wait_for(schedule, limit)
        await monitor.stop()
    return _report(template, fault, policy, monitor, clock, clean)


def _mismatches(monitor: MonitorRuntime, clean: dict[str, str]) -> tuple[str, ...]:
    found = []
    for key, expected in clean.items():
        versions = list(monitor.graph.versions(f"claim:{key}"))
        if not versions:
            continue
        latest = max(versions, key=lambda item: item.version)
        if latest.value != expected:
            found.append(f"{key}={latest.value}")
    return tuple(found)


def _report(
    template: str,
    fault: str,
    policy: str,
    monitor: MonitorRuntime,
    clock: OverlapClock,
    clean: dict[str, str],
) -> GraphReport:
    attempts = [
        attempt for attempt in monitor.graph.attempts.values()
        if attempt.agent_id not in {"verifier", "recovery", "corrector"}
    ]
    counts = Counter(attempt.agent_id for attempt in attempts)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    preserved = []
    rerun = []
    for agent_id in counts:
        rows = [attempt for attempt in attempts if attempt.agent_id == agent_id]
        original = [attempt for attempt in rows if attempt.plan_version == 1]
        if len(original) == 1 and original[0].status.value == "completed":
            preserved.append(agent_id)
        if any(attempt.plan_version > 1 for attempt in rows):
            rerun.append(agent_id)
    success = _done(monitor, clean) and monitor.unresolved is None
    return GraphReport(
        template=template,
        fault=fault,
        policy=policy,
        success=success,
        calls=clock.calls,
        repeated=repeated,
        preserved=tuple(sorted(preserved)),
        rerun=tuple(sorted(rerun)),
        blocked=monitor.verification_waits + monitor.dependency_waits + monitor.action_blocks,
        stale=monitor.stale_rejected,
        mismatches=_mismatches(monitor, clean),
    )


async def evaluate_monitor_graphs() -> list[GraphReport]:
    rows: list[GraphReport] = []
    for template, steps in TEMPLATES.items():
        for fault, _reads in steps:
            for policy in GRAPH_POLICIES:
                rows.append(await run_graph_case(template, fault, policy))
    return rows


def format_graph_rows(rows: list[GraphReport]) -> str:
    lines = [f"{'template':<16}{'fault':<8}{'policy':<10}{'ok':>4}{'calls':>7}{'repeated':>10}  preserved"]
    for row in rows:
        preserved = ",".join(row.preserved) or "-"
        lines.append(
            f"{row.template:<16}{row.fault:<8}{row.policy:<10}{str(row.success):>4}"
            f"{row.calls:>7}{row.repeated:>10}  {preserved}"
        )
    return "\n".join(lines)
