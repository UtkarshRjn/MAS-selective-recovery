"""Validated messages exchanged between an agent attempt and the monitor.

Every message names its attempt and the evidence versions it is about.
The monitor, not this module, rejects messages from invalidated attempts.
An older plan version is recorded here and interpreted by the monitor: work
the monitor has carried forward stays eligible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contract import ArtifactRef, EvidenceRef, TaskNode, canonical


class ProtocolError(ValueError):
    """A message is missing an attempt id, a version, or a required field."""


def _attempt(attempt_id: str) -> str:
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ProtocolError("attempt_id is required")
    return attempt_id


def _refs(refs: tuple[EvidenceRef, ...] | list[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    if refs is None:
        raise ProtocolError("evidence versions are required")
    out: list[EvidenceRef] = []
    for ref in refs:
        if not isinstance(ref, EvidenceRef):
            raise ProtocolError("evidence version must be an EvidenceRef")
        out.append(ref)
    return tuple(out)


def _edges(edges: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for edge in edges:
        if (
            not isinstance(edge, tuple)
            or len(edge) != 2
            or not isinstance(edge[0], str)
            or not isinstance(edge[1], str)
            or not edge[0]
            or not edge[1]
        ):
            raise ProtocolError("dependency edge must be a pair of task ids")
        out.append((edge[0], edge[1]))
    return tuple(out)


@dataclass(frozen=True)
class EvidenceRequested:
    attempt_id: str
    claim_id: str
    evidence_versions: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        if not isinstance(self.claim_id, str) or not self.claim_id:
            raise ProtocolError("claim_id is required")
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))


@dataclass(frozen=True)
class OutputProposed:
    attempt_id: str
    claim_id: str
    value: Any
    evidence_versions: tuple[EvidenceRef, ...]
    plan_version: int
    applies_to: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        if not self.claim_id:
            raise ProtocolError("claim_id is required")
        if self.plan_version < 1:
            raise ProtocolError("plan_version is required")
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))
        if any(not isinstance(item, ArtifactRef) for item in self.applies_to):
            raise ProtocolError("applicability must name ArtifactRef identities")
        object.__setattr__(self, "applies_to", tuple(self.applies_to))


@dataclass(frozen=True)
class ActionProposed:
    attempt_id: str
    action_id: str
    tool: str
    arguments: dict[str, Any]
    evidence_versions: tuple[EvidenceRef, ...]
    task_id: str
    plan_version: int
    preconditions: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        if not self.action_id or not self.tool or not self.task_id:
            raise ProtocolError("action_id, tool, and task_id are required")
        if not isinstance(self.arguments, dict):
            raise ProtocolError("action arguments must be a dict")
        canonical(self.arguments)
        if self.plan_version < 1:
            raise ProtocolError("plan_version is required")
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))
        if not isinstance(self.preconditions, dict):
            raise ProtocolError("preconditions map a resource to its expected version")


@dataclass(frozen=True)
class PlanRevisionProposed:
    attempt_id: str
    evidence_versions: tuple[EvidenceRef, ...]
    base_version: int
    add_tasks: tuple[TaskNode, ...]
    remove_tasks: tuple[str, ...]
    add_edges: tuple[tuple[str, str], ...]
    remove_edges: tuple[tuple[str, str], ...]
    required_goals: tuple[str, ...]
    carry_forward: tuple[str, ...]

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        if self.base_version < 1:
            raise ProtocolError("base_version is required")
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))
        for node in self.add_tasks:
            if not isinstance(node, TaskNode):
                raise ProtocolError("added task must be a TaskNode")
        for task_id in self.remove_tasks:
            if not isinstance(task_id, str) or not task_id:
                raise ProtocolError("removed task id is required")
        object.__setattr__(self, "add_edges", _edges(self.add_edges))
        object.__setattr__(self, "remove_edges", _edges(self.remove_edges))
        if not self.required_goals:
            raise ProtocolError("required goals are required")


@dataclass(frozen=True)
class VerificationCompleted:
    attempt_id: str
    evidence_versions: tuple[EvidenceRef, ...]
    target_attempt: str
    target_evidence: tuple[EvidenceRef, ...]
    ok: bool
    reason: str
    suspect_claims: tuple[EvidenceRef, ...]
    ambiguous: bool
    plan_version: int

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        if not self.target_attempt:
            raise ProtocolError("target_attempt is required")
        if self.plan_version < 1:
            raise ProtocolError("plan_version is required")
        if not isinstance(self.ok, bool) or not isinstance(self.ambiguous, bool):
            raise ProtocolError("verdict flags must be booleans")
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))
        object.__setattr__(self, "target_evidence", _refs(self.target_evidence))
        object.__setattr__(self, "suspect_claims", _refs(self.suspect_claims))
        if not self.ok and not self.suspect_claims and not self.ambiguous:
            raise ProtocolError("a failed verification names suspects or is marked ambiguous")


@dataclass(frozen=True)
class AttemptCompleted:
    attempt_id: str
    evidence_versions: tuple[EvidenceRef, ...]
    plan_version: int

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        if self.plan_version < 1:
            raise ProtocolError("plan_version is required")
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))


@dataclass(frozen=True)
class CancellationAcknowledged:
    attempt_id: str
    evidence_versions: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        _attempt(self.attempt_id)
        object.__setattr__(self, "evidence_versions", _refs(self.evidence_versions))


MESSAGE_TYPES = (
    EvidenceRequested,
    OutputProposed,
    ActionProposed,
    PlanRevisionProposed,
    VerificationCompleted,
    AttemptCompleted,
    CancellationAcknowledged,
)


def ensure_message(message: object) -> None:
    if not isinstance(message, MESSAGE_TYPES):
        raise ProtocolError(f"unsupported message {type(message).__name__}")
