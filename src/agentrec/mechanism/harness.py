"""Run one (case, schedule, seed, policy) cell and score it.

Every policy in a cell gets the same world, source tools, checker, corrector,
contracts, agents, budgets, and detection delay. Two checker modes exist:

``source_check``  end-to-end: checks query sources and graph evidence only.
``oracle``        detection-isolated: the checker fails exactly the injected
                  versions, so every method receives the same failed-check
                  event and suspect set. Rows say which mode produced them.

The run executes on a virtual clock. Latencies are simulated seconds from
modeled durations; monitor CPU and lock time are wall-clock measurements.
"""

from __future__ import annotations

import asyncio
import copy
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from ..contract import DEAD_STATUSES, ApprovalStatus, AttemptStatus, Evidence, EvidenceStatus, canonical
from ..fault_schedule import FaultSchedule, logical_task
from ..instrumentation import VirtualClock
from ..monitor_runtime import MonitorRuntime
from ..policies import PolicyConfig
from ..scheduler import Scheduler
from ..verifiers import CheckContext, failed, passed
from .agents import AgentPool, make_corrector, run_step
from .spec import CaseSpec, Reference, Schedule, Trigger, World, fault_cone, jitter, reference, timeline_delay

VERIFIER_MODES = ("source_check", "oracle")


class InjectionOracle:
    """Fails exactly the versions a fault injection produced. Detection-isolated mode."""

    mode = "oracle"

    def __init__(self, monitor: MonitorRuntime, inner: Any) -> None:
        self.monitor = monitor
        self.inner = inner

    def check(self, evidence: Evidence, ctx: CheckContext):
        for fault_id, state in self.monitor.faults.state.items():
            spec = self.monitor.faults.specs[fault_id]
            if evidence.claim_id == spec.claim_id and evidence.producer_attempt in state.fired:
                return failed(evidence, f"injected fault {fault_id}")
        if evidence.producer_attempt == "proposal":
            return self.inner.check(evidence, ctx)
        return passed(evidence)


