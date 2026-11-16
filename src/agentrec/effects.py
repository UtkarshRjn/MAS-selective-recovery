"""Append-only effect history and a target that can check versions atomically.

The ledger keeps the whole life of each action: proposal, authorization or
block, physical dispatch, remote outcome, acknowledgment, and later findings
such as "its support was invalidated after dispatch". A final database
snapshot cannot show a delivered email that was later regretted; this history
can. Physical calls, committed mutations, and semantic effects are counted
separately.

`ConditionalStore` is the target side. A monitor lock protects monitor data,
not an external database: only a conditional write that the target validates
atomically (compare-and-set on a resource version) closes the gap between a
check and an effect. Idempotency keys let a dispatcher reconcile a lost
acknowledgment by asking the target instead of retrying blindly.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .action_dispatcher import TransportError
from .contract import ProposedAction
from .provenance import loop_clock


class EffectPhase(str, Enum):
    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    BLOCKED = "blocked"
    DISPATCHED = "dispatched"
    RETRIED = "retried"
    COMMITTED = "committed"
    CONFLICT = "conflict"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"
    ACKNOWLEDGED = "acknowledged"
    SUPPORT_INVALIDATED = "support_invalidated"
    COMPENSATED = "compensated"


class UncertainOutcome(TransportError):
    """The request left the runtime but no acknowledgment came back."""


class ConflictError(TransportError):
    """The target refused a conditional write because the resource version moved."""


@dataclass(frozen=True)
class EffectRecord:
    seq: int
    action_id: str
    phase: EffectPhase
    tool: str
    arguments: str
    semantic_key: str
    resource: str
    attempt_id: str
    evidence: tuple[tuple[str, int], ...]
    t: float
    wall: float
    detail: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "action_id": self.action_id,
            "phase": self.phase.value,
            "tool": self.tool,
            "arguments": self.arguments,
            "semantic_key": self.semantic_key,
            "resource": self.resource,
            "attempt_id": self.attempt_id,
            "evidence": [list(item) for item in self.evidence],
            "t": self.t,
            "wall": self.wall,
            "detail": self.detail,
        }


class EffectLedger:
    def __init__(self, clock: Callable[[], tuple[float, float]] = loop_clock) -> None:
        self.clock = clock
        self.records: list[EffectRecord] = []
        self.origin: tuple[float, float] | None = None

    def append(
        self,
        phase: EffectPhase,
        action: ProposedAction,
        *,
        semantic_key: str,
        resource: str,
        detail: dict[str, Any] | None = None,
    ) -> EffectRecord:
        loop_time, wall = self.clock()
        if self.origin is None:
            self.origin = (loop_time, wall)
        record = EffectRecord(
            seq=len(self.records) + 1,
            action_id=action.action_id,
            phase=phase,
            tool=action.tool,
            arguments=action.canonical_arguments,
            semantic_key=semantic_key,
            resource=resource,
            attempt_id=action.proposing_attempt,
            evidence=tuple((ref.claim_id, ref.version) for ref in action.evidence_versions),
            t=round(loop_time - self.origin[0], 6),
            wall=round(wall - self.origin[1], 6),
            detail=dict(detail or {}),
        )
        self.records.append(record)
        return record

    def of(self, action_id: str) -> list[EffectRecord]:
        return [item for item in self.records if item.action_id == action_id]

    def phase(self, phase: EffectPhase) -> list[EffectRecord]:
        return [item for item in self.records if item.phase == phase]

    def last_phase(self, action_id: str) -> EffectPhase | None:
        rows = self.of(action_id)
        return rows[-1].phase if rows else None

    def physical_calls(self) -> int:
        return len(self.phase(EffectPhase.DISPATCHED)) + len(self.phase(EffectPhase.RETRIED))

    def committed(self) -> list[EffectRecord]:
        """Mutations the target applied, including duplicates and later-regretted ones."""
        out = [item for item in self.records if item.phase == EffectPhase.COMMITTED]
        out.extend(
            item for item in self.records
            if item.phase == EffectPhase.RECONCILED and item.detail.get("committed")
        )
        return sorted(out, key=lambda item: item.seq)

    def duplicates(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.committed():
            counts[item.semantic_key] = counts.get(item.semantic_key, 0) + 1
        return {key: count for key, count in counts.items() if count > 1}

    def uncertain(self) -> list[str]:
        seen: dict[str, EffectPhase] = {}
        for item in self.records:
            seen[item.action_id] = item.phase
        return [action_id for action_id, phase in seen.items() if phase == EffectPhase.UNCERTAIN]

    def rows(self) -> list[dict[str, Any]]:
        return [item.row() for item in self.records]


@dataclass
class Commit:
    seq: int
    resource: str
    operation: str
    before: Any
    after: Any
    version: int
    idempotency_key: str
    external: bool = False


class ConditionalStore:
    """Versioned target state. Every write is applied atomically by the target."""

    def __init__(self, resources: dict[str, Any] | None = None) -> None:
        self.data: dict[str, tuple[Any, int]] = {
            key: (copy.deepcopy(value), 1) for key, value in (resources or {}).items()
        }
        self.applied: dict[str, dict[str, Any]] = {}
        self.history: list[Commit] = []
        self.lose_ack: dict[str, int] = {}
        self.conflicts = 0
        self.duplicates_suppressed = 0

    def read(self, resource: str) -> tuple[Any, int]:
        value, version = self.data.get(resource, (None, 0))
        return copy.deepcopy(value), version

    def version(self, resource: str) -> int:
        return self.data.get(resource, (None, 0))[1]

    def write(
        self,
        resource: str,
        update: Callable[[Any], Any],
        *,
        operation: str,
        expected_version: int | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Apply `update` if the precondition holds. Raises ConflictError or UncertainOutcome."""
        if idempotency_key and idempotency_key in self.applied:
            self.duplicates_suppressed += 1
            return {**self.applied[idempotency_key], "duplicate_suppressed": True}
        before, version = self.data.get(resource, (None, 0))
        if expected_version is not None and expected_version != version:
            self.conflicts += 1
            raise ConflictError(f"{resource} is at version {version}, expected {expected_version}")
        after = update(copy.deepcopy(before))
        self.data[resource] = (after, version + 1)
        commit = Commit(len(self.history) + 1, resource, operation, before, copy.deepcopy(after), version + 1, idempotency_key)
        self.history.append(commit)
        result = {"ok": True, "resource": resource, "version": version + 1, "commit": commit.seq}
        if idempotency_key:
            self.applied[idempotency_key] = result
        remaining = self.lose_ack.get(operation, 0)
        if remaining > 0:
            self.lose_ack[operation] = remaining - 1
            raise UncertainOutcome(f"acknowledgment for {operation} on {resource} was lost")
        return result

    def external_write(self, resource: str, value: Any) -> None:
        """A writer outside the runtime changes the resource, as a customer or another system would."""
        before, version = self.data.get(resource, (None, 0))
        self.data[resource] = (copy.deepcopy(value), version + 1)
        self.history.append(Commit(len(self.history) + 1, resource, "external", before, copy.deepcopy(value), version + 1, "", True))

    def lookup(self, idempotency_key: str) -> dict[str, Any] | None:
        return self.applied.get(idempotency_key)


def idempotency_key(semantic_key: str) -> str:
    """One key per semantic request, so a retry of the same request is recognized by the target."""
    return f"idem:{semantic_key}"
