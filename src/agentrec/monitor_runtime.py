"""Central monitor for a speculative multi-agent run.

Agents send proposals and results through one queue. Workers apply those
messages. Evidence waits happen on the calling task, on a per-claim condition,
so a worker is never blocked on verification, a model call, or a condition.

The monitor lock covers dependency registration, invalidation, plan activation,
and action authorization. It is not held across those waits. After a condition
wakes, the predicate is checked again under the lock.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from .action_dispatcher import TransportError, dispatch_blocker, resource_key
from .contract import (
    TERMINAL_ATTEMPTS,
    AgentAttempt,
    ApprovalStatus,
    AttemptStatus,
    Evidence,
    EvidenceRef,
    EvidenceStatus,
    Plan,
    PlanStatus,
    ProposedAction,
    TaskNode,
    canonical,
)
from .forward_recovery import RecoveryDecision, decide_recovery, select_suspects
from .plan_manager import PlanError, apply_revision
from .protocol import (
    ActionProposed,
    AttemptCompleted,
    CancellationAcknowledged,
    OutputProposed,
    PlanRevisionProposed,
    VerificationCompleted,
    ensure_message,
)
from .provenance import ExecutionGraph
from .verification import verify_action, verify_claim


class Rejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CancelledAttempt(Exception):
    pass


class BarrierGate:
    """Test gate. An armed name waits until the test releases it."""

    def __init__(self) -> None:
        self._slots: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}

    def arm(self, name: str) -> tuple[asyncio.Event, asyncio.Event]:
        arrived = asyncio.Event()
        release = asyncio.Event()
        self._slots[name] = (arrived, release)
        return arrived, release

    def armed(self, name: str) -> bool:
        return name in self._slots

    async def arrive(self, name: str) -> None:
        slot = self._slots.get(name)
        if slot is None:
            return
        arrived, release = slot
        arrived.set()
        await release.wait()


@dataclass
class AttemptAccount:
    """Model-call log for one attempt. Concurrent attempts do not share it."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    consumed: list[EvidenceRef] = field(default_factory=list)

    def log_model(self, snapshot: dict[str, Any]) -> None:
        self.calls.append({"claims": sorted(snapshot)})
        self.prompt_tokens += 8
        self.completion_tokens += 4


@dataclass
class _Handled:
    value: Any
    notifies: list[str]
    followup: Callable[[], Awaitable[None]] | None = None