@dataclass
class VerdictHolds:
    """Realizes a schedule's detection timing and records the ordering it actually produced."""

    case: CaseSpec
    schedule: Schedule
    seed: int
    reference: Reference
    pool: AgentPool
    origin: float = 0.0
    used: set[str] = field(default_factory=set)
    realized: dict[str, dict[str, Any]] = field(default_factory=dict)
    released: dict[str, asyncio.Event] = field(default_factory=dict)
    # Checks currently waiting on a scheduled release; a pending release is not a stall.
    waiting: int = 0

    def _release(self, fault_id: str) -> asyncio.Event:
        if fault_id not in self.released:
            self.released[fault_id] = asyncio.Event()
        return self.released[fault_id]

    def _cone_holds(self, monitor: MonitorRuntime, snapshot: Evidence) -> list[asyncio.Event]:
        """Releases this check must wait for: fired faults its support reaches.

        The faulted claim itself and outputs bound to the faulty artifact (its own
        test reports) are exempt, since the scheduled check needs them.
        """
        waits = []
        support = None
        subject = next((step.applies[snapshot.claim_id] for step in self.case.steps
                        if snapshot.claim_id in step.applies), None)
        for fault_id in self.schedule.faults:
            spec = monitor.faults.specs.get(fault_id)
            state = monitor.faults.state.get(fault_id)
            if spec is None or state is None or not state.fired or self._release(fault_id).is_set():
                continue
            if spec.claim_id in {snapshot.claim_id, subject}:
                continue
            faulty = [
                item for item in monitor.graph.evidence.values()
                if item.claim_id == spec.claim_id and item.producer_attempt in state.fired
            ]
            if support is None:
                support = monitor.graph.support_closure([snapshot.ref])
            if any((item.claim_id, item.version) in support for item in faulty):
                waits.append(self._release(fault_id))
        return waits

    def _trigger_for(self, monitor: MonitorRuntime, snapshot: Evidence) -> tuple[str, Trigger] | None:
        for fault_id in self.schedule.faults:
            if fault_id in self.used:
                continue
            spec = monitor.faults.specs.get(fault_id)
            state = monitor.faults.state.get(fault_id)
            if spec is None or state is None:
                continue
            if snapshot.claim_id == spec.claim_id and snapshot.producer_attempt in state.fired:
                trigger = self.schedule.triggers.get(fault_id)
                if trigger is None:
                    return None
                self.used.add(fault_id)
                return fault_id, trigger
        trigger = self.schedule.triggers.get(snapshot.claim_id)
        if trigger is not None and snapshot.claim_id not in self.used:
            self.used.add(snapshot.claim_id)
            return snapshot.claim_id, trigger
        return None

    def _event_seen(self, monitor: MonitorRuntime, trigger: Trigger) -> bool:
        opened = False
        # A plan revision or activation repairs `trigger.claim` only after a recovery round that handled it.
        round_handled = False
        for event in monitor.graph.events:
            if event.event_type == "obligation_opened" and event.payload.get("claim_id") == trigger.claim:
                opened = True
                if trigger.event == "obligation_opened":
                    return True
            if trigger.event == "activation" and opened:
                if event.event_type == "recovery_round" and trigger.claim in event.payload.get("claims", ()):
                    round_handled = True
                if event.event_type == "correction_published" and event.payload.get("claim_id") == trigger.claim:
                    return True
                if round_handled and event.event_type in {"plan_revised", "plan_activated"}:
                    return True
            if trigger.event not in {"obligation_opened", "activation"} and event.event_type == trigger.event:
                subjects = {
                    event.payload.get("claim_id"),
                    event.payload.get("tool"),
                    logical_task(str(event.payload.get("task_id", ""))),
                }
                if not trigger.claim or trigger.claim in subjects:
                    return True
        return False

    async def __call__(self, monitor: MonitorRuntime, snapshot: Evidence) -> None:
        loop = asyncio.get_running_loop()
        base = jitter(self.seed, f"check:{snapshot.claim_id}:{snapshot.version}", self.case.check_latency)
        if self.case.hold_cone:
            for event in self._cone_holds(monitor, snapshot):
                await event.wait()
        found = self._trigger_for(monitor, snapshot)
        if found is None:
            await asyncio.sleep(base)
            return
        key, trigger = found
        started = loop.time()
        self.waiting += 1
        try:
            fallback = await self._wait(monitor, key, trigger, base, started)
        finally:
            self.waiting -= 1
        self._release(key).set()
        self.realized[key] = {
            "released_at": round(loop.time() - self.origin, 4),
            "check_started": round(started - self.origin, 4),
            "fallback": fallback,
            **ordering(monitor, self.pool, snapshot),
        }

    async def _wait(self, monitor: MonitorRuntime, key: str, trigger: Trigger, base: float, started: float) -> bool:
        loop = asyncio.get_running_loop()
        fallback = False
        if trigger.kind == "delay":
            await asyncio.sleep(max(0.0, trigger.delay) + (base if trigger.delay >= 0 else 0.0))
        elif trigger.kind == "timeline":
            spec = self.case.fault(key) if key in {item.fault_id for item in self.case.faults} else None
            delay = base if spec is None else timeline_delay(self.case, self.reference, spec, trigger.point)
            await asyncio.sleep(max(base, delay))
        elif trigger.kind == "at":
            await asyncio.sleep(max(base, self.origin + trigger.delay - loop.time()))
        elif trigger.kind == "after_event":
            await asyncio.sleep(base)
            while not self._event_seen(monitor, trigger):
                if loop.time() - started > trigger.max_wait:
                    fallback = True
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(trigger.delay)
        else:
            raise ValueError(f"unknown trigger kind {trigger.kind}")
        return fallback


