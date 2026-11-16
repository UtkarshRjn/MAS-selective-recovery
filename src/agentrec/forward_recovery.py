"""Forward recovery without restoring an old checkpoint.

On a failed verification the monitor invalidates the suspect versions and the
provisional outputs that consumed them, cancels those running attempts, and
continues from new evidence. Independent attempts are not in the suspect cone
and are left running. Old attempts stay in the history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .contract import Evidence, EvidenceRef, EvidenceStatus, TaskNode
from .protocol import PlanRevisionProposed
from .verification import verify_claim


@dataclass(frozen=True)
class RecoveryDecision:
    publish: tuple[tuple[str, Any], ...]
    revision: PlanRevisionProposed | None
    restart: bool
    unresolved: bool
    rejected: bool = False


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


def coarse_claims(tasks: tuple[TaskNode, ...], roots: Iterable[str]) -> tuple[str, ...]:
    """Every claim written by a producer of a suspect, including siblings."""
    root_ids = set(roots)
    expanded: list[str] = []
    for node in tasks:
        if not (set(node.writes) & root_ids):
            continue
        for claim_id in node.writes:
            if claim_id not in expanded:
                expanded.append(claim_id)
    for claim_id in roots:
        if claim_id not in expanded:
            expanded.append(claim_id)
    return tuple(expanded)


def revise_suffix(
    *,
    tasks: tuple[TaskNode, ...],
    edges: tuple[tuple[str, str], ...],
    goals: tuple[str, ...],
    roots: tuple[str, ...],
    base_version: int,
    evidence_versions: tuple[EvidenceRef, ...],
) -> PlanRevisionProposed:
    """Relaunch the faulty task and every task after it in plan order.

    A later task is included even when it never read the faulty claim.
    """
    root_ids = set(roots)
    fault_at = 0
    for index, node in enumerate(tasks):
        if set(node.writes) & root_ids:
            fault_at = index
            break
    kept = tasks[:fault_at]
    tainted = tasks[fault_at:]
    taken = {node.task_id for node in tasks}
    added = [
        TaskNode(_successor_id(node.task_id, taken), node.agent_id, node.goals, node.reads, node.writes)
        for node in tainted
    ]
    carry = tuple(node.task_id for node in kept)
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
        remove_tasks=tuple(node.task_id for node in tainted),
        add_edges=tuple(sorted(new_edges)),
        remove_edges=(),
        required_goals=goals,
        carry_forward=carry,
    )


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


def superstep_levels(tasks: tuple[TaskNode, ...], edges: tuple[tuple[str, str], ...]) -> dict[str, int]:
    """Bulk-synchronous level of each task: one more than its deepest predecessor."""
    ids = {node.task_id for node in tasks}
    preds: dict[str, list[str]] = {task_id: [] for task_id in ids}
    for src, dst in edges:
        if src in ids and dst in ids:
            preds[dst].append(src)
    levels: dict[str, int] = {}

    def level(task_id: str, stack: tuple[str, ...] = ()) -> int:
        if task_id in levels:
            return levels[task_id]
        if task_id in stack:
            raise ValueError("plan is not a DAG")
        value = 0 if not preds[task_id] else 1 + max(level(src, (*stack, task_id)) for src in preds[task_id])
        levels[task_id] = value
        return value

    for node in tasks:
        level(node.task_id)
    return levels


def revise_superstep(
    *,
    tasks: tuple[TaskNode, ...],
    edges: tuple[tuple[str, str], ...],
    goals: tuple[str, ...],
    roots: tuple[str, ...],
    base_version: int,
    evidence_versions: tuple[EvidenceRef, ...],
) -> PlanRevisionProposed:
    """Replay from the checkpoint before the faulty superstep.

    Earlier supersteps are kept. In the faulty superstep, siblings that did not
    write a suspect keep their pending writes. The faulty task and every later
    superstep run again, whether or not they read the fault.
    """
    levels = superstep_levels(tasks, edges)
    root_ids = set(roots)
    producers = [node for node in tasks if set(node.writes) & root_ids]
    fault_level = min((levels[node.task_id] for node in producers), default=0)
    rerun = [
        node for node in tasks
        if levels[node.task_id] > fault_level or (set(node.writes) & root_ids)
    ]
    rerun_ids = {node.task_id for node in rerun}
    kept = [node for node in tasks if node.task_id not in rerun_ids]
    taken = {node.task_id for node in tasks}
    added = [
        TaskNode(_successor_id(node.task_id, taken), node.agent_id, node.goals, node.reads, node.writes)
        for node in rerun
    ]
    carry = tuple(node.task_id for node in kept)
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
        remove_tasks=tuple(node.task_id for node in rerun),
        add_edges=tuple(sorted(new_edges)),
        remove_edges=(),
        required_goals=goals,
        carry_forward=carry,
    )


Screen = Callable[[str, Any], bool]


def screen_proposals(
    claim_ids: Iterable[str],
    corrections: dict[str, Any],
    truth: dict[str, Any] | None = None,
    screen: Screen | None = None,
) -> tuple[tuple[tuple[str, Any], ...], tuple[str, ...]]:
    """Accept a repair only when the checker accepts it.

    `screen` is the runtime checker's verdict on the proposed value. `truth` is
    the legacy oracle screen. A rejected value is not published and cannot be
    cited by an action.
    """
    accepted: list[tuple[str, Any]] = []
    rejected: list[str] = []
    for claim_id in claim_ids:
        if claim_id not in corrections:
            continue
        value = corrections[claim_id]
        if screen is not None:
            ok = screen(claim_id, value)
        else:
            probe = Evidence(claim_id, 1, value, "proposal", ())
            ok = verify_claim(probe, truth or {}).ok
        if ok:
            accepted.append((claim_id, value))
        else:
            rejected.append(claim_id)
    return tuple(accepted), tuple(rejected)


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
    truth: dict[str, Any] | None = None,
    screen: Screen | None = None,
) -> RecoveryDecision:
    """Choose the next graph edit. The monitor applies it after releasing its lock.

    When `screen` (the runtime checker) or the legacy `truth` is set, an online
    repair is published only after it is accepted. A rejected repair spends
    the attempt. With budget left, the revision is the support cone and the
    producer runs again. With no budget left, nothing is published and nothing
    is booked.
    """
    if budget_remaining <= 0:
        return RecoveryDecision((), None, False, True)
    claim_ids: list[str] = []
    for ref in roots:
        if ref.claim_id not in claim_ids:
            claim_ids.append(ref.claim_id)
    publish = tuple((claim_id, corrections[claim_id]) for claim_id in claim_ids if claim_id in corrections)
    if policy in {"verify_before", "local_repair"}:
        if screen is not None:
            publish, _rejected = screen_proposals(claim_ids, corrections, screen=screen)
        return RecoveryDecision(publish, None, False, False)
    if policy == "none":
        return RecoveryDecision((), None, False, False)
    if policy == "restart":
        return RecoveryDecision((), None, True, False)
    if policy == "superstep":
        revision = revise_superstep(
            tasks=tasks,
            edges=edges,
            goals=goals,
            roots=tuple(claim_ids),
            base_version=base_version,
            evidence_versions=evidence_versions,
        )
        return RecoveryDecision((), revision, False, False)
    if policy == "online":
        rejected: tuple[str, ...] = ()
        if screen is not None or truth is not None:
            publish, rejected = screen_proposals(claim_ids, corrections, truth, screen)
        if rejected and not publish:
            if budget_remaining <= 1:
                return RecoveryDecision((), None, False, True, True)
            revision = revise_from_cone(
                tasks=tasks,
                edges=edges,
                goals=goals,
                roots=tuple(claim_ids),
                replaced=(),
                base_version=base_version,
                evidence_versions=evidence_versions,
            )
            return RecoveryDecision((), revision, False, False, True)
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
    if policy == "coarse":
        revision = revise_from_cone(
            tasks=tasks,
            edges=edges,
            goals=goals,
            roots=coarse_claims(tasks, claim_ids),
            replaced=(),
            base_version=base_version,
            evidence_versions=evidence_versions,
        )
        return RecoveryDecision((), revision, False, False)
    if policy == "suffix":
        revision = revise_suffix(
            tasks=tasks,
            edges=edges,
            goals=goals,
            roots=tuple(claim_ids),
            base_version=base_version,
            evidence_versions=evidence_versions,
        )
        return RecoveryDecision((), revision, False, False)
    raise ValueError(f"unknown recovery policy {policy}")
