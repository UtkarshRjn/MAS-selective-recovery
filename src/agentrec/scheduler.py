"""Run ready plan nodes together, up to a fixed concurrency limit.

Each attempt receives a private copy of the evidence the monitor delivered.
Invalidation both sets the attempt's cooperative cancellation event and cancels
its asyncio task. Async model clients receive that cancellation at their active
request; late results remain subject to the monitor's eligibility check.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
from typing import Any, Awaitable, Callable

from .contract import AgentAttempt, TaskNode
from .fault_schedule import logical_task
from .forward_recovery import superstep_levels
from .monitor_runtime import CancelledAttempt, MonitorRuntime, Rejected
from .protocol import ActionProposed, AttemptCompleted, CancellationAcknowledged, OutputProposed

Runner = Callable[[MonitorRuntime, AgentAttempt, TaskNode], Awaitable[None]]


def isolated_snapshot(values: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(values)


class OverlapClock:
    """Counts overlapping model sections without sleeping for a fixed delay."""

    def __init__(self) -> None:
        self.active = 0
        self.ticks = 0
        self.calls = 0
        self.overlapped = False

    def enter(self) -> None:
        if self.active:
            self.overlapped = True
        self.active += 1
        self.calls += 1

    def leave(self) -> None:
        self.active -= 1
        if self.active == 0:
            self.ticks += 1


class Holds:
    """One-shot barriers. The first attempt of a named task waits; a reexecution does not."""

    def __init__(self, names: set[str]) -> None:
        self.entered = {name: asyncio.Event() for name in names}
        self.release = {name: asyncio.Event() for name in names}
        self.attempt_ids: dict[str, str] = {}
        self._used: set[str] = set()

    async def acquire(self, task_id: str, attempt_id: str) -> None:
        if task_id not in self.release or task_id in self._used:
            await asyncio.sleep(0)
            return
        self._used.add(task_id)
        self.attempt_ids[task_id] = attempt_id
        self.entered[task_id].set()
        await self.release[task_id].wait()


class Scheduler:
    def __init__(self, monitor: MonitorRuntime, runner: Runner, concurrency: int) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self.monitor = monitor
        self.runner = runner
        self.concurrency = concurrency
        self.launched: set[tuple[int, str]] = set()
        self.tasks: set[asyncio.Task] = set()
        self.inflight: dict[asyncio.Task, str] = {}
        self.monitor.attempt_canceller = self.cancel_attempt

    def cancel_attempt(self, attempt_id: str) -> None:
        """Preempt the coroutine for one invalidated attempt."""
        for task, running_attempt in tuple(self.inflight.items()):
            if running_attempt == attempt_id and not task.done():
                task.cancel()

    async def run(self) -> None:
        while True:
            self.monitor.progress_event.clear()
            await self._launch_ready()
            await asyncio.sleep(0)
            self._reap()
            async with self.monitor.lock:
                quiet = self.monitor.settled and not self.tasks
            if quiet:
                self.monitor.quiescent.set()
                return
            if not self.tasks:
                self.monitor.quiescent.set()
                await self.monitor.progress_event.wait()
                continue
            self.monitor.quiescent.clear()
            waiter = asyncio.create_task(self.monitor.progress_event.wait())
            done, _pending = await asyncio.wait(self.tasks | {waiter}, return_when=asyncio.FIRST_COMPLETED)
            if waiter not in done:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter
            self._reap()

    async def _launch_ready(self) -> None:
        async with self.monitor.lock:
            version = self.monitor.plan.version
            available = self.concurrency - self._uncancelled_locked()
            opened: list[tuple[AgentAttempt, TaskNode]] = []
            blocked_levels = self._barrier_locked()
            for node in self.monitor.plan.task_nodes:
                if node.task_id in blocked_levels:
                    continue
                key = (version, node.task_id)
                if key in self.launched:
                    continue
                if node.task_id in self.monitor.carried_forward and self._live_attempt_locked(node.task_id):
                    if self.monitor.task_output_ready(node.task_id):
                        self.launched.add(key)
                        continue
                    if self._running_attempt_locked(node.task_id):
                        continue
                    # The carried attempt finished without an accepted output (e.g. fenced as stale): run it again.
                predecessors = [src for src, dst in self.monitor.plan.dependency_edges if dst == node.task_id]
                if not all(self.monitor.task_output_ready(src) for src in predecessors):
                    continue
                if available <= 0:
                    break
                attempt = self.monitor._open_attempt_locked(node)
                self.launched.add(key)
                opened.append((attempt, node))
                available -= 1
        for attempt, node in opened:
            task = asyncio.create_task(self._run(attempt, node), name=attempt.attempt_id)
            self.tasks.add(task)
            self.inflight[task] = attempt.attempt_id

    def _uncancelled_locked(self) -> int:
        """Busy slots. Without cancellation an invalidated attempt keeps running and keeps its slot."""
        stopped = self.monitor.is_cancelled if self.monitor.config.cancellation else self.monitor.cancel_requested
        count = 0
        for task, attempt_id in self.inflight.items():
            if not task.done() and not stopped(attempt_id):
                count += 1
        return count

    def _barrier_locked(self) -> set[str]:
        """Tasks held by a superstep barrier: a level starts only after every earlier level verified."""
        if self.monitor.config.barrier != "superstep":
            return set()
        plan = self.monitor.plan
        by_level: dict[int, list[str]] = {}
        for task_id, level in superstep_levels(plan.task_nodes, plan.dependency_edges).items():
            by_level.setdefault(level, []).append(task_id)
        exempt = self.monitor.checker_tasks
        held: set[str] = set()
        closed = False
        for level in sorted(by_level):
            if closed:
                held.update(task_id for task_id in by_level[level] if logical_task(task_id) not in exempt)
            elif not all(self.monitor.task_output_verified(task_id) for task_id in by_level[level]):
                closed = True
        return held

    def _live_attempt_locked(self, task_id: str) -> bool:
        for attempt in self.monitor.graph.attempts.values():
            if attempt.task_id != task_id or attempt.attempt_id in self.monitor.graph.obsolete_attempts:
                continue
            if attempt.status.value in {"pending", "running", "completed"}:
                return True
        return False

    def _running_attempt_locked(self, task_id: str) -> bool:
        return any(
            attempt.task_id == task_id and attempt.status.value in {"pending", "running"}
            for attempt in self.monitor.graph.attempts.values()
        )

    def _reap(self) -> None:
        finished = {task for task in self.tasks if task.done()}
        for task in finished:
            self.tasks.discard(task)
            self.inflight.pop(task, None)
            if not task.cancelled():
                task.exception()

    async def _run(self, attempt: AgentAttempt, node: TaskNode) -> None:
        try:
            await self.runner(self.monitor, attempt, node)
        except asyncio.CancelledError:
            consumed = tuple(self.monitor.accounts[attempt.attempt_id].consumed)
            self.monitor._spawn(
                lambda: self.monitor.submit(CancellationAcknowledged(attempt.attempt_id, consumed))
            )
        except Exception as exc:
            async with self.monitor.lock:
                if self.monitor.unresolved is None:
                    self.monitor.unresolved = f"{node.task_id} failed: {exc}"
                    self.monitor.settled = True
            await self.monitor._notify(["progress"])


async def run_mock(
    monitor: MonitorRuntime,
    attempt: AgentAttempt,
    node: TaskNode,
    *,
    producers: dict[str, Callable[..., dict[str, Any]]],
    actions: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    clock: OverlapClock,
    holds: Holds,
) -> None:
    account = monitor.accounts[attempt.attempt_id]
    snapshot: dict[str, Any] = {}
    try:
        for claim_id in node.reads:
            if monitor.is_cancelled(attempt.attempt_id):
                raise CancelledAttempt()
            evidence = await monitor.request_evidence(attempt.attempt_id, claim_id)
            snapshot[claim_id] = evidence.value
            account.consumed.append(evidence.ref)
        if monitor.is_cancelled(attempt.attempt_id):
            raise CancelledAttempt()
        snapshot = isolated_snapshot(snapshot)
        clock.enter()
        try:
            account.log_model(snapshot)
            await holds.acquire(node.task_id, attempt.attempt_id)
        finally:
            clock.leave()
        produced = producers[node.agent_id](monitor, snapshot, attempt)
        if inspect.isawaitable(produced):
            produced = await produced
        for claim_id in node.writes:
            await monitor.submit(OutputProposed(
                attempt_id=attempt.attempt_id,
                claim_id=claim_id,
                value=produced[claim_id],
                evidence_versions=tuple(account.consumed),
                plan_version=attempt.plan_version,
            ))
        action_for = actions.get(node.task_id)
        if action_for is None and ":" in node.task_id:
            action_for = actions.get(node.task_id.rsplit(":", 1)[0])
        if action_for is not None and not monitor.is_cancelled(attempt.attempt_id):
            await monitor.submit(ActionProposed(
                attempt_id=attempt.attempt_id,
                action_id=f"action:{attempt.attempt_id}",
                tool="book_flight",
                arguments=action_for(snapshot),
                evidence_versions=tuple(account.consumed),
                task_id=node.task_id,
                plan_version=attempt.plan_version,
            ))
        if monitor.is_cancelled(attempt.attempt_id):
            await monitor.submit(CancellationAcknowledged(attempt.attempt_id, tuple(account.consumed)))
            return
        await monitor.submit(AttemptCompleted(
            attempt.attempt_id,
            tuple(account.consumed),
            attempt.plan_version,
        ))
    except (Rejected, CancelledAttempt):
        await monitor.submit(CancellationAcknowledged(attempt.attempt_id, tuple(account.consumed)))
