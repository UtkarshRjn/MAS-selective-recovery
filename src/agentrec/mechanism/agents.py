"""Deterministic agents for the mechanism suite.

Each step reads its claims through the monitor, makes one modeled model call
(a sleep on the loop clock with a token count), computes its outputs, passes
them through the fault schedule at the output boundary, and submits outputs
and effect proposals. A model-backed agent plugs in through `StepContext.llm`.

Two request transports are modeled. A cancellable request stops when its task
is cancelled and bills tokens only up to that point. An uncancellable request
keeps running on the server: the client swallows the cancellation, waits for
the result, and then submits it late, which is what the monitor must fence.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import random
from dataclasses import dataclass, field
from typing import Any

from ..contract import AgentAttempt, ArtifactRef, Evidence, TaskNode
from ..fault_schedule import logical_task
from ..instrumentation import RequestRecord
from ..monitor_runtime import CancelledAttempt, MonitorRuntime, Rejected
from ..protocol import ActionProposed, AttemptCompleted, CancellationAcknowledged, OutputProposed
from .spec import CaseSpec, Schedule, StepContext, StepSpec, World, jitter


@dataclass
class AgentPool:
    """Shared state of the agents in one run."""

    case: CaseSpec
    schedule: Schedule
    seed: int
    world: World
    llm: Any = None
    active: dict[str, RequestRecord] = field(default_factory=dict)
    attempts_by_task: dict[str, int] = field(default_factory=dict)
    advisory_rereads: int = 0
    advisory_ignored: int = 0
    late_submissions: int = 0

    def step_for(self, node: TaskNode) -> StepSpec:
        return self.case.step(logical_task(node.task_id))

    def duration(self, step: StepSpec, number: int) -> float:
        base = self.schedule.durations.get(step.task_id, step.duration)
        base = self.schedule.attempt_durations.get((step.task_id, number), base)
        return jitter(self.seed, f"{step.task_id}#{number}", base)

    def cancellable(self, step: StepSpec, number: int) -> bool:
        return not (step.task_id in self.schedule.uncancellable and number == 1)

    def complies(self, attempt_id: str) -> bool:
        rng = random.Random(f"{self.seed}:advisory:{attempt_id}")
        return rng.random() < self.case.advisory_compliance


async def model_call(
    monitor: MonitorRuntime,
    pool: AgentPool,
    attempt_id: str,
    duration: float,
    tokens: tuple[int, int],
    *,
    cancellable: bool,
) -> RequestRecord:
    loop = asyncio.get_running_loop()
    now = loop.time()
    record = RequestRecord(attempt_id, now, now + duration, tokens[0], tokens[1], cancellable)
    monitor.requests.requests.append(record)
    pool.active[attempt_id] = record
    monitor.record("request_started", attempt_id, {"duration": duration})
    try:
        while True:
            try:
                await asyncio.sleep(max(0.0, record.planned_end - loop.time()))
                break
            except asyncio.CancelledError:
                at = loop.time()
                if record.cancel_requested is None:
                    record.cancel_requested = at
                if cancellable:
                    record.cancel_acknowledged = at
                    span = max(record.planned_end - record.started, 1e-9)
                    record.billed_prompt = record.prompt_tokens
                    record.billed_completion = int(round(record.completion_tokens * (at - record.started) / span))
                    monitor.record("request_cancelled", attempt_id, {"billed_completion": record.billed_completion})
                    raise
                record.observable = False
                task = asyncio.current_task()
                if task is not None:
                    task.uncancel()
                monitor.record("cancel_ignored_by_transport", attempt_id, {})
        record.completed = loop.time()
        record.billed_prompt = record.prompt_tokens
        record.billed_completion = record.completion_tokens
        record.delivered = True
        monitor.record("request_completed", attempt_id, {})
        return record
    finally:
        pool.active.pop(attempt_id, None)


def _required(pool: AgentPool, step: StepSpec, claim_id: str, evidence: dict[str, Evidence]) -> tuple[ArtifactRef, ...]:
    target = step.bind.get(claim_id)
    if target is None or target not in evidence:
        return ()
    bound = tuple(evidence[item].artifact for item in step.context.get(claim_id, ()) if item in evidence)
    return (evidence[target].artifact, *bound, *pool.case.identity)


async def run_step(monitor: MonitorRuntime, attempt: AgentAttempt, node: TaskNode, *, pool: AgentPool) -> None:
    step = pool.step_for(node)
    number = pool.attempts_by_task.get(step.task_id, 0) + 1
    pool.attempts_by_task[step.task_id] = number
    account = monitor.accounts[attempt.attempt_id]
    attempt_id = attempt.attempt_id
    values: dict[str, Any] = {}
    evidence: dict[str, Evidence] = {}
    cancellable = pool.cancellable(step, number)

    def stop_requested() -> bool:
        return cancellable and monitor.cancel_requested(attempt_id)

    def meter(prompt: int, completion: int, started: float, ended: float) -> None:
        record = RequestRecord(attempt_id, started, ended, prompt, completion, cancellable=False)
        record.completed = ended
        record.billed_prompt = prompt
        record.billed_completion = completion
        record.delivered = True
        if monitor.cancel_requested(attempt_id):
            record.cancel_requested = started
            record.observable = False
        monitor.requests.requests.append(record)
        account.log_usage(prompt, completion, task=step.task_id)

    def context() -> StepContext:
        return StepContext(pool.world, number, evidence, pool.case.identity, pool.llm, meter)

    async def read(claim_id: str) -> None:
        got = await monitor.request_evidence(
            attempt_id, claim_id,
            applicable_to=_required(pool, step, claim_id, evidence),
            provisional_ok=True if step.checker_step else None,
        )
        evidence[claim_id] = got
        values[claim_id] = copy.deepcopy(got.value)
        if got.ref not in account.consumed:
            account.consumed.append(got.ref)

    try:
        for claim_id in step.reads:
            if stop_requested():
                raise CancelledAttempt()
            await read(claim_id)
        for claim_id in step.late_reads:
            if stop_requested():
                raise CancelledAttempt()
            await read(claim_id)
        if stop_requested():
            raise CancelledAttempt()
        if step.modeled:
            record = await model_call(
                monitor, pool, attempt_id, pool.duration(step, number), step.tokens, cancellable=cancellable,
            )
            account.log_usage(record.billed_prompt, record.billed_completion, task=step.task_id)
        ctx = context()
        produced = step.compute(copy.deepcopy(values), ctx)
        if inspect.isawaitable(produced):
            produced = await produced
        produced, fired = monitor.faults.apply(node.task_id, attempt_id, produced)
        if fired:
            monitor.record("fault_fired", attempt_id, {"faults": fired})
        notices = monitor.notices.get(attempt_id, [])
        if notices:
            if pool.complies(attempt_id):
                pool.advisory_rereads += 1
                monitor.record("advisory_reread", attempt_id, {"claims": sorted({ref.claim_id for ref in notices})})
                account.consumed[:] = [ref for ref in account.consumed if ref.claim_id not in {n.claim_id for n in notices}]
                for ref in notices:
                    await read(ref.claim_id)
                again = await model_call(
                    monitor, pool, attempt_id, pool.duration(step, number) * 0.5, step.tokens, cancellable=cancellable,
                )
                account.log_usage(again.billed_prompt, again.billed_completion, task=step.task_id, reread=True)
                ctx = context()
                produced = step.compute(copy.deepcopy(values), ctx)
                if inspect.isawaitable(produced):
                    produced = await produced
                produced, _fired = monitor.faults.apply(node.task_id, attempt_id, produced)
                monitor.notices.pop(attempt_id, None)
            else:
                pool.advisory_ignored += 1
                monitor.record("advisory_ignored", attempt_id, {})
        if not cancellable and monitor.cancel_requested(attempt_id):
            pool.late_submissions += 1
        if stop_requested():
            raise CancelledAttempt()
        own: list = []
        for retry in range(4):
            refs = tuple(account.consumed)
            own = []
            stale_read = False
            for claim_id in step.writes:
                applies_to: tuple[ArtifactRef, ...] = ()
                source = step.applies.get(claim_id)
                if source is not None and source in evidence:
                    bound = tuple(evidence[item].artifact for item in step.context.get(claim_id, ()) if item in evidence)
                    applies_to = (evidence[source].artifact, *bound, *pool.case.identity)
                result = await monitor.submit(OutputProposed(
                    attempt_id=attempt_id,
                    claim_id=claim_id,
                    value=produced[claim_id],
                    evidence_versions=refs,
                    plan_version=attempt.plan_version,
                    applies_to=applies_to,
                ))
                if isinstance(result, dict) and result.get("reason") == "stale_read":
                    stale_read = True
                    break
                if isinstance(result, dict) and result.get("accepted"):
                    own.append(result["ref"])
            if not stale_read or retry == 3:
                break
            # Write-time validation failed: reread the inputs, redo the step, and write again.
            account.consumed.clear()
            for claim_id in step.all_reads:
                await read(claim_id)
            again = await model_call(
                monitor, pool, attempt_id, pool.duration(step, number) * 0.5, step.tokens, cancellable=cancellable,
            )
            account.log_usage(again.billed_prompt, again.billed_completion, task=step.task_id, revalidated=True)
            ctx = context()
            produced = step.compute(copy.deepcopy(values), ctx)
            if inspect.isawaitable(produced):
                produced = await produced
            produced, _fired = monitor.faults.apply(node.task_id, attempt_id, produced)
        refs = tuple(account.consumed)
        cited = (*refs, *own)
        for index, action in enumerate(step.actions):
            if stop_requested():
                raise CancelledAttempt()
            arguments = action.arguments({**values, **produced}, ctx)
            if arguments is None:
                continue
            preconditions = action.preconditions({**values, **produced}, ctx) if action.preconditions else {}
            await monitor.submit(ActionProposed(
                attempt_id=attempt_id,
                action_id=f"action:{attempt_id}:{index}",
                tool=action.tool,
                arguments=arguments,
                evidence_versions=cited,
                task_id=node.task_id,
                plan_version=attempt.plan_version,
                preconditions=preconditions,
            ))
        if stop_requested():
            raise CancelledAttempt()
        await monitor.submit(AttemptCompleted(attempt_id, refs, attempt.plan_version))
    except (Rejected, CancelledAttempt):
        await monitor.submit(CancellationAcknowledged(attempt_id, tuple(account.consumed)))


def make_corrector(monitor: MonitorRuntime, pool: AgentPool):
    """A corrector with the same source access as every agent. Its latency and tokens are modeled."""
    case = pool.case

    async def propose(claim_id: str) -> Any:
        if case.corrector is None:
            return None
        attempt_id = f"corrector:{claim_id}:{monitor.corrector_calls}"
        if case.correction_latency > 0:
            await model_call(monitor, pool, attempt_id, case.correction_latency, (500, 120), cancellable=True)

        def meter(prompt: int, completion: int, started: float, ended: float) -> None:
            record = RequestRecord(attempt_id, started, ended, prompt, completion, cancellable=False)
            record.completed = ended
            record.billed_prompt = prompt
            record.billed_completion = completion
            record.delivered = True
            monitor.requests.requests.append(record)

        pool.world.meter = meter
        values: dict[str, Any] = {}
        for item in case.steps:
            for read in (*item.all_reads, *item.writes):
                latest = monitor.latest_live_locked(read)
                if latest is not None:
                    values[read] = copy.deepcopy(latest.value)
        result = case.corrector(claim_id, pool.world, values)
        if inspect.isawaitable(result):
            result = await result
        return result

    return propose
