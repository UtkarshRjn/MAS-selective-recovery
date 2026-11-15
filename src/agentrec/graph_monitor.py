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


async def run_graph_case(template: str, fault: str, policy: str = "online") -> GraphReport:
    if policy not in GRAPH_POLICIES:
        raise ValueError(f"unknown policy {policy}")
    if template not in TEMPLATES:
        raise KeyError(template)
    clean = clean_values(template)
    if fault not in clean:
        raise KeyError(fault)
    plan = graph_plan(template)
    truth = {f"claim:{key}": value for key, value in clean.items()}
    monitor = MonitorRuntime(
        plan,
        worker_count=2,
        policy=policy,
        correction_budget=2,
        truth=truth,
        corrections={f"claim:{fault}": clean[fault]},
        allow_provisional=True,
    )
    monitor.graph_fault = fault
    monitor.defer_input_verification = True
    producers = {node.agent_id: _produce for node in plan.task_nodes}
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
    try:
        await monitor.wait_until(lambda item: _done(item, clean), timeout=5)
        async with monitor.lock:
            monitor.settled = True
        await monitor._notify(["progress"])
        await asyncio.wait_for(schedule, 5)
    finally:
        if not schedule.done():
            async with monitor.lock:
                monitor.settled = True
            await monitor._notify(["progress"])
            await asyncio.wait_for(schedule, 5)
        await monitor.stop()
    return _report(template, fault, policy, monitor, clock, clean)


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
