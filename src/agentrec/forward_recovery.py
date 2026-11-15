"""Forward recovery without restoring an old checkpoint.

On a failed verification the monitor invalidates the suspect versions and the
provisional outputs that consumed them, cancels those running attempts, and
continues from new evidence. Independent attempts are not in the suspect cone
and are left running. Old attempts stay in the history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .contract import EvidenceRef, EvidenceStatus, TaskNode
from .protocol import PlanRevisionProposed


@dataclass(frozen=True)
class RecoveryDecision:
    publish: tuple[tuple[str, Any], ...]
    revision: PlanRevisionProposed | None
    restart: bool
    unresolved: bool


def select_suspects(
    *,
    supplied: tuple[EvidenceRef, ...],
    ambiguous: bool,
    consumed: Iterable[EvidenceRef],
    produced: Iterable[EvidenceRef],
    status_of,
) -> tuple[EvidenceRef, ...]:
    """Use the supplied attribution, or the union when attribution is ambiguous."""
    if supplied and not ambiguous:
        return tuple(supplied)
    seen: list[EvidenceRef] = []
    known: set[tuple[str, int]] = set()
    for ref in (*consumed, *produced):
        key = (ref.claim_id, ref.version)
        if key in known:
            continue
        if status_of(ref) == EvidenceStatus.VERIFIED:
            continue
        known.add(key)
        seen.append(ref)
    return tuple(seen)


def decide_recovery(
    *,
    policy: str,
    budget_remaining: int,
    roots: tuple[EvidenceRef, ...],
    corrections: dict[str, Any],
    base_version: int,
    goals: tuple[str, ...],
    planner: TaskNode,
    relaunch: tuple[TaskNode, ...],
    evidence_versions: tuple[EvidenceRef, ...],
) -> RecoveryDecision:
    """Choose the next graph edit. The monitor applies it after releasing its lock."""
    if budget_remaining <= 0:
        return RecoveryDecision((), None, False, True)
    claim_ids = []
    for ref in roots:
        if ref.claim_id not in claim_ids:
            claim_ids.append(ref.claim_id)
    if policy == "verify_before":
        publish = tuple((claim_id, corrections[claim_id]) for claim_id in claim_ids if claim_id in corrections)
        return RecoveryDecision(publish, None, False, False)
    if policy == "restart":
        return RecoveryDecision((), None, True, False)
    if policy == "online":
        revision = PlanRevisionProposed(
            attempt_id="monitor:recovery",
            evidence_versions=evidence_versions,
            base_version=base_version,
            add_tasks=(planner,),
            remove_tasks=("research_restaurant", "transfer", "planner"),
            add_edges=(("research_flights", "planner:2"), ("planner:2", "book_flight")),
            remove_edges=(("planner", "book_flight"),),
            required_goals=goals,
            carry_forward=("research_flights", "restaurant_notes"),
        )
        publish = tuple((claim_id, corrections[claim_id]) for claim_id in claim_ids if claim_id in corrections)
        return RecoveryDecision(publish, revision, False, False)
    if policy == "support":
        revision = PlanRevisionProposed(
            attempt_id="monitor:recovery",
            evidence_versions=evidence_versions,
            base_version=base_version,
            add_tasks=relaunch,
            remove_tasks=tuple(node.task_id for node in relaunch),
            add_edges=(
                ("research_restaurant", "transfer"),
                ("research_restaurant", "restaurant_notes"),
                ("research_restaurant", "planner"),
                ("planner", "book_flight"),
            ),
            remove_edges=(),
            required_goals=goals,
            carry_forward=("research_flights", "restaurant_notes"),
        )
        return RecoveryDecision((), revision, False, False)
    raise ValueError(f"unknown recovery policy {policy}")