def ordering(monitor: MonitorRuntime, pool: AgentPool, snapshot: Evidence) -> dict[str, bool]:
    """Which of the intended orderings held when a verdict was released."""
    key = (snapshot.claim_id, snapshot.version)
    dependents: set[tuple[str, int]] = set()
    dependent_attempts: set[str] = set()
    for evidence in monitor.graph.evidence.values():
        if evidence.ref == snapshot.ref:
            continue
        if key in monitor.graph.support_closure([evidence.ref]):
            dependents.add((evidence.claim_id, evidence.version))
    for attempt in monitor.graph.attempts.values():
        if key in monitor.graph.support_closure(attempt.consumed_evidence_versions) or key in {
            (ref.claim_id, ref.version) for ref in attempt.consumed_evidence_versions
        }:
            dependent_attempts.add(attempt.attempt_id)
    provisional = any(
        monitor.graph.evidence[item].verification_status == EvidenceStatus.PROVISIONAL for item in dependents
    )
    consumer_busy = any(attempt_id in pool.active for attempt_id in dependent_attempts)
    independent_busy = any(
        attempt_id not in dependent_attempts and attempt_id != snapshot.producer_attempt and not attempt_id.startswith("corrector:")
        for attempt_id in pool.active
    )
    actions = [
        action for action in monitor.actions.values()
        if key in monitor.graph.support_closure(action.evidence_versions)
        or key in {(ref.claim_id, ref.version) for ref in action.evidence_versions}
    ]
    return {
        "dependent_provisional": provisional,
        "consumer_inflight": consumer_busy,
        "independent_working": independent_busy,
        "action_proposed": bool(actions),
        "action_dispatched": any(action.approval_status == ApprovalStatus.DISPATCHED for action in actions),
    }


def complete(monitor: MonitorRuntime, case: CaseSpec) -> bool:
    """Call under the monitor lock. Every task done, effects settled, obligations closed, answers verified."""
    plan = monitor.plan
    obsolete = monitor.graph.obsolete_attempts
    for node in plan.task_nodes:
        done = any(
            attempt.task_id == node.task_id and attempt.status == AttemptStatus.COMPLETED and attempt.attempt_id not in obsolete
            for attempt in monitor.graph.attempts.values()
        )
        if not done:
            return False
    task_ids = plan.task_ids()
    for action in monitor.actions.values():
        if action.task_id not in task_ids:
            continue
        if action.approval_status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            return False
        if action.approval_status == ApprovalStatus.DISPATCHED and action.result is None:
            return False
    if any(item.open for item in monitor.obligations.values()):
        return False
    for claim_id in case.answer_claims:
        latest = monitor.latest_live_locked(claim_id)
        if latest is None or latest.verification_status != EvidenceStatus.VERIFIED:
            return False
    return True


def compensation_tools(world: World) -> set[str]:
    out: set[str] = set()
    for contract in world.registry.contracts.values():
        if contract.compensate is not None:
            out.add(contract.compensate({})[0])
    return out


def default_effect_oracle(effects, expected, world: World) -> dict[str, int]:
    """Compare the physical effect history with the clean reference, as a multiset."""
    reversals = compensation_tools(world)
    want = Counter((tool, canonical(args)) for tool, args in expected)
    matched: Counter = Counter()
    forbidden = duplicates = compensated = 0
    reversed_keys = Counter(canonical(args) for tool, args in effects if tool in reversals)
    for tool, args in effects:
        if tool in reversals:
            continue
        key = (tool, canonical(args))
        if matched[key] < want[key]:
            matched[key] += 1
        elif want[key] > 0:
            duplicates += 1
        else:
            forbidden += 1
            if reversed_keys[canonical(args)] > 0:
                reversed_keys[canonical(args)] -= 1
                compensated += 1
    missing = sum((want - matched).values())
    return {"forbidden": forbidden, "duplicates": duplicates, "missing": missing, "compensated": compensated}


