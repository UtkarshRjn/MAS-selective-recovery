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


def tainted_claims(tasks: tuple[TaskNode, ...], roots: Iterable[str]) -> set[str]:
    """Claims spoiled by the suspect roots.

    A task that reads a tainted claim taints every claim it writes. A producer
    that also writes an untouched claim does not taint that sibling.
    """
    tainted = set(roots)
    changed = True
    while changed:
        changed = False
        for node in tasks:
            if not (set(node.reads) & tainted):
                continue
            for claim_id in node.writes:
                if claim_id not in tainted:
                    tainted.add(claim_id)
                    changed = True
    return tainted


def _successor_id(task_id: str, taken: set[str]) -> str:
    number = 2
    while True:
        candidate = f"{task_id}:{number}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        number += 1


def revise_from_cone(
    *,
    tasks: tuple[TaskNode, ...],
    edges: tuple[tuple[str, str], ...],
    goals: tuple[str, ...],
    roots: tuple[str, ...],
    replaced: tuple[str, ...],
    base_version: int,
    evidence_versions: tuple[EvidenceRef, ...],
) -> PlanRevisionProposed:
    """Drop the tainted cone and relaunch only the tasks still required.

    A claim in `replaced` is supplied by a correction, so its producer is not
    relaunched. Every relaunched visit is a new task id.
    """
    spoiled = tainted_claims(tasks, roots)
    tainted = [
        node for node in tasks
        if set(node.reads) & spoiled or set(node.writes) & spoiled
    ]
    tainted_ids = {node.task_id for node in tainted}
    kept = [node for node in tasks if node.task_id not in tainted_ids]
    replaced_ids = set(replaced)
    relaunch: list[TaskNode] = []
    pending = list(tainted)
    while True:
        covered: set[str] = set()
        for node in (*kept, *relaunch):
            covered.update(node.goals)
        written = {claim_id for node in (*kept, *relaunch) for claim_id in node.writes}
        chosen: TaskNode | None = None
        for node in pending:
            missing_goal = any(goal not in covered for goal in node.goals)
            needed = False
            for reader in (*kept, *relaunch):
                for claim_id in reader.reads:
                    if (
                        claim_id in node.writes
                        and claim_id in spoiled
                        and claim_id not in replaced_ids
                        and claim_id not in written
                    ):
                        needed = True
            if missing_goal or needed:
                chosen = node
                break
        if chosen is None:
            break
        relaunch.append(chosen)
        pending = [node for node in pending if node.task_id != chosen.task_id]

    taken = {node.task_id for node in tasks}
    added: list[TaskNode] = []
    for node in relaunch:
        added.append(TaskNode(
            _successor_id(node.task_id, taken),
            node.agent_id,
            node.goals,
            node.reads,
            node.writes,
        ))
    carry = tuple(node.task_id for node in tasks if node.task_id not in tainted_ids)
    carry_ids = set(carry)
    rebuilt = [*kept, *added]
    new_edges: set[tuple[str, str]] = set()
    for src, dst in edges:
        if src in carry_ids and dst in carry_ids:
            new_edges.add((src, dst))
    for node in added:
        for claim_id in node.reads:
            for writer in rebuilt:
                if claim_id in writer.writes and writer.task_id != node.task_id:
                    new_edges.add((writer.task_id, node.task_id))
    return PlanRevisionProposed(
        attempt_id="monitor:recovery",
        evidence_versions=evidence_versions,
        base_version=base_version,
        add_tasks=tuple(added),
        remove_tasks=tuple(node.task_id for node in tasks if node.task_id in tainted_ids),
        add_edges=tuple(sorted(new_edges)),
        remove_edges=(),
        required_goals=goals,
        carry_forward=carry,
    )


def decide_recovery(
    *,
    policy: str,
    budget_remaining: int,
    roots: tuple[EvidenceRef, ...],
    corrections: dict[str, Any],
    base_version: int,
    goals: tuple[str, ...],
    tasks: tuple[TaskNode, ...],
    edges: tuple[tuple[str, str], ...],
    evidence_versions: tuple[EvidenceRef, ...],
) -> RecoveryDecision:
    """Choose the next graph edit. The monitor applies it after releasing its lock."""
    if budget_remaining <= 0:
        return RecoveryDecision((), None, False, True)
    claim_ids: list[str] = []
    for ref in roots:
        if ref.claim_id not in claim_ids:
            claim_ids.append(ref.claim_id)
    publish = tuple((claim_id, corrections[claim_id]) for claim_id in claim_ids if claim_id in corrections)
    if policy == "verify_before":
        return RecoveryDecision(publish, None, False, False)
    if policy == "restart":
        return RecoveryDecision((), None, True, False)
    if policy == "online":
        revision = revise_from_cone(
            tasks=tasks,
            edges=edges,
            goals=goals,
            roots=tuple(claim_ids),
            replaced=tuple(claim_id for claim_id, _value in publish),
            base_version=base_version,
            evidence_versions=evidence_versions,
        )
        return RecoveryDecision(publish, revision, False, False)
    if policy == "support":
        revision = revise_from_cone(
            tasks=tasks,
            edges=edges,
            goals=goals,
            roots=tuple(claim_ids),
            replaced=(),
            base_version=base_version,
            evidence_versions=evidence_versions,
        )
        return RecoveryDecision((), revision, False, False)
    raise ValueError(f"unknown recovery policy {policy}")
