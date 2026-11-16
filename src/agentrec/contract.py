"""Execution contract for speculative multi-agent runs.

Agents may read evidence before it is verified. Outputs stay provisional, and
external actions stay blocked, until verification succeeds. A correction is a
new version. Verified evidence cannot be moved to invalid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class ContractError(ValueError):
    """A status change or record violates the execution contract."""


class EvidenceStatus(str, Enum):
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    INVALID = "invalid"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    OBSOLETE = "obsolete"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    TRANSPORT_FAILED = "transport_failed"


class PlanStatus(str, Enum):
    ACTIVE = "active"
    OBSOLETE = "obsolete"


TERMINAL_ATTEMPTS = {
    AttemptStatus.CANCELLED,
    AttemptStatus.INVALIDATED,
    AttemptStatus.OBSOLETE,
}


def canonical(value: Any) -> str:
    """Stable encoding for exact argument and value comparisons."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class EvidenceRef:
    claim_id: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.claim_id, str) or not self.claim_id:
            raise ContractError("claim id is required")
        if not isinstance(self.version, int) or self.version < 1:
            raise ContractError("evidence version must be a positive integer")


@dataclass(frozen=True)
class Evidence:
    claim_id: str
    version: int
    value: Any
    producer_attempt: str
    supporting_evidence: tuple[EvidenceRef, ...]
    verification_status: EvidenceStatus = EvidenceStatus.PROVISIONAL

    def __post_init__(self) -> None:
        EvidenceRef(self.claim_id, self.version)
        if not self.producer_attempt:
            raise ContractError("producer attempt is required")
        if not isinstance(self.verification_status, EvidenceStatus):
            raise ContractError("verification status is required")

    @property
    def ref(self) -> EvidenceRef:
        return EvidenceRef(self.claim_id, self.version)


def transition_evidence(evidence: Evidence, status: EvidenceStatus) -> Evidence:
    """Move provisional evidence to verified or invalid.

    Verified evidence stays verified. Invalid evidence stays invalid.
    Corrections do not use this function; they are new versions.
    """
    current = evidence.verification_status
    if current == status:
        return evidence
    if current == EvidenceStatus.VERIFIED:
        raise ContractError("verified evidence cannot be invalidated")
    if current == EvidenceStatus.INVALID:
        raise ContractError("invalid evidence is terminal")
    if status not in {EvidenceStatus.VERIFIED, EvidenceStatus.INVALID}:
        raise ContractError(f"provisional evidence cannot become {status.value}")
    return replace(evidence, verification_status=status)


@dataclass
class AgentAttempt:
    agent_id: str
    task_id: str
    attempt_id: str
    plan_version: int
    consumed_evidence_versions: tuple[EvidenceRef, ...] = ()
    status: AttemptStatus = AttemptStatus.PENDING

    def __post_init__(self) -> None:
        if not self.agent_id or not self.task_id or not self.attempt_id:
            raise ContractError("agent, task, and attempt ids are required")
        if self.plan_version < 1:
            raise ContractError("plan version is required")


@dataclass(frozen=True)
class TaskNode:
    task_id: str
    agent_id: str
    goals: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id or not self.agent_id:
            raise ContractError("task and agent ids are required")


@dataclass(frozen=True)
class Plan:
    version: int
    task_nodes: tuple[TaskNode, ...]
    dependency_edges: tuple[tuple[str, str], ...]
    required_goals: tuple[str, ...]
    status: PlanStatus = PlanStatus.ACTIVE

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ContractError("plan version is required")
        if not isinstance(self.status, PlanStatus):
            raise ContractError("plan status is required")

    def task_ids(self) -> set[str]:
        return {node.task_id for node in self.task_nodes}

    def node(self, task_id: str) -> TaskNode | None:
        return next((node for node in self.task_nodes if node.task_id == task_id), None)


@dataclass
class ProposedAction:
    action_id: str
    tool: str
    arguments: dict[str, Any]
    evidence_versions: tuple[EvidenceRef, ...]
    proposing_attempt: str
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    task_id: str = ""
    approved_canonical: str = ""
    transport_error: str = ""
    result: Any = None

    def __post_init__(self) -> None:
        if not self.action_id or not self.tool or not self.proposing_attempt:
            raise ContractError("action id, tool, and proposing attempt are required")
        if not isinstance(self.arguments, dict):
            raise ContractError("action arguments must be a dict")
        canonical(self.arguments)

    @property
    def canonical_arguments(self) -> str:
        return canonical(self.arguments)


@dataclass(frozen=True)
class MonitorEvent:
    event_id: str
    originating_attempt: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id or not self.originating_attempt or not self.event_type:
            raise ContractError("event id, originating attempt, and event type are required")