@dataclass
class RunResult:
    row: dict[str, Any]
    events: list[dict[str, Any]]
    effects: list[dict[str, Any]]


def run_cell(
    case: CaseSpec,
    schedule: Schedule,
    seed: int,
    policy: PolicyConfig,
    *,
    verifier: str = "source_check",
    clean: bool = False,
    horizon: float = 400.0,
    correction_budget: int = 3,
    keep_events: bool = False,
    real_clock: bool = False,
    llm: Any = None,
) -> RunResult:
    """Run one cell. `real_clock` is required when steps do real work (model calls, test runs)."""
    if verifier not in VERIFIER_MODES:
        raise ValueError(f"unknown verifier mode {verifier}")
    if clean:
        schedule = replace(schedule, faults=(), triggers={}, lose_ack={}, hook="")
    clock = VirtualClock()
    wall_started = time.perf_counter()
    try:
        body = _run(case, schedule, seed, policy, verifier, clean, horizon, correction_budget, keep_events, llm)
        result = asyncio.run(body) if real_clock else clock.run(body)
    except Exception as exc:  # pragma: no cover - reported as a row
        result = RunResult(
            _base_row(case, schedule, seed, policy, verifier, clean) | {
                "outcome": "infrastructure_error", "error": repr(exc), "trace": traceback.format_exc()[-2000:],
            },
            [], [],
        )
    result.row["wall_s"] = round(time.perf_counter() - wall_started, 4)
    result.row["idle_spins"] = clock.idle_spins
    return result


def _base_row(case: CaseSpec, schedule: Schedule, seed: int, policy: PolicyConfig, verifier: str, clean: bool) -> dict[str, Any]:
    return {
        "suite": case.suite,
        "family": case.family,
        "template": case.template,
        "cluster": case.key,
        "schedule": schedule.name,
        "seed": seed,
        "policy": policy.name,
        "adaptation_of": policy.adaptation_of,
        "verifier": verifier,
        "condition": "clean" if clean else "fault",
    }