class MonitorRuntime:
    def __init__(
        self,
        plan: Plan,
        *,
        worker_count: int = 2,
        policy: str = "online",
        correction_budget: int = 2,
        truth: dict[str, Any] | None = None,
        corrections: dict[str, Any] | None = None,
        allow_provisional: bool = True,
        required_action: dict[str, Any] | None = None,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        self.graph = ExecutionGraph()
        self.plan = plan
        self.plans = {plan.version: plan}
        self.graph.mark_plan(plan.version, plan.task_ids())
        self.lock = asyncio.Lock()
        self.progress_event = asyncio.Event()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.conditions: dict[str, asyncio.Condition] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.resource_locks: dict[str, asyncio.Lock] = {}
        self.actions: dict[str, ProposedAction] = {}
        self.accounts: dict[str, AttemptAccount] = {}
        self.latest_attempt: dict[str, str] = {}
        self.carried_forward: set[str] = set()
        self.worker_count = worker_count
        self.policy = policy
        self.correction_budget = correction_budget
        self.corrections_used = 0
        self.truth = truth or {}
        self.corrections = corrections or {}
        self.allow_provisional = allow_provisional
        self.required_action = required_action or {"flight_id": "FL1"}
        self.faults_enabled = True
        self.fault_name = "restaurant_date"
        self.defer_input_verification = False
        self.graph_fault = ""
        self.proposer = None
        self.proposals_rejected = 0
        self.verification_gate: asyncio.Event | None = asyncio.Event()
        self.verification_blocked = asyncio.Event()
        self.gate = BarrierGate()
        self.transport: Callable[[ProposedAction], Awaitable[Any]] = _default_transport
        self.verifier_id = "monitor:verifier"
        self.recovery_id = "monitor:recovery"
        self._counts: dict[str, int] = {}
        self._workers: list[asyncio.Task] = []
        self._background: set[asyncio.Task] = set()
        self.final_answer: dict[str, Any] | None = None
        self.unresolved: str | None = None
        self.settled = False
        self.recovery_applied = False
        self.verification_cost = 0
        self.verification_waits = 0
        self.dependency_waits = 0
        self.action_blocks = 0
        self.stale_rejected = 0
        self.dispatched: list[str] = []
        self.transport_calls: list[str] = []
        self._seed_monitor_attempts()

    def _seed_monitor_attempts(self) -> None:
        for attempt_id, agent_id in ((self.verifier_id, "verifier"), (self.recovery_id, "recovery")):
            attempt = AgentAttempt(agent_id, "monitor", attempt_id, self.plan.version, status=AttemptStatus.RUNNING)
            self.graph.add_attempt(attempt)

    def _cond(self, key: str) -> asyncio.Condition:
        cond = self.conditions.get(key)
        if cond is None:
            cond = asyncio.Condition()
            self.conditions[key] = cond
        return cond

    def _cancel_event(self, attempt_id: str) -> asyncio.Event:
        event = self.cancel_events.get(attempt_id)
        if event is None:
            event = asyncio.Event()
            self.cancel_events[attempt_id] = event
        return event

    def is_cancelled(self, attempt_id: str) -> bool:
        event = self.cancel_events.get(attempt_id)
        if event is not None and event.is_set():
            return True
        attempt = self.graph.attempts.get(attempt_id)
        return attempt is not None and attempt.status in TERMINAL_ATTEMPTS

    async def start(self) -> None:
        for _ in range(self.worker_count):
            self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
        for _ in self._workers:
            await self.queue.put(None)
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)

    async def submit(self, message: object) -> Any:
        ensure_message(message)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self.queue.put((message, future))
        return await future

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                message, future = item
                try:
                    value = await self._dispatch(message)
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
                else:
                    if not future.done():
                        future.set_result(value)
            finally:
                self.queue.task_done()

    async def _dispatch(self, message: object) -> Any:
        handled = await self._handle(message)
        await self._notify(handled.notifies)
        if handled.followup is not None:
            self._spawn(handled.followup)
        return handled.value

    async def _guard(self, followup: Callable[[], Awaitable[None]]) -> None:
        try:
            await followup()
        except Exception as exc:
            async with self.lock:
                if self.unresolved is None:
                    self.unresolved = f"monitor error: {exc}"
                    self.settled = True
            await self._notify(["progress"])

    def _spawn(self, followup: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(self._guard(followup))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _notify(self, keys: list[str]) -> None:
        if "progress" in keys:
            self.progress_event.set()
        for key in dict.fromkeys(keys):
            cond = self._cond(key)
            async with cond:
                cond.notify_all()

    async def _handle(self, message: object) -> _Handled:
        if isinstance(message, OutputProposed):
            return await self._handle_output(message)
        if isinstance(message, ActionProposed):
            return await self._handle_action(message)
        if isinstance(message, PlanRevisionProposed):
            return await self._handle_revision(message)
        if isinstance(message, VerificationCompleted):
            return await self._handle_verification(message)
        if isinstance(message, AttemptCompleted):
            return await self._handle_completed(message)
        if isinstance(message, CancellationAcknowledged):
            return await self._handle_cancel(message)
        raise Rejected("unsupported message")

    def _eligible_locked(self, attempt_id: str) -> bool:
        if attempt_id in {self.verifier_id, self.recovery_id}:
            return True
        attempt = self.graph.attempts.get(attempt_id)
        if attempt is None or attempt.status in TERMINAL_ATTEMPTS:
            return False
        if attempt.attempt_id in self.graph.obsolete_attempts:
            return False
        if attempt.plan_version != self.plan.version and attempt.task_id not in self.carried_forward:
            return False
        return True

    def _ensure_eligible_locked(self, attempt_id: str) -> None:
        if not self._eligible_locked(attempt_id):
            raise Rejected("attempt_ineligible")

    def _allowed_locked(self, claim_id: str, *, verified_only: bool) -> Evidence | None:
        versions = [
            item for item in self.graph.versions(claim_id)
            if item.verification_status != EvidenceStatus.INVALID
        ]
        if verified_only:
            versions = [item for item in versions if item.verification_status == EvidenceStatus.VERIFIED]
        if not versions:
            return None
        return max(versions, key=lambda item: item.version)

    def _copy(self, evidence: Evidence) -> Evidence:
        return replace(evidence, value=copy.deepcopy(evidence.value))

    def _next_version_locked(self, claim_id: str) -> int:
        versions = self.graph.versions(claim_id)
        return 1 if not versions else max(item.version for item in versions) + 1

    async def open_attempt(self, node: TaskNode) -> AgentAttempt:
        async with self.lock:
            return self._open_attempt_locked(node)

    def _open_attempt_locked(self, node: TaskNode) -> AgentAttempt:
        self._counts[node.task_id] = self._counts.get(node.task_id, 0) + 1
        attempt = AgentAttempt(
            agent_id=node.agent_id,
            task_id=node.task_id,
            attempt_id=f"{node.task_id}:attempt:{self._counts[node.task_id]}",
            plan_version=self.plan.version,
            status=AttemptStatus.RUNNING,
        )
        self.graph.add_attempt(attempt)
        self.accounts[attempt.attempt_id] = AttemptAccount()
        self.latest_attempt[node.task_id] = attempt.attempt_id
        self._cancel_event(attempt.attempt_id)
        self.graph.record("attempt_opened", attempt.attempt_id, {"task_id": node.task_id, "plan": self.plan.version})
        return attempt

    async def request_evidence(self, attempt_id: str, claim_id: str) -> Evidence:
        """Register the consumer before copying the value out of the monitor."""
        gate_name = f"register:{attempt_id}:{claim_id}"
        if self.gate.armed(gate_name):
            await self.gate.arrive(gate_name)
        cond = self._cond(f"evidence:{claim_id}")
        async with cond:
            while True:
                async with self.lock:
                    self._ensure_eligible_locked(attempt_id)
                    evidence = self._allowed_locked(claim_id, verified_only=not self.allow_provisional)
                    if evidence is not None:
                        self.graph.register_consumption(attempt_id, evidence.ref)
                        return self._copy(evidence)
                    provisional = self._allowed_locked(claim_id, verified_only=False)
                    if provisional is not None and not self.allow_provisional:
                        self.verification_waits += 1
                    else:
                        self.dependency_waits += 1
                await cond.wait()

    async def _handle_output(self, message: OutputProposed) -> _Handled:
        async with self.lock:
            if not self._eligible_locked(message.attempt_id):
                self.stale_rejected += 1
                self.graph.record("output_rejected", message.attempt_id, {"claim_id": message.claim_id})
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            consumed = self.graph.attempts[message.attempt_id].consumed_evidence_versions
            for ref in message.evidence_versions:
                evidence = self.graph.get(ref)
                if evidence is None or evidence.verification_status == EvidenceStatus.INVALID or ref not in consumed:
                    self.stale_rejected += 1
                    self.graph.record("output_rejected", message.attempt_id, {"claim_id": message.claim_id})
                    return _Handled({"accepted": False, "reason": "stale_evidence"}, ["progress"])
            evidence = Evidence(
                claim_id=message.claim_id,
                version=self._next_version_locked(message.claim_id),
                value=copy.deepcopy(message.value),
                producer_attempt=message.attempt_id,
                supporting_evidence=message.evidence_versions,
            )
            self.graph.add_evidence(evidence)
            for ref in message.evidence_versions:
                self.graph.link_support(ref, message.attempt_id, evidence.ref)
            return _Handled({"accepted": True, "ref": evidence.ref}, [f"evidence:{message.claim_id}", "progress"])

    async def _handle_action(self, message: ActionProposed) -> _Handled:
        async with self.lock:
            if not self._eligible_locked(message.attempt_id):
                self.stale_rejected += 1
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            if message.task_id not in self.plan.task_ids():
                return _Handled({"accepted": False, "reason": "not_in_active_plan"}, ["progress"])
            action = ProposedAction(
                action_id=message.action_id,
                tool=message.tool,
                arguments=copy.deepcopy(message.arguments),
                evidence_versions=message.evidence_versions,
                proposing_attempt=message.attempt_id,
                task_id=message.task_id,
            )
            self.actions[action.action_id] = action
            self.graph.record("action_proposed", message.attempt_id, {"action_id": action.action_id})
            action_id = action.action_id
        self._spawn(lambda: self._watch_action(action_id))
        return _Handled({"accepted": True, "action_id": action_id}, ["progress"])

    async def _watch_action(self, action_id: str) -> None:
        cond = self._cond(f"action:{action_id}")
        async with cond:
            while True:
                async with self.lock:
                    action = self.actions.get(action_id)
                    if action is None or action.approval_status in {
                        ApprovalStatus.REJECTED, ApprovalStatus.DISPATCHED, ApprovalStatus.TRANSPORT_FAILED,
                    }:
                        return
                    found = [self.graph.get(ref) for ref in action.evidence_versions]
                    ineligible = (
                        not self._eligible_locked(action.proposing_attempt)
                        or action.task_id not in self.plan.task_ids()
                        or any(item is None or item.verification_status == EvidenceStatus.INVALID for item in found)
                    )
                    if ineligible:
                        action.approval_status = ApprovalStatus.REJECTED
                        return
                    if all(item is not None and item.verification_status == EvidenceStatus.VERIFIED for item in found):
                        snapshot = [self._copy(item) for item in found if item is not None]
                        tool = action.tool
                        arguments = copy.deepcopy(action.arguments)
                        break
                self.action_blocks += 1
                await cond.wait()
        verdict = verify_action(tool, arguments, snapshot, self.required_action)
        if not verdict.ok or verdict.approved_arguments is None:
            async with self.lock:
                self.actions[action_id].approval_status = ApprovalStatus.REJECTED
                self.graph.record("action_rejected", self.actions[action_id].proposing_attempt, {"reason": verdict.reason})
            return
        await self.authorize_and_dispatch(action_id, canonical(verdict.approved_arguments))

    async def authorize_and_dispatch(self, action_id: str, approved_canonical: str) -> dict[str, Any]:
        """Record the dispatch under the lock, then call the tool outside it."""
        if self.gate.armed("approve"):
            await self.gate.arrive("approve")
        async with self.lock:
            action = self.actions[action_id]
            evidence = [item for ref in action.evidence_versions if (item := self.graph.get(ref)) is not None]
            blocker = dispatch_blocker(
                action,
                evidence,
                attempt_eligible=self._eligible_locked(action.proposing_attempt),
                task_in_plan=action.task_id in self.plan.task_ids(),
                approved_canonical=approved_canonical,
            )
            if blocker is not None:
                action.approval_status = ApprovalStatus.REJECTED
                self.graph.record("action_rejected", action.proposing_attempt, {"reason": blocker})
                return {"dispatched": False, "reason": blocker}
            action.approval_status = ApprovalStatus.DISPATCHED
            action.approved_canonical = approved_canonical
            self.dispatched.append(action_id)
            self.graph.record("action_dispatched", action.proposing_attempt, {"action_id": action_id})
            resource = self.resource_locks.setdefault(resource_key(action.tool, action.arguments), asyncio.Lock())
            delivered = copy.deepcopy(action)
        try:
            async with resource:
                self.transport_calls.append(action_id)
                result = await self.transport(delivered)
        except TransportError as exc:
            async with self.lock:
                failed = self.actions[action_id]
                failed.approval_status = ApprovalStatus.TRANSPORT_FAILED
                failed.transport_error = str(exc)
                self.graph.record("transport_failed", failed.proposing_attempt, {"action_id": action_id, "error": str(exc)})
            await self._notify(["progress"])
            return {"dispatched": True, "transport": "failed"}
        async with self.lock:
            self.actions[action_id].result = result
            self._maybe_release_locked()
        await self._notify(["progress"])
        return {"dispatched": True, "transport": "ok"}

    def _maybe_release_locked(self) -> None:
        if self.unresolved or self.final_answer is not None or not self.dispatched:
            return
        flight = self._latest_verified_locked("claim:flight_id")
        plan = self._latest_verified_locked("claim:plan")
        if flight is None or plan is None:
            return
        action = self.actions[self.dispatched[-1]]
        if action.approval_status != ApprovalStatus.DISPATCHED or action.result is None:
            return
        if flight.value.get("flight_id") != action.arguments.get("flight_id"):
            return
        if plan.value.get("flight_id") != action.arguments.get("flight_id"):
            return
        bad_date = self.truth.get("bad_date")
        if bad_date and bad_date in str(plan.value.get("itinerary", "")):
            return
        self.final_answer = {
            "flight_id": flight.value.get("flight_id"),
            "itinerary": plan.value.get("itinerary"),
            "action_id": action.action_id,
        }
        self.settled = True
        self.graph.record("final_released", self.recovery_id, {"action_id": action.action_id})

    def _latest_verified_locked(self, claim_id: str) -> Evidence | None:
        live = [
            item for item in self.graph.versions(claim_id)
            if item.verification_status == EvidenceStatus.VERIFIED
            and item.producer_attempt not in self.graph.obsolete_attempts
        ]
        if not live:
            return None
        return max(live, key=lambda item: item.version)

    async def _handle_completed(self, message: AttemptCompleted) -> _Handled:
        async with self.lock:
            if not self._eligible_locked(message.attempt_id):
                self.stale_rejected += 1
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            attempt = self.graph.attempts[message.attempt_id]
            attempt.status = AttemptStatus.COMPLETED
            refs = tuple(self.graph.produced_by.get(message.attempt_id, ()))
            self.graph.record("attempt_completed", message.attempt_id, {"outputs": len(refs)})
        return _Handled({"accepted": True}, ["progress"], lambda: self._verify_outputs(message.attempt_id, refs))

    async def _verify_outputs(self, attempt_id: str, refs: tuple[EvidenceRef, ...]) -> None:
        async with self.lock:
            attempt = self.graph.attempts.get(attempt_id)
            task_id = None if attempt is None else attempt.task_id
            wait_for_gate = (
                self.verification_gate is not None
                and task_id == "research_restaurant"
                and not self.verification_gate.is_set()
            )
        if wait_for_gate and self.verification_gate is not None:
            self.verification_blocked.set()
            await self.verification_gate.wait()
        if self.defer_input_verification and not await self._inputs_settled(attempt_id):
            return
        if self.policy == "coarse":
            await self._verify_coarse(attempt_id, refs)
            return
        for ref in refs:
            await self._submit_verdict(attempt_id, ref)

    async def _inputs_settled(self, attempt_id: str) -> bool:
        """Wait until each consumed claim is verified, or stop when one is invalid."""
        async with self.lock:
            attempt = self.graph.attempts.get(attempt_id)
            if attempt is None:
                return False
            consumed = tuple(attempt.consumed_evidence_versions)
        for ref in consumed:
            cond = self._cond(f"evidence:{ref.claim_id}")
            async with cond:
                while True:
                    async with self.lock:
                        if attempt_id in self.graph.obsolete_attempts:
                            return False
                        evidence = self.graph.get(ref)
                        if evidence is None or evidence.verification_status == EvidenceStatus.INVALID:
                            return False
                        if evidence.verification_status == EvidenceStatus.VERIFIED:
                            break
                    await asyncio.wait_for(cond.wait(), 5)
        return True

    async def _verify_coarse(self, attempt_id: str, refs: tuple[EvidenceRef, ...]) -> None:
        """Judge every output, then discard the producer's provisional siblings together."""
        judged: list[tuple[EvidenceRef, object, int]] = []
        for ref in refs:
            async with self.lock:
                evidence = self.graph.get(ref)
                if evidence is None or evidence.verification_status != EvidenceStatus.PROVISIONAL:
                    continue
                snapshot = self._copy(evidence)
                plan_version = self.plan.version
            judged.append((ref, verify_claim(snapshot, self.truth), plan_version))
        failed = [item for item in judged if not item[1].ok]
        if not failed:
            for ref, _verdict, _plan_version in judged:
                await self._submit_verdict(attempt_id, ref)
            return
        async with self.lock:
            self.verification_cost += len(judged)
        ref, verdict, plan_version = failed[0]
        await self.submit(VerificationCompleted(
            attempt_id=self.verifier_id,
            evidence_versions=tuple(item[0] for item in judged),
            target_attempt=attempt_id,
            target_evidence=(ref,),
            ok=False,
            reason=verdict.reason,
            suspect_claims=tuple(item[0] for item in judged),
            ambiguous=False,
            plan_version=plan_version,
        ))

    async def _submit_verdict(self, attempt_id: str, ref: EvidenceRef) -> None:
        async with self.lock:
            evidence = self.graph.get(ref)
            if evidence is None or evidence.verification_status == EvidenceStatus.INVALID:
                return
            snapshot = self._copy(evidence)
            plan_version = self.plan.version
            self.verification_cost += 1
        verdict = verify_claim(snapshot, self.truth)
        await self.submit(VerificationCompleted(
            attempt_id=self.verifier_id,
            evidence_versions=(ref,),
            target_attempt=attempt_id,
            target_evidence=(ref,),
            ok=verdict.ok,
            reason=verdict.reason,
            suspect_claims=verdict.suspect_claims,
            ambiguous=verdict.ambiguous,
            plan_version=plan_version,
        ))

    async def _handle_verification(self, message: VerificationCompleted) -> _Handled:
        async with self.lock:
            if self.unresolved and not message.ok:
                return _Handled({"applied": False, "reason": "unresolved"}, ["progress"])
            if not self._verdict_applicable_locked(message):
                return _Handled({"applied": False, "reason": "stale_verdict"}, ["progress"])
            if message.ok:
                for ref in message.target_evidence:
                    self.graph.set_status(ref, EvidenceStatus.VERIFIED)
                notifies = [f"evidence:{ref.claim_id}" for ref in message.target_evidence]
                notifies.extend(self._action_keys_locked(message.target_evidence))
                notifies.append("progress")
                self._maybe_release_locked()
                return _Handled({"applied": True, "ok": True}, notifies)
            roots = self._suspects_locked(message)
            affected = self.graph.invalidate(roots)
            self._signal_locked(affected.attempts)
            self.faults_enabled = False
            claim_ids: list[str] = []
            for ref in roots:
                if ref.claim_id not in claim_ids:
                    claim_ids.append(ref.claim_id)
            view = (
                tuple(claim_ids),
                self.correction_budget - self.corrections_used,
                self.plan.version,
                self.plan.required_goals,
                self.plan.task_nodes,
                self.plan.dependency_edges,
                tuple(roots),
            )
            notifies = [f"evidence:{ref.claim_id}" for ref in affected.evidence]
            notifies.extend(f"action:{action_id}" for action_id in self.actions)
            notifies.append("progress")
            if self.proposer is not None and self.policy == "online":
                return _Handled(
                    {"applied": True, "ok": False},
                    notifies,
                    lambda: self._propose_and_recover(view, notifies),
                )
            decision = self._decision_locked(view, self.corrections)
            return self._finish_recovery_locked(decision, notifies)

    def _decision_locked(self, view: tuple, corrections: dict[str, Any]) -> RecoveryDecision:
        claim_ids, budget, version, goals, tasks, edges, roots = view
        return decide_recovery(
            policy=self.policy,
            budget_remaining=budget,
            roots=roots,
            corrections=corrections,
            base_version=version,
            goals=goals,
            tasks=tasks,
            edges=edges,
            evidence_versions=(),
            truth=self.truth,
        )

    def _finish_recovery_locked(self, decision: RecoveryDecision, notifies: list[str]) -> _Handled:
        if decision.rejected:
            self.proposals_rejected += 1
            self.graph.record("correction_rejected", self.recovery_id, {
                "accepted": [claim_id for claim_id, _value in decision.publish],
            })
        if decision.unresolved:
            if decision.rejected:
                self.corrections_used += 1
                self.unresolved = "correction rejected"
            else:
                self.unresolved = "correction budget exhausted"
            self.settled = True
            self.recovery_applied = True
            return _Handled({"applied": True, "unresolved": True}, notifies)
        if decision.restart:
            self._restart_locked()
            self.corrections_used += 1
            self.recovery_applied = True
            return _Handled({"applied": True, "restart": True}, notifies)
        self.corrections_used += 1
        return _Handled({"applied": True, "ok": False}, notifies, lambda: self._recover(decision))

    async def _propose_and_recover(self, view: tuple, notifies: list[str]) -> None:
        """Ask the repair model outside the lock, then accept or reject its value."""
        proposer = self.proposer
        proposals = dict(self.corrections)
        if proposer is not None:
            for claim_id in view[0]:
                proposals[claim_id] = await asyncio.to_thread(proposer, claim_id)
        decision = self._decision_locked(view, proposals)
        async with self.lock:
            if self.unresolved is not None:
                return
            handled = self._finish_recovery_locked(decision, notifies)
        if handled.followup is not None:
            await handled.followup()
        else:
            await self._notify(handled.notifies)

    def _suspects_locked(self, message: VerificationCompleted) -> tuple[EvidenceRef, ...]:
        attempt = self.graph.attempts[message.target_attempt]

        def status_of(ref: EvidenceRef) -> EvidenceStatus:
            evidence = self.graph.get(ref)
            if evidence is None:
                return EvidenceStatus.INVALID
            return evidence.verification_status

        return select_suspects(
            supplied=message.suspect_claims,
            ambiguous=message.ambiguous,
            consumed=attempt.consumed_evidence_versions,
            produced=message.target_evidence,
            status_of=status_of,
        )

    def _verdict_applicable_locked(self, message: VerificationCompleted) -> bool:
        attempt = self.graph.attempts.get(message.target_attempt)
        if attempt is None or attempt.status in TERMINAL_ATTEMPTS or attempt.attempt_id in self.graph.obsolete_attempts:
            return False
        if message.plan_version != self.plan.version and attempt.task_id not in self.carried_forward:
            return False
        for ref in message.target_evidence:
            evidence = self.graph.get(ref)
            if evidence is None or evidence.verification_status == EvidenceStatus.INVALID:
                return False
        return True

    async def invalidate_refs(self, refs: list[EvidenceRef]) -> None:
        """Invalidate under the monitor lock. Tests arm `invalidate` to order this against registration."""
        if self.gate.armed("invalidate"):
            await self.gate.arrive("invalidate")
        async with self.lock:
            affected = self.graph.invalidate(refs)
            self._signal_locked(affected.attempts)
            keys = [f"evidence:{ref.claim_id}" for ref in affected.evidence]
            keys.extend(f"action:{action_id}" for action_id in self.actions)
            keys.append("progress")
        await self._notify(keys)

    def _signal_locked(self, attempt_ids: tuple[str, ...] | list[str]) -> None:
        for attempt_id in attempt_ids:
            if attempt_id in {self.verifier_id, self.recovery_id}:
                continue
            self._cancel_event(attempt_id).set()

    def _restart_locked(self) -> None:
        self.carried_forward = set()
        self.faults_enabled = False
        for attempt in list(self.graph.attempts.values()):
            if attempt.attempt_id in {self.verifier_id, self.recovery_id}:
                continue
            if attempt.status == AttemptStatus.RUNNING:
                attempt.status = AttemptStatus.CANCELLED
                self.graph.obsolete_attempts.add(attempt.attempt_id)
                self._cancel_event(attempt.attempt_id).set()
            elif attempt.status in {AttemptStatus.COMPLETED, AttemptStatus.INVALIDATED, AttemptStatus.PENDING}:
                self.graph.mark_obsolete(attempt.attempt_id)
                self._cancel_event(attempt.attempt_id).set()
        for evidence in list(self.graph.evidence.values()):
            if evidence.verification_status == EvidenceStatus.PROVISIONAL:
                self.graph.set_status(evidence.ref, EvidenceStatus.INVALID)
        previous = self.plan
        revised = Plan(
            version=previous.version + 1,
            task_nodes=previous.task_nodes,
            dependency_edges=previous.dependency_edges,
            required_goals=previous.required_goals,
        )
        self.plans[previous.version] = replace(previous, status=PlanStatus.OBSOLETE)
        self.plans[revised.version] = revised
        self.plan = revised
        self.graph.mark_plan(revised.version, revised.task_ids())

    async def _recover(self, decision: RecoveryDecision) -> None:
        published: list[EvidenceRef] = []
        for claim_id, value in decision.publish:
            published.append(await self.publish_correction(claim_id, value))
        async with self.lock:
            closed = self.unresolved is not None
        if closed or decision.revision is None:
            async with self.lock:
                self.recovery_applied = True
            await self._notify(["progress"])
            return
        revision = replace(decision.revision, evidence_versions=tuple(published))
        await self.submit(revision)
        async with self.lock:
            self.recovery_applied = True
        await self._notify(["progress"])

    async def publish_correction(self, claim_id: str, value: Any) -> EvidenceRef:
        async with self.lock:
            self._counts[claim_id] = self._counts.get(claim_id, 0) + 1
            attempt = AgentAttempt(
                agent_id="corrector",
                task_id=f"correct:{claim_id}",
                attempt_id=f"correct:{claim_id}:attempt:{self._counts[claim_id]}",
                plan_version=self.plan.version,
                status=AttemptStatus.COMPLETED,
            )
            self.graph.add_attempt(attempt)
            self.accounts[attempt.attempt_id] = AttemptAccount()
            evidence = Evidence(
                claim_id=claim_id,
                version=self._next_version_locked(claim_id),
                value=copy.deepcopy(value),
                producer_attempt=attempt.attempt_id,
                supporting_evidence=(),
            )
            self.graph.add_evidence(evidence)
            ref = evidence.ref
            self.graph.record("correction_published", attempt.attempt_id, {
                "claim_id": claim_id,
                "version": ref.version,
            })
        await self._notify([f"evidence:{claim_id}", "progress"])
        await self._submit_verdict(attempt.attempt_id, ref)
        return ref

    async def _handle_revision(self, message: PlanRevisionProposed) -> _Handled:
        if self.gate.armed("revise"):
            await self.gate.arrive("revise")
        async with self.lock:
            privileged = message.attempt_id in {self.recovery_id, self.verifier_id}
            if not privileged and not self._eligible_locked(message.attempt_id):
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            for ref in message.evidence_versions:
                evidence = self.graph.get(ref)
                if evidence is None or evidence.verification_status == EvidenceStatus.INVALID:
                    return _Handled({"accepted": False, "reason": "stale_evidence"}, ["progress"])
            try:
                revised = apply_revision(self.plan, message)
            except PlanError as exc:
                return _Handled({"accepted": False, "reason": str(exc)}, ["progress"])
            self._switch_plan_locked(revised, message)
            notifies = ["progress", *(f"action:{action_id}" for action_id in self.actions)]
            return _Handled({"accepted": True, "version": revised.version}, notifies)

    def _switch_plan_locked(self, revised: Plan, message: PlanRevisionProposed) -> None:
        previous = self.plan
        self.plans[previous.version] = replace(previous, status=PlanStatus.OBSOLETE)
        self.plans[revised.version] = revised
        self.plan = revised
        self.carried_forward = set(message.carry_forward)
        relaunch = set(message.remove_tasks) & revised.task_ids()
        removed = set(message.remove_tasks) - revised.task_ids()
        for attempt in list(self.graph.attempts.values()):
            if attempt.attempt_id in {self.verifier_id, self.recovery_id}:
                continue
            if attempt.task_id in self.carried_forward:
                continue
            drop = attempt.task_id in removed or (
                attempt.task_id in relaunch and attempt.plan_version == previous.version
            )
            if drop:
                self.graph.mark_obsolete(attempt.attempt_id)
                self._cancel_event(attempt.attempt_id).set()
        self.graph.mark_plan(revised.version, revised.task_ids())
        self.graph.record("plan_revised", message.attempt_id, {
            "version": revised.version,
            "tasks": [node.task_id for node in revised.task_nodes],
            "carry_forward": list(message.carry_forward),
        })

    async def _handle_cancel(self, message: CancellationAcknowledged) -> _Handled:
        async with self.lock:
            attempt = self.graph.attempts.get(message.attempt_id)
            if attempt is not None and attempt.status == AttemptStatus.RUNNING:
                attempt.status = AttemptStatus.CANCELLED
                self.graph.obsolete_attempts.add(attempt.attempt_id)
            self._cancel_event(message.attempt_id).set()
            self.graph.record("cancellation_acknowledged", message.attempt_id, {})
            return _Handled({"accepted": True}, ["progress"])

    def _action_keys_locked(self, refs: tuple[EvidenceRef, ...]) -> list[str]:
        cited = {(ref.claim_id, ref.version) for ref in refs}
        return [
            f"action:{action.action_id}"
            for action in self.actions.values()
            if any((ref.claim_id, ref.version) in cited for ref in action.evidence_versions)
        ]

    def task_output_ready(self, task_id: str) -> bool:
        """Call while holding the monitor lock. A carried task is ready once its writes exist."""
        node = self.plan.node(task_id)
        if node is None:
            return False
        if not node.writes:
            return any(
                attempt.task_id == task_id
                and attempt.status == AttemptStatus.COMPLETED
                and attempt.attempt_id not in self.graph.obsolete_attempts
                for attempt in self.graph.attempts.values()
            )
        return all(self._allowed_locked(claim_id, verified_only=False) is not None for claim_id in node.writes)

    async def wait_until(self, predicate: Callable[["MonitorRuntime"], bool], timeout: float = 5) -> None:
        cond = self._cond("progress")
        async with cond:
            while True:
                async with self.lock:
                    if predicate(self):
                        return
                await asyncio.wait_for(cond.wait(), timeout)


async def _default_transport(action: ProposedAction) -> dict[str, Any]:
    return {"ok": True, "action_id": action.action_id}
