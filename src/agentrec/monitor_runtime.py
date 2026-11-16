"""Central monitor for a speculative multi-agent run.

Agents send proposals and results through one queue. Workers apply those
messages. Evidence waits happen on the calling task, on a per-claim condition,
so a worker is never blocked on verification, a model call, or a condition.

The monitor lock covers dependency registration, invalidation, plan activation,
and action authorization. It is not held across those waits. After a condition
wakes, the predicate is checked again under the lock.

Recovery rounds are serialized by a separate lock. A round reads a snapshot of
the active plan and the failure epoch, asks for corrections outside both
locks, and applies its decision only if neither changed; otherwise it rebases
on the current plan and the union of open repair obligations. Several faults
can therefore be open at once, and no action is released while an open
obligation's cone reaches its support.

Policy switches (`PolicyConfig`) decide whether failures spread, whether late
work is fenced, whether running attempts are preempted, and whether effects
wait behind the gate. The legacy policy names keep their original behavior.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from .action_contracts import ContractContext, ContractRegistry, ToolGateway, default_registry
from .action_dispatcher import TransportError, dispatch_blocker
from .contract import (
    DEAD_STATUSES,
    TERMINAL_ATTEMPTS,
    AgentAttempt,
    ApprovalStatus,
    ArtifactRef,
    AttemptStatus,
    Evidence,
    EvidenceRef,
    EvidenceStatus,
    Plan,
    PlanStatus,
    ProposedAction,
    TaskNode,
    applies,
    canonical,
)
from .effects import ConflictError, EffectLedger, EffectPhase, UncertainOutcome, idempotency_key
from .fault_schedule import FaultSchedule
from .forward_recovery import RecoveryDecision, decide_recovery, select_suspects, tainted_claims
from .instrumentation import RequestLog, TimedLock
from .plan_manager import PlanError, apply_revision
from .policies import PolicyConfig, legacy_config
from .protocol import (
    ActionProposed,
    AttemptCompleted,
    CancellationAcknowledged,
    OutputProposed,
    PlanRevisionProposed,
    VerificationCompleted,
    ensure_message,
)
from .provenance import ExecutionGraph, Invalidation
from .verification import Verdict
from .verifiers import CheckContext, GlobalInvariant, OracleVerifier, TRUST_MODELS


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

    def log_usage(self, prompt: int, completion: int, **detail: Any) -> None:
        self.calls.append({"prompt": prompt, "completion": completion, **detail})
        self.prompt_tokens += prompt
        self.completion_tokens += completion


@dataclass
class Obligation:
    """A failed claim that recovery still owes a live, verified replacement for."""

    claim_id: str
    failed_version: int
    opened_at: float
    handled: bool = False
    discharged_at: float | None = None
    reason: str = ""

    @property
    def open(self) -> bool:
        return self.discharged_at is None


@dataclass(frozen=True)
class RecoveryView:
    claims: tuple[str, ...]
    roots: tuple[EvidenceRef, ...]
    budget: int
    version: int
    epoch: int
    goals: tuple[str, ...]
    tasks: tuple[TaskNode, ...]
    edges: tuple[tuple[str, str], ...]


@dataclass
class _Handled:
    value: Any
    notifies: list[str]
    followup: Callable[[], Awaitable[Any]] | None = None


MONITOR_ATTEMPTS = ("monitor:verifier", "monitor:recovery")


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
        config: PolicyConfig | None = None,
        checker: Any = None,
        contracts: ContractRegistry | None = None,
        gateway: ToolGateway | None = None,
        faults: FaultSchedule | None = None,
        trust_model: str = "local_commit",
        invariants: tuple[GlobalInvariant, ...] = (),
        settle_timeout: float | None = 5,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        if trust_model not in TRUST_MODELS:
            raise ValueError(f"unknown trust model {trust_model}")
        self.config = config if config is not None else legacy_config(policy)
        self.graph = ExecutionGraph()
        self.plan = plan
        self.plans = {plan.version: plan}
        self.graph.mark_plan(plan.version, plan.task_ids())
        self.lock = TimedLock()
        self.recovery_lock = asyncio.Lock()
        self.progress_event = asyncio.Event()
        self.progress_seq = 0
        self.quiescent = asyncio.Event()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.max_queue_depth = 0
        self.conditions: dict[str, asyncio.Condition] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.attempt_canceller: Callable[[str], None] | None = None
        self.resource_locks: dict[str, asyncio.Lock] = {}
        self.actions: dict[str, ProposedAction] = {}
        self.accounts: dict[str, AttemptAccount] = {}
        self.requests = RequestLog()
        self.latest_attempt: dict[str, str] = {}
        self.carried_forward: set[str] = set()
        self.worker_count = worker_count
        self.policy = self.config.recovery
        self.correction_budget = correction_budget
        self.corrections_used = 0
        self.truth = truth or {}
        self.corrections = corrections or {}
        self.allow_provisional = allow_provisional if config is None else self.config.consume_provisional
        self.required_action = required_action or {"flight_id": "FL1"}
        self.checker = checker if checker is not None else OracleVerifier(self.truth)
        self.check_tools: Any = None
        self.contracts = contracts if contracts is not None else default_registry()
        self.gateway = gateway
        self.reconciler: Callable[[str], dict[str, Any] | None] | None = None
        self.ledger = EffectLedger()
        self.faults = faults if faults is not None else FaultSchedule()
        self.legacy_fault_switch = False
        self.faults_enabled = True
        self.fault_name = "restaurant_date"
        self.trust_model = trust_model
        self.invariants = tuple(invariants)
        self.locally_checked: set[tuple[str, int]] = set()
        self.invariant_checked: set[tuple] = set()
        self.settle_timeout = settle_timeout
        self.defer_input_verification = False
        self.concurrent_checks = False
        self.graph_fault = ""
        self.proposer = None
        self.proposals_rejected = 0
        self.proposal_values: dict[str, Any] = {}
        self.proposal_cache: dict[tuple[str, int], Any] = {}
        self.corrector_calls = 0
        self.verification_gate: asyncio.Event | None = asyncio.Event()
        self.verification_blocked = asyncio.Event()
        self.verdict_hold: Callable[["MonitorRuntime", Evidence], Awaitable[None]] | None = None
        self.gate = BarrierGate()
        self.transport: Callable[[ProposedAction], Awaitable[Any]] = _default_transport
        self.verifier_id, self.recovery_id = MONITOR_ATTEMPTS
        self._counts: dict[str, int] = {}
        self._workers: list[asyncio.Task] = []
        self._background: set[asyncio.Task] = set()
        self.release_rule: Callable[["MonitorRuntime"], dict[str, Any] | None] | None = None
        self.final_answer: dict[str, Any] | None = None
        self.unresolved: str | None = None
        self.settled = False
        self.recovery_applied = False
        self.obligations: dict[str, Obligation] = {}
        self.graph_epoch = 0
        self.max_rebases = 8
        self.rebases = 0
        self.notices: dict[str, list[EvidenceRef]] = {}
        self.regretted: list[str] = []
        self.verification_cost = 0
        self.verification_waits = 0
        self.dependency_waits = 0
        self.action_blocks = 0
        self.stale_rejected = 0
        self.stale_accepted = 0
        self.read_validation_rejects = 0
        self.dispatched: list[str] = []
        self.transport_calls: list[str] = []
        self._seed_monitor_attempts()

    # ------------------------------------------------------------------ basics

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

    def cancel_requested(self, attempt_id: str) -> bool:
        """True only when the monitor actually asked this attempt to stop."""
        event = self.cancel_events.get(attempt_id)
        return event is not None and event.is_set()

    def record(self, event_type: str, attempt_id: str, payload: dict[str, Any]) -> None:
        self.graph.record(event_type, attempt_id, payload)

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

    async def abort(self) -> None:
        """Cancel background followups and stop workers. Used when a harness gives up on a run."""
        for task in list(self._background):
            task.cancel()
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
        self.max_queue_depth = max(self.max_queue_depth, self.queue.qsize())
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

    async def _guard(self, followup: Callable[[], Awaitable[Any]]) -> None:
        try:
            await followup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.lock:
                if self.unresolved is None:
                    self.unresolved = f"monitor error: {exc!r}"
                    self.settled = True
                    self.record("monitor_error", self.recovery_id, {"error": repr(exc)})
            await self._notify(["progress"])

    def _spawn(self, followup: Callable[[], Awaitable[Any]]) -> None:
        task = asyncio.create_task(self._guard(followup))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _notify(self, keys: list[str]) -> None:
        if "progress" in keys:
            self.progress_seq += 1
            self.progress_event.set()
        for key in dict.fromkeys(keys):
            cond = self._cond(key)
            async with cond:
                cond.notify_all()

    async def _wait_progress_after(self, seen: int, timeout: float | None = None) -> None:
        cond = self._cond("progress")
        async with cond:
            while self.progress_seq == seen:
                if timeout is None:
                    await cond.wait()
                else:
                    await asyncio.wait_for(cond.wait(), timeout)

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

    # ------------------------------------------------------------- eligibility

    def _eligible_locked(self, attempt_id: str) -> bool:
        if attempt_id in MONITOR_ATTEMPTS:
            return True
        attempt = self.graph.attempts.get(attempt_id)
        if attempt is None:
            return False
        if not self.config.fencing:
            return True
        if attempt.status in TERMINAL_ATTEMPTS:
            return False
        if attempt.attempt_id in self.graph.obsolete_attempts:
            return False
        if attempt.plan_version != self.plan.version and attempt.task_id not in self.carried_forward:
            return False
        return True

    def _ensure_eligible_locked(self, attempt_id: str) -> None:
        if not self._eligible_locked(attempt_id):
            raise Rejected("attempt_ineligible")

    def _allowed_locked(
        self,
        claim_id: str,
        *,
        verified_only: bool,
        applicable_to: tuple[ArtifactRef, ...] = (),
    ) -> Evidence | None:
        versions = [item for item in self.graph.versions(claim_id) if item.verification_status not in DEAD_STATUSES]
        if verified_only:
            versions = [item for item in versions if item.verification_status == EvidenceStatus.VERIFIED]
        if applicable_to and self.config.enforce_applicability:
            versions = [item for item in versions if applies(item.applies_to, applicable_to)]
        if not versions:
            return None
        return max(versions, key=lambda item: item.version)

    def latest_live_locked(self, claim_id: str) -> Evidence | None:
        return self._allowed_locked(claim_id, verified_only=False)

    def _copy(self, evidence: Evidence) -> Evidence:
        return replace(evidence, value=copy.deepcopy(evidence.value))

    def _next_version_locked(self, claim_id: str) -> int:
        versions = self.graph.versions(claim_id)
        return 1 if not versions else max(item.version for item in versions) + 1

    def _dead_in_support_locked(self, refs: tuple[EvidenceRef, ...] | list[EvidenceRef]) -> bool:
        for claim_id, version in self.graph.support_closure(refs):
            evidence = self.graph.get(EvidenceRef(claim_id, version))
            if evidence is None or evidence.verification_status in DEAD_STATUSES:
                return True
        return False

    def _obligation_reaches_locked(self, refs: tuple[EvidenceRef, ...] | list[EvidenceRef]) -> str | None:
        """Name an open obligation whose cone reaches this support, if any."""
        open_claims = [item.claim_id for item in self.obligations.values() if item.open]
        if not open_claims:
            return None
        support = {claim_id for claim_id, _version in self.graph.support_closure(refs)}
        for claim_id in open_claims:
            cone = tainted_claims(self.plan.task_nodes, (claim_id,))
            if support & cone:
                return claim_id
        return None

    # ---------------------------------------------------------------- attempts

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

    async def request_evidence(
        self,
        attempt_id: str,
        claim_id: str,
        *,
        applicable_to: tuple[ArtifactRef, ...] = (),
    ) -> Evidence:
        """Register the consumer before copying the value out of the monitor."""
        gate_name = f"register:{attempt_id}:{claim_id}"
        if self.gate.armed(gate_name):
            await self.gate.arrive(gate_name)
        cond = self._cond(f"evidence:{claim_id}")
        async with cond:
            while True:
                async with self.lock:
                    self._ensure_eligible_locked(attempt_id)
                    evidence = self._allowed_locked(
                        claim_id, verified_only=not self.allow_provisional, applicable_to=applicable_to,
                    )
                    if evidence is not None:
                        self.graph.register_consumption(attempt_id, evidence.ref)
                        return self._copy(evidence)
                    provisional = self._allowed_locked(claim_id, verified_only=False, applicable_to=applicable_to)
                    if provisional is not None and not self.allow_provisional:
                        self.verification_waits += 1
                    else:
                        self.dependency_waits += 1
                await cond.wait()

    # ----------------------------------------------------------------- outputs

    async def _handle_output(self, message: OutputProposed) -> _Handled:
        async with self.lock:
            self.record("result_arrived", message.attempt_id, {"claim_id": message.claim_id})
            if not self._eligible_locked(message.attempt_id):
                self.stale_rejected += 1
                self.graph.record("output_rejected", message.attempt_id, {"claim_id": message.claim_id, "reason": "attempt_ineligible"})
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            attempt = self.graph.attempts[message.attempt_id]
            consumed = attempt.consumed_evidence_versions
            stale = False
            for ref in message.evidence_versions:
                evidence = self.graph.get(ref)
                dead = evidence is None or evidence.verification_status in DEAD_STATUSES
                if dead or ref not in consumed:
                    if self.config.fencing:
                        self.stale_rejected += 1
                        self.graph.record("output_rejected", message.attempt_id, {"claim_id": message.claim_id, "reason": "stale_evidence"})
                        return _Handled({"accepted": False, "reason": "stale_evidence"}, ["progress"])
                    stale = True
            if self.config.read_version_validation:
                for ref in message.evidence_versions:
                    latest = self.latest_live_locked(ref.claim_id)
                    if latest is None or latest.version != ref.version:
                        self.read_validation_rejects += 1
                        self.graph.record("output_rejected", message.attempt_id, {"claim_id": message.claim_id, "reason": "stale_read"})
                        return _Handled({"accepted": False, "reason": "stale_read"}, ["progress"])
            if attempt.attempt_id in self.graph.obsolete_attempts or attempt.status in TERMINAL_ATTEMPTS:
                stale = True
            evidence = Evidence(
                claim_id=message.claim_id,
                version=self._next_version_locked(message.claim_id),
                value=copy.deepcopy(message.value),
                producer_attempt=message.attempt_id,
                supporting_evidence=message.evidence_versions,
                applies_to=message.applies_to,
            )
            self.graph.add_evidence(evidence)
            for ref in message.evidence_versions:
                self.graph.link_support(ref, message.attempt_id, evidence.ref)
            if stale:
                self.stale_accepted += 1
                self.graph.record("stale_output_accepted", message.attempt_id, {"claim_id": message.claim_id, "version": evidence.version})
            self.graph.record("output_accepted", message.attempt_id, {"claim_id": message.claim_id, "version": evidence.version})
            return _Handled({"accepted": True, "ref": evidence.ref}, [f"evidence:{message.claim_id}", "progress"])

    # ----------------------------------------------------------------- actions

    async def _handle_action(self, message: ActionProposed) -> _Handled:
        async with self.lock:
            if not self._eligible_locked(message.attempt_id):
                self.stale_rejected += 1
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            if self.config.fencing and message.task_id not in self.plan.task_ids():
                return _Handled({"accepted": False, "reason": "not_in_active_plan"}, ["progress"])
            action = ProposedAction(
                action_id=message.action_id,
                tool=message.tool,
                arguments=copy.deepcopy(message.arguments),
                evidence_versions=message.evidence_versions,
                proposing_attempt=message.attempt_id,
                task_id=message.task_id,
                preconditions=dict(message.preconditions),
            )
            self.actions[action.action_id] = action
            self.graph.record("action_proposed", message.attempt_id, {"action_id": action.action_id, "tool": action.tool})
            self._ledger(EffectPhase.PROPOSED, action)
            action_id = action.action_id
        if self.config.action_gate:
            self._spawn(lambda: self._watch_action(action_id))
        else:
            self._spawn(lambda: self.authorize_and_dispatch(action_id, "", gated=False))
        return _Handled({"accepted": True, "action_id": action_id}, ["progress"])

    def _ledger(self, phase: EffectPhase, action: ProposedAction, **detail: Any) -> None:
        self.ledger.append(
            phase,
            action,
            semantic_key=self.contracts.semantic_key(action.tool, action.arguments),
            resource=self.contracts.resource(action.tool, action.arguments),
            detail=detail,
        )

    def _contract_context(self) -> ContractContext:
        return ContractContext(
            required=dict(self.required_action),
            enforce_applicability=self.config.enforce_applicability,
            versions=lambda claim_id: [self._copy(item) for item in self.graph.versions(claim_id)],
        )

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
                        or any(item is None or item.verification_status in DEAD_STATUSES for item in found)
                        or self._dead_in_support_locked(action.evidence_versions)
                    )
                    if ineligible:
                        action.approval_status = ApprovalStatus.REJECTED
                        self._ledger(EffectPhase.BLOCKED, action, reason="ineligible_or_stale_support")
                        self.graph.record("action_rejected", action.proposing_attempt, {"action_id": action_id, "reason": "ineligible"})
                        return
                    waiting_on = self._obligation_reaches_locked(action.evidence_versions)
                    if waiting_on is None and all(
                        item is not None and item.verification_status == EvidenceStatus.VERIFIED for item in found
                    ):
                        snapshot = [self._copy(item) for item in found if item is not None]
                        tool = action.tool
                        arguments = copy.deepcopy(action.arguments)
                        ctx = self._contract_context()
                        break
                self.action_blocks += 1
                await cond.wait()
        verdict = self.contracts.validate(tool, arguments, snapshot, ctx)
        if not verdict.ok or verdict.approved_arguments is None:
            async with self.lock:
                rejected = self.actions[action_id]
                rejected.approval_status = ApprovalStatus.REJECTED
                self._ledger(EffectPhase.BLOCKED, rejected, reason=verdict.reason)
                self.graph.record("action_rejected", rejected.proposing_attempt, {"action_id": action_id, "reason": verdict.reason})
            await self._notify(["progress"])
            return
        await self.authorize_and_dispatch(action_id, canonical(verdict.approved_arguments))

    async def authorize_and_dispatch(
        self,
        action_id: str,
        approved_canonical: str,
        *,
        gated: bool = True,
    ) -> dict[str, Any]:
        """Record the dispatch under the lock, then call the tool outside it."""
        if self.gate.armed("approve"):
            await self.gate.arrive("approve")
        async with self.lock:
            action = self.actions[action_id]
            evidence = [item for ref in action.evidence_versions if (item := self.graph.get(ref)) is not None]
            if gated:
                blocker = dispatch_blocker(
                    action,
                    evidence,
                    attempt_eligible=self._eligible_locked(action.proposing_attempt),
                    task_in_plan=action.task_id in self.plan.task_ids(),
                    approved_canonical=approved_canonical,
                )
                if blocker is None and self._dead_in_support_locked(action.evidence_versions):
                    blocker = "stale_support"
                if blocker is None and self._obligation_reaches_locked(action.evidence_versions):
                    blocker = "recovery_in_progress"
            else:
                blocker = None
                if action.approval_status in {ApprovalStatus.DISPATCHED, ApprovalStatus.TRANSPORT_FAILED}:
                    blocker = "already_dispatched"
                elif self.config.fencing and not self._eligible_locked(action.proposing_attempt):
                    blocker = "attempt_ineligible"
            if blocker is None and self.config.read_version_validation:
                for ref in action.evidence_versions:
                    latest = self.latest_live_locked(ref.claim_id)
                    if latest is None or latest.version != ref.version:
                        blocker = "stale_read"
                        self.read_validation_rejects += 1
                        break
            if blocker is not None:
                action.approval_status = ApprovalStatus.REJECTED
                self._ledger(EffectPhase.BLOCKED, action, reason=blocker)
                self.graph.record("action_rejected", action.proposing_attempt, {"action_id": action_id, "reason": blocker})
                rejected = {"dispatched": False, "reason": blocker}
            else:
                rejected = None
                action.approval_status = ApprovalStatus.DISPATCHED
                action.approved_canonical = approved_canonical or action.canonical_arguments
                self.dispatched.append(action_id)
                self.graph.record("action_authorized", action.proposing_attempt, {"action_id": action_id, "gated": gated})
                self._ledger(EffectPhase.AUTHORIZED, action, gated=gated)
                self.graph.record("action_dispatched", action.proposing_attempt, {"action_id": action_id})
                self._ledger(EffectPhase.DISPATCHED, action)
                semantic = self.contracts.semantic_key(action.tool, action.arguments)
                mode = self.config.effect_consistency
                if mode == "per_call_lock":
                    lock_key = f"{action.tool}:{action.canonical_arguments}"
                elif mode == "resource_lock":
                    lock_key = self.contracts.resource(action.tool, action.arguments)
                else:
                    lock_key = ""
                resource = self.resource_locks.setdefault(lock_key, asyncio.Lock()) if lock_key else None
                delivered = copy.deepcopy(action)
        if rejected is not None:
            await self._notify(["progress", f"action:{action_id}"])
            return rejected
        return await self._deliver(action_id, delivered, semantic, resource)

    async def _deliver(
        self,
        action_id: str,
        delivered: ProposedAction,
        semantic: str,
        resource: asyncio.Lock | None,
    ) -> dict[str, Any]:
        key = idempotency_key(semantic) if self.config.reconcile == "reconcile" else ""
        preconditions = dict(delivered.preconditions) if self.config.effect_consistency == "cas" else {}
        attempts = 0
        while True:
            attempts += 1
            try:
                if resource is not None:
                    async with resource:
                        result = await self._call(delivered, key, preconditions)
                else:
                    result = await self._call(delivered, key, preconditions)
                break
            except ConflictError as exc:
                async with self.lock:
                    failed = self.actions[action_id]
                    failed.approval_status = ApprovalStatus.TRANSPORT_FAILED
                    failed.transport_error = str(exc)
                    self._ledger(EffectPhase.CONFLICT, failed, error=str(exc))
                    self.graph.record("transport_failed", failed.proposing_attempt, {"action_id": action_id, "error": str(exc), "conflict": True})
                await self._notify(["progress", f"action:{action_id}"])
                return {"dispatched": True, "transport": "conflict"}
            except UncertainOutcome as exc:
                async with self.lock:
                    self._ledger(EffectPhase.UNCERTAIN, self.actions[action_id], error=str(exc))
                    self.graph.record("ack_lost", self.actions[action_id].proposing_attempt, {"action_id": action_id})
                policy = self.config.reconcile
                if policy == "reconcile" and self.reconciler is not None:
                    found = self.reconciler(key)
                    async with self.lock:
                        self._ledger(EffectPhase.RECONCILED, self.actions[action_id], committed=found is not None)
                    if found is not None:
                        result = found
                        break
                if policy in {"reconcile", "naive_retry"} and attempts < 3:
                    async with self.lock:
                        self._ledger(EffectPhase.RETRIED, self.actions[action_id], attempt=attempts + 1)
                    continue
                async with self.lock:
                    failed = self.actions[action_id]
                    failed.approval_status = ApprovalStatus.TRANSPORT_FAILED
                    failed.transport_error = "outcome uncertain"
                await self._notify(["progress", f"action:{action_id}"])
                return {"dispatched": True, "transport": "uncertain"}
            except TransportError as exc:
                async with self.lock:
                    failed = self.actions[action_id]
                    failed.approval_status = ApprovalStatus.TRANSPORT_FAILED
                    failed.transport_error = str(exc)
                    self._ledger(EffectPhase.FAILED, failed, error=str(exc))
                    self.graph.record("transport_failed", failed.proposing_attempt, {"action_id": action_id, "error": str(exc)})
                await self._notify(["progress", f"action:{action_id}"])
                return {"dispatched": True, "transport": "failed"}
        async with self.lock:
            done = self.actions[action_id]
            done.result = result
            self._ledger(EffectPhase.COMMITTED, done, result=result if isinstance(result, dict) else str(result))
            self.graph.record("remote_commit", done.proposing_attempt, {"action_id": action_id})
            self._ledger(EffectPhase.ACKNOWLEDGED, done)
            self.graph.record("action_acknowledged", done.proposing_attempt, {"action_id": action_id})
            self._maybe_release_locked()
        await self._notify(["progress", f"action:{action_id}"])
        return {"dispatched": True, "transport": "ok"}

    async def _call(self, action: ProposedAction, key: str, preconditions: dict[str, int]) -> Any:
        self.transport_calls.append(action.action_id)
        if self.gateway is not None:
            return await self.gateway.execute(action, idempotency_key=key, preconditions=preconditions)
        return await self.transport(action)

    def _maybe_release_locked(self) -> None:
        if self.release_rule is not None:
            if self.unresolved or self.final_answer is not None:
                return
            answer = self.release_rule(self)
            if answer is not None:
                self.final_answer = answer
                self.settled = True
                self.graph.record("final_released", self.recovery_id, {})
            return
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

    # ------------------------------------------------------------ verification

    async def _handle_completed(self, message: AttemptCompleted) -> _Handled:
        async with self.lock:
            if not self._eligible_locked(message.attempt_id):
                self.stale_rejected += 1
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            attempt = self.graph.attempts[message.attempt_id]
            if attempt.status == AttemptStatus.RUNNING:
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
        if self.policy == "coarse":
            if self.defer_input_verification and not await self._inputs_settled(attempt_id):
                return
            await self._verify_coarse(attempt_id, refs)
            return
        if self.concurrent_checks:
            await asyncio.gather(*(self._verify_one(attempt_id, ref) for ref in refs))
            return
        if self.defer_input_verification and not await self._inputs_settled(attempt_id):
            return
        for ref in refs:
            await self._submit_verdict(attempt_id, ref)

    async def _verify_one(self, attempt_id: str, ref: EvidenceRef) -> None:
        if self.defer_input_verification and not await self._inputs_settled(attempt_id, ref):
            return
        await self._submit_verdict(attempt_id, ref)

    async def _inputs_settled(self, attempt_id: str, produced: EvidenceRef | None = None) -> bool:
        """Wait until each consumed claim is verified, or stop when one is dead.

        With `produced`, only the inputs that output actually cites are awaited.
        """
        async with self.lock:
            attempt = self.graph.attempts.get(attempt_id)
            if attempt is None:
                return False
            consumed = tuple(attempt.consumed_evidence_versions)
            if produced is not None:
                evidence = self.graph.get(produced)
                if evidence is not None:
                    consumed = tuple(evidence.supporting_evidence)
        for ref in consumed:
            cond = self._cond(f"evidence:{ref.claim_id}")
            async with cond:
                while True:
                    async with self.lock:
                        if self.config.fencing and attempt_id in self.graph.obsolete_attempts:
                            return False
                        evidence = self.graph.get(ref)
                        if evidence is None or evidence.verification_status in DEAD_STATUSES:
                            return False
                        if evidence.verification_status == EvidenceStatus.VERIFIED:
                            break
                    if self.settle_timeout is None:
                        await cond.wait()
                    else:
                        await asyncio.wait_for(cond.wait(), self.settle_timeout)
        return True

    def _check_context(self) -> CheckContext:
        return CheckContext(
            versions=lambda claim_id: [self._copy(item) for item in self.graph.versions(claim_id)],
            enforce_applicability=self.config.enforce_applicability,
            tools=self.check_tools,
        )

    async def _run_check(self, evidence: Evidence) -> Verdict:
        verdict = self.checker.check(evidence, self._check_context())
        if inspect.isawaitable(verdict):
            verdict = await verdict
        return verdict

    async def _check(self, ref: EvidenceRef) -> tuple[Verdict, int] | None:
        """Run the checker on one version, waiting while it reports a pending dependency."""
        async with self.lock:
            evidence = self.graph.get(ref)
            if evidence is None or evidence.verification_status in DEAD_STATUSES:
                return None
            snapshot = self._copy(evidence)
            self.verification_cost += 1
            self.record("check_started", self.verifier_id, {"claim_id": ref.claim_id, "version": ref.version})
        if self.verdict_hold is not None:
            await self.verdict_hold(self, snapshot)
        while True:
            seen = self.progress_seq
            verdict = await self._run_check(snapshot)
            if not verdict.pending:
                break
            async with self.lock:
                self.record("check_pending", self.verifier_id, {"claim_id": ref.claim_id, "waiting_for": list(verdict.wait_for)})
            await self._wait_progress_after(seen)
            async with self.lock:
                current = self.graph.get(ref)
                if current is None or current.verification_status in DEAD_STATUSES:
                    return None
        async with self.lock:
            plan_version = self.plan.version
            self.record("verdict", self.verifier_id, {
                "claim_id": ref.claim_id,
                "version": ref.version,
                "ok": verdict.ok,
                "reason": verdict.reason,
            })
        if not verdict.ok and self.config.timing == "posthoc":
            await self.quiescent.wait()
        return verdict, plan_version

    async def _verify_coarse(self, attempt_id: str, refs: tuple[EvidenceRef, ...]) -> None:
        """Judge every output, then discard the producer's provisional siblings together."""
        judged: list[tuple[EvidenceRef, Verdict, int]] = []
        for ref in refs:
            async with self.lock:
                evidence = self.graph.get(ref)
                if evidence is None or evidence.verification_status != EvidenceStatus.PROVISIONAL:
                    continue
            outcome = await self._check(ref)
            if outcome is not None:
                judged.append((ref, outcome[0], outcome[1]))
        failed = [item for item in judged if not item[1].ok]
        if not failed:
            for ref, verdict, plan_version in judged:
                await self._submit_known(attempt_id, ref, verdict, plan_version)
            return
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
        outcome = await self._check(ref)
        if outcome is None:
            return
        await self._submit_known(attempt_id, ref, outcome[0], outcome[1])

    async def _submit_known(self, attempt_id: str, ref: EvidenceRef, verdict: Verdict, plan_version: int) -> None:
        await self.submit(VerificationCompleted(
            attempt_id=self.verifier_id,
            evidence_versions=(ref,),
            target_attempt=attempt_id,
            target_evidence=(ref,),
            ok=verdict.ok,
            reason=verdict.reason,
            suspect_claims=verdict.suspect_claims if not verdict.ok else (),
            ambiguous=verdict.ambiguous if not verdict.ok else False,
            plan_version=plan_version,
        ))

    def _defer_commit_locked(self, ref: EvidenceRef) -> bool:
        return self.trust_model == "global_settle" and any(ref.claim_id in item.claims for item in self.invariants)

    def _discharge_locked(self, ref: EvidenceRef) -> None:
        obligation = self.obligations.get(ref.claim_id)
        if obligation is None or not obligation.open or ref.version <= obligation.failed_version:
            return
        evidence = self.graph.get(ref)
        if evidence is None or evidence.verification_status != EvidenceStatus.VERIFIED:
            return
        obligation.discharged_at = self.graph.clock()[0] - (self.graph.origin or (0.0, 0.0))[0]
        self.record("obligation_discharged", self.recovery_id, {"claim_id": ref.claim_id, "version": ref.version})

    async def _handle_verification(self, message: VerificationCompleted) -> _Handled:
        async with self.lock:
            if self.unresolved and not message.ok:
                return _Handled({"applied": False, "reason": "unresolved"}, ["progress"])
            if not self._verdict_applicable_locked(message):
                return _Handled({"applied": False, "reason": "stale_verdict"}, ["progress"])
            if message.ok:
                for ref in message.target_evidence:
                    if self._defer_commit_locked(ref):
                        self.locally_checked.add((ref.claim_id, ref.version))
                        self.record("locally_checked", self.verifier_id, {"claim_id": ref.claim_id, "version": ref.version})
                    else:
                        self.graph.set_status(ref, EvidenceStatus.VERIFIED)
                        self._discharge_locked(ref)
                notifies = [f"evidence:{ref.claim_id}" for ref in message.target_evidence]
                notifies.extend(self._action_keys_locked(message.target_evidence))
                notifies.append("progress")
                self._maybe_release_locked()
                followup = self._settle_invariants if self.invariants else None
                return _Handled({"applied": True, "ok": True}, notifies, followup)
            roots = self._suspects_locked(message)
            return self._fail_locked(roots, message.reason)

    def _fail_locked(self, roots: tuple[EvidenceRef, ...], reason: str, *, revoke: bool = False) -> _Handled:
        """Apply a failure: spread or mark it, open obligations, and schedule a recovery round."""
        if self.config.invalidation:
            affected = self.graph.invalidate(roots, revoke=revoke)
            self._signal_locked(affected.attempts)
        else:
            for ref in roots:
                if revoke:
                    self.graph.invalidate([ref], revoke=True)
                else:
                    self.graph.mark_dead(ref)
            affected = Invalidation(tuple(roots), ())
            if self.config.advisory:
                for ref in roots:
                    for attempt_id in sorted(self.graph.consumers.get((ref.claim_id, ref.version), ())):
                        self.notices.setdefault(attempt_id, []).append(ref)
                        self.record("advisory_notice", attempt_id, {"claim_id": ref.claim_id, "version": ref.version})
        self.graph_epoch += 1
        claim_ids = sorted({ref.claim_id for ref in roots})
        detected = self.faults.detected(claim_ids)
        if detected:
            self.record("fault_detected", self.recovery_id, {"faults": detected})
        if self.legacy_fault_switch:
            self.faults_enabled = False
        now = self.graph.clock()[0] - (self.graph.origin or (0.0, 0.0))[0]
        for ref in roots:
            current = self.obligations.get(ref.claim_id)
            if current is None or not current.open or ref.version > current.failed_version:
                self.obligations[ref.claim_id] = Obligation(ref.claim_id, ref.version, now, reason=reason)
                self.record("obligation_opened", self.recovery_id, {"claim_id": ref.claim_id, "version": ref.version})
        self._regret_effects_locked()
        notifies = [f"evidence:{ref.claim_id}" for ref in affected.evidence]
        notifies.extend(f"action:{action_id}" for action_id in self.actions)
        notifies.append("progress")
        return _Handled({"applied": True, "ok": False}, notifies, self._run_recovery)

    def _regret_effects_locked(self) -> None:
        """Flag dispatched effects whose support died after dispatch; compensate when allowed."""
        flagged = {record.action_id for record in self.ledger.phase(EffectPhase.SUPPORT_INVALIDATED)}
        for action in self.actions.values():
            if action.approval_status != ApprovalStatus.DISPATCHED or action.action_id in flagged:
                continue
            if action.proposing_attempt == self.recovery_id:
                continue
            if not self._dead_in_support_locked(action.evidence_versions):
                continue
            self.regretted.append(action.action_id)
            self._ledger(EffectPhase.SUPPORT_INVALIDATED, action, irreversible=self.contracts.irreversible(action.tool))
            contract = self.contracts.get(action.tool)
            if self.config.compensate and contract is not None and contract.compensate is not None:
                tool, arguments = contract.compensate(dict(action.arguments))
                self._spawn(lambda action=action, tool=tool, arguments=arguments: self._compensate(action, tool, arguments))

    async def _compensate(self, original: ProposedAction, tool: str, arguments: dict[str, Any]) -> None:
        compensation = ProposedAction(
            action_id=f"compensate:{original.action_id}",
            tool=tool,
            arguments=arguments,
            evidence_versions=(),
            proposing_attempt=self.recovery_id,
            task_id="compensation",
        )
        async with self.lock:
            self.actions[compensation.action_id] = compensation
            compensation.approval_status = ApprovalStatus.DISPATCHED
            self._ledger(EffectPhase.DISPATCHED, compensation, compensates=original.action_id)
        try:
            result = await self._call(compensation, idempotency_key(f"comp:{original.action_id}"), {})
        except TransportError as exc:
            async with self.lock:
                self._ledger(EffectPhase.FAILED, compensation, error=str(exc))
            return
        async with self.lock:
            compensation.result = result
            self._ledger(EffectPhase.COMPENSATED, compensation, compensates=original.action_id)

    # ---------------------------------------------------------------- recovery

    async def _run_recovery(self) -> None:
        """One serialized recovery round over every unhandled obligation, rebased when stale."""
        async with self.recovery_lock:
            for _round in range(self.max_rebases):
                async with self.lock:
                    if self.unresolved is not None:
                        return
                    pending = [item for item in self.obligations.values() if item.open and not item.handled]
                    if not pending:
                        return
                    view = RecoveryView(
                        claims=tuple(item.claim_id for item in pending),
                        roots=tuple(EvidenceRef(item.claim_id, item.failed_version) for item in pending),
                        budget=self.correction_budget - self.corrections_used,
                        version=self.plan.version,
                        epoch=self.graph_epoch,
                        goals=self.plan.required_goals,
                        tasks=self.plan.task_nodes,
                        edges=self.plan.dependency_edges,
                    )
                proposals, screened = await self._proposals(view)
                async with self.lock:
                    if self.unresolved is not None:
                        return
                    if self.plan.version != view.version or self.graph_epoch != view.epoch:
                        self.rebases += 1
                        self.record("recovery_rebased", self.recovery_id, {
                            "stale_plan": view.version,
                            "stale_epoch": view.epoch,
                            "plan": self.plan.version,
                            "epoch": self.graph_epoch,
                        })
                        continue
                    decision = self._decide_locked(view, proposals, screened)
                    for claim_id in view.claims:
                        self.obligations[claim_id].handled = True
                    handled = self._finish_recovery_locked(decision, [])
                accepted = True
                if handled.followup is not None:
                    accepted = await handled.followup()
                else:
                    await self._notify(handled.notifies or ["progress"])
                if accepted is False:
                    async with self.lock:
                        for claim_id in view.claims:
                            if claim_id in self.obligations and self.obligations[claim_id].open:
                                self.obligations[claim_id].handled = False
                        self.rebases += 1
                        self.record("recovery_rebased", self.recovery_id, {"reason": "revision_rejected"})
                    continue
                return
            async with self.lock:
                if self.unresolved is None:
                    self.unresolved = "recovery did not settle on a stable plan"
                    self.settled = True
            await self._notify(["progress"])

    async def _proposals(self, view: RecoveryView) -> tuple[dict[str, Any], dict[str, bool] | None]:
        """Corrections for the view's claims, and the runtime checker's verdict on each."""
        if not self.config.correction:
            return {}, None
        proposals = {claim_id: self.corrections[claim_id] for claim_id in view.claims if claim_id in self.corrections}
        proposer = self.proposer
        if proposer is not None:
            for ref in view.roots:
                key = (ref.claim_id, ref.version)
                if key not in self.proposal_cache:
                    async with self.lock:
                        self.record("correction_requested", self.recovery_id, {"claim_id": ref.claim_id})
                    self.corrector_calls += 1
                    if inspect.iscoroutinefunction(proposer):
                        value = await proposer(ref.claim_id)
                    else:
                        value = await asyncio.to_thread(proposer, ref.claim_id)
                    self.proposal_cache[key] = value
                proposals[ref.claim_id] = self.proposal_cache[key]
        self.proposal_values.update(proposals)
        if isinstance(self.checker, OracleVerifier):
            return proposals, None
        screened: dict[str, bool] = {}
        for claim_id, value in proposals.items():
            probe = Evidence(claim_id, 1, copy.deepcopy(value), "proposal", ())
            verdict = await self._run_check(probe)
            while verdict.pending:
                seen = self.progress_seq
                await self._wait_progress_after(seen)
                verdict = await self._run_check(probe)
            screened[claim_id] = verdict.ok
        return proposals, screened

    def _decide_locked(
        self,
        view: RecoveryView,
        proposals: dict[str, Any],
        screened: dict[str, bool] | None,
    ) -> RecoveryDecision:
        screen = None if screened is None else (lambda claim_id, _value: screened.get(claim_id, False))
        return decide_recovery(
            policy=self.policy,
            budget_remaining=view.budget,
            roots=view.roots,
            corrections=proposals,
            base_version=view.version,
            goals=view.goals,
            tasks=view.tasks,
            edges=view.edges,
            evidence_versions=(),
            truth=self.truth if screened is None else None,
            screen=screen,
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
            self.record("recovery_unresolved", self.recovery_id, {"reason": self.unresolved})
            return _Handled({"applied": True, "unresolved": True}, [*notifies, "progress"])
        if decision.restart:
            self._restart_locked()
            self.corrections_used += 1
            self.recovery_applied = True
            return _Handled({"applied": True, "restart": True}, [*notifies, "progress"])
        self.corrections_used += 1
        return _Handled({"applied": True, "ok": False}, notifies, lambda: self._recover(decision))

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
        if attempt is None:
            return False
        if self.config.fencing:
            if attempt.status in TERMINAL_ATTEMPTS or attempt.attempt_id in self.graph.obsolete_attempts:
                return False
            if message.plan_version != self.plan.version and attempt.task_id not in self.carried_forward:
                if attempt.agent_id != "corrector":
                    return False
        for ref in message.target_evidence:
            evidence = self.graph.get(ref)
            if evidence is None or evidence.verification_status in DEAD_STATUSES:
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

    async def revoke_source(self, claim_id: str, reason: str = "source changed") -> bool:
        """Withdraw the latest verified version of a claim. Refused unless the trust model is revocable."""
        async with self.lock:
            target = self._latest_verified_locked(claim_id)
            if target is None:
                return False
            if self.trust_model != "revocable":
                self.record("revocation_refused", self.recovery_id, {"claim_id": claim_id, "version": target.version})
                return False
            self.record("revocation", self.recovery_id, {"claim_id": claim_id, "version": target.version, "reason": reason})
            handled = self._fail_locked((target.ref,), reason, revoke=True)
        await self._notify(handled.notifies)
        if handled.followup is not None:
            self._spawn(handled.followup)
        return True

    async def _settle_invariants(self) -> None:
        """Check each global invariant once per combination of live member versions."""
        for invariant in self.invariants:
            async with self.lock:
                members: list[Evidence] = []
                for claim_id in invariant.claims:
                    latest = self.latest_live_locked(claim_id)
                    if latest is None:
                        break
                    members.append(latest)
                if len(members) != len(invariant.claims):
                    continue
                key = (invariant.name, tuple((item.claim_id, item.version) for item in members))
                if key in self.invariant_checked:
                    continue
                ready = all(
                    item.verification_status == EvidenceStatus.VERIFIED
                    or (item.claim_id, item.version) in self.locally_checked
                    for item in members
                )
                if not ready:
                    continue
                self.invariant_checked.add(key)
                values = {item.claim_id: copy.deepcopy(item.value) for item in members}
                self.verification_cost += 1
                self.record("invariant_check_started", self.verifier_id, {"invariant": invariant.name})
            ok, reason, attributed = invariant.check(values)
            async with self.lock:
                self.record("invariant_verdict", self.verifier_id, {"invariant": invariant.name, "ok": ok, "reason": reason})
                current = [self.graph.get(item.ref) for item in members]
                if any(item is None or item.verification_status in DEAD_STATUSES for item in current):
                    continue
                if ok:
                    for item in members:
                        if item.verification_status == EvidenceStatus.PROVISIONAL:
                            self.graph.set_status(item.ref, EvidenceStatus.VERIFIED)
                        self._discharge_locked(item.ref)
                    handled = _Handled({}, [*(f"evidence:{item.claim_id}" for item in members), *(f"action:{a}" for a in self.actions), "progress"])
                else:
                    suspects = [item for item in current if item is not None and (not attributed or item.claim_id in attributed)]
                    committed = [item for item in suspects if item.verification_status == EvidenceStatus.VERIFIED]
                    if self.trust_model == "local_commit" and committed:
                        if self.unresolved is None:
                            self.unresolved = f"global invariant {invariant.name} failed on committed evidence"
                            self.settled = True
                        self.record("invariant_blocked", self.recovery_id, {"invariant": invariant.name})
                        handled = _Handled({}, ["progress"])
                    else:
                        revoke = self.trust_model == "revocable"
                        if not revoke:
                            suspects = [item for item in suspects if item.verification_status != EvidenceStatus.VERIFIED]
                        handled = self._fail_locked(tuple(item.ref for item in suspects), reason, revoke=revoke)
            await self._notify(handled.notifies)
            if handled.followup is not None:
                self._spawn(handled.followup)

    def _signal_locked(self, attempt_ids: tuple[str, ...] | list[str]) -> None:
        for attempt_id in attempt_ids:
            if attempt_id in MONITOR_ATTEMPTS:
                continue
            self._cancel_attempt_locked(attempt_id)

    def _cancel_attempt_locked(self, attempt_id: str) -> None:
        """Signal cooperative cancellation and preempt the scheduler task."""
        attempt = self.graph.attempts.get(attempt_id)
        if attempt is not None and attempt.status == AttemptStatus.COMPLETED:
            return
        if not self.config.cancellation:
            self.record("cancel_suppressed", attempt_id, {})
            return
        if not self._cancel_event(attempt_id).is_set():
            self.record("cancel_requested", attempt_id, {})
        self._cancel_event(attempt_id).set()
        if self.attempt_canceller is not None:
            self.attempt_canceller(attempt_id)

    def _restart_locked(self) -> None:
        self.carried_forward = set()
        if self.legacy_fault_switch:
            self.faults_enabled = False
        for attempt in list(self.graph.attempts.values()):
            if attempt.attempt_id in MONITOR_ATTEMPTS:
                continue
            if attempt.status == AttemptStatus.RUNNING:
                if self.config.fencing:
                    attempt.status = AttemptStatus.CANCELLED
                    self.graph.obsolete_attempts.add(attempt.attempt_id)
                self._cancel_attempt_locked(attempt.attempt_id)
            elif attempt.status in {AttemptStatus.COMPLETED, AttemptStatus.INVALIDATED, AttemptStatus.PENDING}:
                if self.config.fencing:
                    self.graph.mark_obsolete(attempt.attempt_id)
        for evidence in list(self.graph.evidence.values()):
            if evidence.verification_status == EvidenceStatus.PROVISIONAL:
                self.graph.set_status(evidence.ref, EvidenceStatus.INVALID)
        for obligation in self.obligations.values():
            obligation.handled = True
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

    async def _recover(self, decision: RecoveryDecision) -> bool:
        published: list[EvidenceRef] = []
        for claim_id, value in decision.publish:
            published.append(await self.publish_correction(claim_id, value))
        async with self.lock:
            closed = self.unresolved is not None
        if closed or decision.revision is None:
            async with self.lock:
                self.recovery_applied = True
            await self._notify(["progress"])
            return True
        revision = replace(decision.revision, evidence_versions=tuple(published))
        result = await self.submit(revision)
        async with self.lock:
            self.recovery_applied = True
        await self._notify(["progress"])
        if isinstance(result, dict) and not result.get("accepted", False):
            return "active plan" not in str(result.get("reason", "")) and "stale" not in str(result.get("reason", ""))
        return True

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
            privileged = message.attempt_id in MONITOR_ATTEMPTS
            if not privileged and not self._eligible_locked(message.attempt_id):
                return _Handled({"accepted": False, "reason": "attempt_ineligible"}, ["progress"])
            for ref in message.evidence_versions:
                evidence = self.graph.get(ref)
                if evidence is None or evidence.verification_status in DEAD_STATUSES:
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
        carried = set(message.carry_forward)
        dropped_carry = sorted(
            task_id for task_id in carried
            if (latest := self.latest_attempt.get(task_id)) is not None
            and latest in self.graph.obsolete_attempts
        )
        if dropped_carry:
            self.record("carry_rebased", self.recovery_id, {"tasks": dropped_carry})
        self.carried_forward = carried - set(dropped_carry)
        relaunch = set(message.remove_tasks) & revised.task_ids()
        removed = set(message.remove_tasks) - revised.task_ids()
        for attempt in list(self.graph.attempts.values()):
            if attempt.attempt_id in MONITOR_ATTEMPTS:
                continue
            if attempt.task_id in self.carried_forward:
                continue
            drop = attempt.task_id in removed or (
                attempt.task_id in relaunch and attempt.plan_version == previous.version
            )
            if drop:
                if self.config.fencing:
                    self.graph.mark_obsolete(attempt.attempt_id)
                self._cancel_attempt_locked(attempt.attempt_id)
        self.graph.mark_plan(revised.version, revised.task_ids())
        self.graph.record("plan_revised", message.attempt_id, {
            "version": revised.version,
            "tasks": [node.task_id for node in revised.task_nodes],
            "carry_forward": sorted(self.carried_forward),
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
        if self.obligations:
            return [f"action:{action_id}" for action_id in self.actions]
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

    def task_output_verified(self, task_id: str) -> bool:
        """Call while holding the monitor lock. True once every write of the task has a verified live version."""
        node = self.plan.node(task_id)
        if node is None:
            return False
        if not node.writes:
            return self.task_output_ready(task_id)
        return all(self._allowed_locked(claim_id, verified_only=True) is not None for claim_id in node.writes)

    async def wait_until(self, predicate: Callable[["MonitorRuntime"], bool], timeout: float = 5) -> None:
        cond = self._cond("progress")
        async with cond:
            while True:
                async with self.lock:
                    if predicate(self):
                        return
                await asyncio.wait_for(cond.wait(), timeout)

    def stats(self) -> dict[str, Any]:
        return {
            "lock": self.lock.stats(),
            "max_queue_depth": self.max_queue_depth,
            "events": len(self.graph.events),
            "evidence": len(self.graph.evidence),
            "attempts": len(self.graph.attempts),
            "rebases": self.rebases,
        }


async def _default_transport(action: ProposedAction) -> dict[str, Any]:
    return {"ok": True, "action_id": action.action_id}