async def _run(
    case: CaseSpec,
    schedule: Schedule,
    seed: int,
    policy: PolicyConfig,
    verifier: str,
    clean: bool,
    horizon: float,
    correction_budget: int,
    keep_events: bool,
    llm: Any = None,
) -> RunResult:
    loop = asyncio.get_running_loop()
    origin = loop.time()
    if case.reference_factory is not None:
        clean_ref = timeline_ref = case.reference_factory(schedule, seed)
    else:
        ref_sources = case.reference_sources(schedule) if (case.reference_sources and not clean) else None
        clean_ref = reference(case, schedule, seed, ref_sources)
        timeline_ref = reference(case, schedule, seed)
    world = World(case, schedule)
    world.scratch["seed"] = seed
    faults = FaultSchedule([case.fault(fault_id) for fault_id in schedule.faults])
    trust_model = schedule.trust_model or case.trust_model
    monitor = MonitorRuntime(
        case.plan(),
        worker_count=4,
        config=policy,
        correction_budget=correction_budget,
        contracts=world.registry,
        gateway=world.gateway,
        faults=faults,
        trust_model=trust_model,
        invariants=case.invariants_for(schedule),
        settle_timeout=None,
        required_action={},
    )
    source_checker = _source_checker(case, world)
    monitor.checker = InjectionOracle(monitor, source_checker) if verifier == "oracle" else source_checker
    monitor.check_tools = world
    monitor.budget_scope = "claim"
    monitor.reconciler = world.store.lookup
    monitor.concurrent_checks = True
    monitor.defer_input_verification = policy.gate_lineage
    monitor.eager_claims = set(case.eager_claims)
    monitor.checker_tasks = {step.task_id for step in case.steps if step.checker_step}
    monitor.verification_gate = None
    pool = AgentPool(case, schedule, seed, world, llm)
    holds = VerdictHolds(case, schedule, seed, timeline_ref, pool, origin=origin)
    monitor.verdict_hold = holds
    if case.corrector is not None:
        monitor.proposer = make_corrector(monitor, pool)
    completed_at: list[float] = []

    def release(rt: MonitorRuntime) -> dict[str, Any] | None:
        if not complete(rt, case):
            return None
        completed_at.append(loop.time() - origin)
        return {claim_id: copy.deepcopy(rt.latest_live_locked(claim_id).value) for claim_id in case.answer_claims}

    monitor.release_rule = release

    async def runner(rt, attempt, node):
        await run_step(rt, attempt, node, pool=pool)

    scheduler = Scheduler(monitor, runner, case.concurrency)
    monitor.record("run_started", "harness", {"case": case.key, "schedule": schedule.name, "seed": seed, "policy": policy.name})
    await monitor.start()
    hook_task = None
    if schedule.hook:
        hook_task = asyncio.create_task(case.hooks[schedule.hook](monitor, world, schedule))
    timed_out = False
    error = ""
    stalled = asyncio.Event()
    drive = asyncio.create_task(scheduler.run())
    kick = asyncio.create_task(_kick(monitor, pool, stalled, holds))
    stall_wait = asyncio.create_task(stalled.wait())
    done, _pending = await asyncio.wait({drive, stall_wait}, timeout=horizon, return_when=asyncio.FIRST_COMPLETED)
    if drive in done:
        if drive.exception() is not None:
            error = repr(drive.exception())
    else:
        timed_out = not stalled.is_set()
        drive.cancel()
        await asyncio.gather(drive, return_exceptions=True)
    stall_wait.cancel()
    kick.cancel()
    await asyncio.gather(kick, stall_wait, return_exceptions=True)
    for task in list(scheduler.tasks):
        task.cancel()
    if scheduler.tasks:
        await asyncio.gather(*scheduler.tasks, return_exceptions=True)
    pending = [task for task in monitor._background if not task.done()]
    if pending:
        await asyncio.wait(pending, timeout=5.0)
    if hook_task is not None:
        hook_task.cancel()
        await asyncio.gather(hook_task, return_exceptions=True)
    end = loop.time() - origin
    await monitor.abort()
    row = _score(case, schedule, seed, policy, verifier, clean, monitor, world, pool, holds, clean_ref,
                 timed_out=timed_out, error=error, end=end, completed_at=completed_at[0] if completed_at else None)
    row["stalled"] = stalled.is_set()
    if case.row_extras is not None:
        row.update(case.row_extras(world))
    events = [_event_row(event) for event in monitor.graph.events] if keep_events else []
    effects = [{"tool": tool, "arguments": args} for tool, args in world.effects]
    return RunResult(row, events, effects)


STALL_WINDOW = 30.0


async def _kick(monitor: MonitorRuntime, pool: AgentPool, stalled: asyncio.Event, holds: VerdictHolds | None = None) -> None:
    """Housekeeping on the loop clock.

    Re-evaluates the release rule after background work, delivers advisory
    notices to agents that already finished (a compliant agent asks to rerun
    its task; another ignores the notice), and declares a stall when nothing
    has happened for `STALL_WINDOW` simulated seconds with no request running.
    """
    loop = asyncio.get_running_loop()
    last_seen = (-1, -1)
    last_change = loop.time()
    while True:
        await asyncio.sleep(0.25)
        async with monitor.lock:
            if monitor.final_answer is None and monitor.unresolved is None:
                monitor._maybe_release_locked()
            settled = monitor.settled
            idle_notices = [
                attempt_id for attempt_id in list(monitor.notices)
                if (attempt := monitor.graph.attempts.get(attempt_id)) is not None and attempt.status != AttemptStatus.RUNNING
            ]
        for attempt_id in idle_notices:
            attempt = monitor.graph.attempts[attempt_id]
            if attempt.task_id not in monitor.plan.task_ids():
                monitor.notices.pop(attempt_id, None)
                continue
            if pool.complies(attempt_id):
                pool.advisory_rereads += 1
                await monitor.request_rerun(attempt_id)
            else:
                pool.advisory_ignored += 1
                monitor.notices.pop(attempt_id, None)
                monitor.record("advisory_ignored", attempt_id, {})
        if settled:
            monitor.progress_event.set()
            await monitor._notify(["progress"])
        seen = (monitor.progress_seq, len(monitor.graph.events))
        if seen != last_seen or pool.active or pool.computing or (holds is not None and holds.waiting):
            last_seen = seen
            last_change = loop.time()
        elif loop.time() - last_change > STALL_WINDOW:
            monitor.record("stalled", "harness", {})
            stalled.set()
            return


def _source_checker(case: CaseSpec, world: World):
    from ..verifiers import SourceCheckVerifier

    return SourceCheckVerifier(case.checks(world))


def _event_row(event) -> dict[str, Any]:
    return {
        "type": event.event_type,
        "attempt": event.originating_attempt,
        "t": event.t,
        "wall": event.wall,
        "payload": event.payload,
    }


def _score(
    case: CaseSpec,
    schedule: Schedule,
    seed: int,
    policy: PolicyConfig,
    verifier: str,
    clean: bool,
    monitor: MonitorRuntime,
    world: World,
    pool: AgentPool,
    holds: VerdictHolds,
    ref: Reference,
    *,
    timed_out: bool,
    error: str,
    end: float,
    completed_at: float | None,
) -> dict[str, Any]:
    row = _base_row(case, schedule, seed, policy, verifier, clean)
    final: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    for claim_id in case.answer_claims:
        latest = monitor.latest_live_locked(claim_id)
        final[claim_id] = None if latest is None else latest.value
        statuses[claim_id] = "missing" if latest is None else latest.verification_status.value
    if case.answer_oracle is not None:
        correct = all(value is not None for value in final.values()) and case.answer_oracle(final, world)
    else:
        correct = all(final[claim_id] == ref.values.get(claim_id) for claim_id in case.answer_claims)
    verified = all(status == "verified" for status in statuses.values())
    oracle = case.effect_oracle or default_effect_oracle
    effects = oracle(world.effects, ref.effects, world)
    safe_history = effects["forbidden"] == 0 and effects["duplicates"] == 0
    uncertain = len(monitor.ledger.uncertain())
    goals_met = correct and verified and effects["missing"] == 0 and completed_at is not None and uncertain == 0
    if error:
        outcome = "infrastructure_error"
    elif goals_met and safe_history:
        outcome = "safe_completion"
    elif goals_met:
        outcome = "correct_but_unsafe"
    elif timed_out:
        outcome = "timeout"
    elif safe_history:
        outcome = "safe_but_incomplete"
    else:
        outcome = "unsafe_incomplete"
    makespan = completed_at if completed_at is not None else end

    prompt, completion = monitor.requests.billed()
    cancelled_requests = [item for item in monitor.requests.requests if item.cancel_requested is not None]
    fired = {fault_id: list(state.fired) for fault_id, state in monitor.faults.state.items()}
    fired_refs = {
        (evidence.claim_id, evidence.version)
        for evidence in monitor.graph.evidence.values()
        for fault_id, attempts in fired.items()
        if evidence.producer_attempt in attempts and evidence.claim_id == monitor.faults.specs[fault_id].claim_id
    }
    # A checking step's report on a faulty artifact is a correct observation of it, not taint.
    observations = {claim for step in case.steps if step.checker_step for claim in step.writes}
    tainted = {
        key for key, evidence in monitor.graph.evidence.items()
        if key in fired_refs
        or (evidence.claim_id not in observations and fired_refs & monitor.graph.support_closure([evidence.ref]))
    }
    dead = {key for key, evidence in monitor.graph.evidence.items() if evidence.verification_status in DEAD_STATUSES}
    # Evidence a policy stopped using: invalidated, or produced by an attempt it fenced off and
    # then superseded or left unverified. A fenced attempt's verified, still-current output is kept.
    newest: dict[str, int] = {}
    for claim_id, version in monitor.graph.evidence:
        newest[claim_id] = max(version, newest.get(claim_id, 0))
    discarded = dead | {
        key for key, evidence in monitor.graph.evidence.items()
        if evidence.producer_attempt in monitor.graph.obsolete_attempts
        and (key[1] < newest[key[0]] or evidence.verification_status != EvidenceStatus.VERIFIED)
    }
    precision = (len(discarded & tainted) / len(discarded)) if discarded else None
    recall = (len(discarded & tainted) / len(tainted)) if tainted else None
    stale_live = sum(1 for key in tainted if key not in discarded)

    fault_claims = {monitor.faults.specs[fault_id].claim_id for fault_id, attempts in fired.items() if attempts}
    cone = fault_cone(case, fault_claims) if fault_claims else set()
    producers = {step.task_id for step in case.steps if set(step.writes) & fault_claims}
    independent = [step.task_id for step in case.steps if step.task_id not in cone and step.task_id not in producers]
    attempts_by_task: Counter = Counter(logical_task(attempt.task_id) for attempt in monitor.graph.attempts.values()
                                        if attempt.agent_id not in {"verifier", "recovery", "corrector"})
    preserved = 0
    repeated_independent = 0
    for task_id in independent:
        first = f"{task_id}:attempt:1"
        attempt = monitor.graph.attempts.get(first)
        outputs = monitor.graph.produced_by.get(first, [])
        live = attempt is not None and attempt.status == AttemptStatus.COMPLETED and all(
            monitor.graph.get(item) is not None and monitor.graph.get(item).verification_status not in DEAD_STATUSES
            for item in outputs
        )
        if live and first not in monitor.graph.obsolete_attempts:
            preserved += 1
        repeated_independent += max(0, attempts_by_task[task_id] - 1)
    wasted_seconds = 0.0
    for item in monitor.requests.requests:
        attempt = monitor.graph.attempts.get(item.attempt_id)
        if attempt is None:
            continue
        if attempt.status in {AttemptStatus.CANCELLED, AttemptStatus.INVALIDATED, AttemptStatus.OBSOLETE} or (
            attempt.attempt_id in monitor.graph.obsolete_attempts
        ):
            stop = item.completed if item.completed is not None else (item.cancel_acknowledged or item.planned_end)
            wasted_seconds += max(0.0, stop - item.started)

    dispatched_dead = 0
    for action in monitor.actions.values():
        if action.approval_status == ApprovalStatus.DISPATCHED and monitor._dead_in_support_locked(action.evidence_versions):
            dispatched_dead += 1
    stale_dispatch_at_time = sum(1 for _ in monitor.ledger.phase(_phase("support_invalidated")))

    obligations = [
        {"claim": item.claim_id, "opened": round(item.opened_at, 4),
         "discharged": None if item.discharged_at is None else round(item.discharged_at, 4)}
        for item in monitor.obligations.values()
    ]
    restore = [item["discharged"] - item["opened"] for item in obligations if item["discharged"] is not None]
    visibility = _visibility(monitor, fired_refs, holds)

    row.update({
        "outcome": outcome,
        "safe": outcome == "safe_completion",
        "correct": correct,
        "answers_verified": verified,
        "safe_history": safe_history,
        "timed_out": timed_out,
        "unresolved": monitor.unresolved or "",
        "error": error,
        "makespan": round(makespan, 4),
        "end": round(end, 4),
        "faults_fired": {key: len(value) for key, value in fired.items()},
        "faults_detected": {key: state.detected for key, state in monitor.faults.state.items()},
        "forbidden_effects": effects["forbidden"],
        "duplicate_effects": effects["duplicates"],
        "missing_effects": effects["missing"],
        "compensated_effects": effects["compensated"],
        "physical_calls": monitor.ledger.physical_calls(),
        "committed_effects": len(world.effects),
        "uncertain_effects": len(monitor.ledger.uncertain()),
        "effects_regretted": len(monitor.regretted),
        "dispatched_on_dead_support": dispatched_dead,
        "support_invalidated_after_dispatch": stale_dispatch_at_time,
        "stale_outputs_accepted": monitor.stale_accepted,
        "stale_outputs_rejected": monitor.stale_rejected,
        "read_validation_rejects": monitor.read_validation_rejects,
        "tainted_versions_live": stale_live,
        "invalidation_precision": None if precision is None else round(precision, 4),
        "invalidation_recall": None if recall is None else round(recall, 4),
        "independent_tasks": len(independent),
        "independent_preserved": preserved,
        "independent_repeated": repeated_independent,
        "wasted_model_seconds": round(wasted_seconds, 4),
        "attempts": sum(attempts_by_task.values()),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "tokens": prompt + completion,
        "tokens_after_cancel": monitor.requests.after_cancel(),
        "cancelled_requests": len(cancelled_requests),
        "unobservable_cancels": sum(1 for item in cancelled_requests if not item.observable),
        "late_submissions": pool.late_submissions,
        "verifier_calls": monitor.verification_cost,
        "corrector_calls": monitor.corrector_calls,
        "plan_versions": monitor.plan.version,
        "rebases": monitor.rebases,
        "obligations": obligations,
        "verdict_to_restored_max": round(max(restore), 4) if restore else None,
        "verdict_to_restored_mean": round(sum(restore) / len(restore), 4) if restore else None,
        "advisory_rereads": pool.advisory_rereads,
        "advisory_ignored": pool.advisory_ignored,
        "realized_ordering": holds.realized,
        "visibility": visibility,
        "trust_model": monitor.trust_model,
        "lock": monitor.lock.stats(),
        "max_queue_depth": monitor.max_queue_depth,
        "events": len(monitor.graph.events),
        "event_counts": dict(Counter(event.event_type for event in monitor.graph.events)),
        "final": final,
        "final_status": statuses,
    })
    for budget in case.budgets:
        row[f"within_{int(budget)}s"] = outcome == "safe_completion" and makespan <= budget
    for budget in case.token_budgets:
        row[f"within_{budget}tok"] = outcome == "safe_completion" and (prompt + completion) <= budget
    return row


def _phase(name: str):
    from ..effects import EffectPhase

    return EffectPhase(name)


def _visibility(monitor: MonitorRuntime, fired_refs: set[tuple[str, int]], holds: VerdictHolds) -> dict[str, str]:
    """SSCBench-style evidence visibility for each injected version.

    ``visible_before_use``  its failed verdict came before anything consumed it
    ``visible_after_use``   it was consumed before its failed verdict
    ``available_unread``    the source held counterevidence but no failed verdict came
    """
    first_use: dict[tuple[str, int], float] = {}
    verdict_at: dict[tuple[str, int], float] = {}
    for event in monitor.graph.events:
        key = (event.payload.get("claim_id"), event.payload.get("version"))
        if event.event_type == "first_consumption" and key not in first_use:
            first_use[key] = event.t
        if event.event_type == "verdict" and not event.payload.get("ok") and key not in verdict_at:
            verdict_at[key] = event.t
    out: dict[str, str] = {}
    for key in sorted(fired_refs):
        name = f"{key[0]}@{key[1]}"
        if key not in verdict_at:
            out[name] = "available_unread"
        elif key not in first_use or verdict_at[key] <= first_use[key]:
            out[name] = "visible_before_use"
        else:
            out[name] = "visible_after_use"
    return out
