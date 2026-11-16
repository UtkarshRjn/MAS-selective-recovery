from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .contract import (
    AgentAttempt,
    AttemptStatus,
    Evidence,
    EvidenceRef,
    EvidenceStatus,
    MonitorEvent,
    transition_evidence,
)


@dataclass
class Node:
    id: str
    seq: int
    kind: str  # "step" | "artifact" | "effect"
    key: str
    data: dict[str, Any] = field(default_factory=dict)


class Provenance:
    """Append-only record of steps, artifacts, side effects, and their edges.

    Edge types:
      input   artifact -> step    the artifact was in the step's context
      output  step -> artifact    the step produced the artifact
      effect  step -> effect      the step caused a side effect in the world

    Dependencies are coarse: anything placed in a step's context is an input,
    whether or not the model actually relied on it.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[tuple[str, str, str]] = []
        self._seq = 0

    def _add(self, kind: str, key: str, data: dict[str, Any]) -> str:
        self._seq += 1
        nid = f"{kind}:{self._seq}:{key}"
        self.nodes[nid] = Node(nid, self._seq, kind, key, data)
        return nid

    def add_step(self, key: str, agent: str, **data: Any) -> str:
        return self._add("step", key, {"agent": agent, **data})

    def add_artifact(self, key: str, value: Any, producer: str | None = None, **data: Any) -> str:
        nid = self._add("artifact", key, {"value": value, **data})
        if producer is not None:
            self.edges.append((producer, nid, "output"))
        return nid

    def add_input(self, artifact_id: str, step_id: str) -> None:
        self.edges.append((artifact_id, step_id, "input"))

    def add_effect(self, step_id: str, effect: dict[str, Any]) -> str:
        nid = self._add("effect", effect["tool"], {"status": "applied", **effect})
        self.edges.append((step_id, nid, "effect"))
        return nid

    def update(self, nid: str, **data: Any) -> None:
        self.nodes[nid].data.update(data)

    def of_kind(self, kind: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def _targets(self, src: str, etype: str) -> list[str]:
        return [d for s, d, t in self.edges if s == src and t == etype]

    def outputs_of(self, step_id: str) -> list[Node]:
        return [self.nodes[d] for d in self._targets(step_id, "output")]

    def producer_of(self, artifact_id: str) -> str | None:
        return next((s for s, d, t in self.edges if d == artifact_id and t == "output"), None)

    def effects_of(self, step_id: str) -> list[str]:
        return self._targets(step_id, "effect")

    def faulty_nodes(self) -> list[str]:
        return [n.id for n in self.nodes.values() if n.data.get("faulty")]

    def add_support(self, evidence_id: str, dependent_id: str) -> None:
        """Record that `dependent_id` is justified by `evidence_id`.

        Support is separate from context. A step can have a claim in its prompt
        without that claim being evidence for every output the step produces.
        """
        self.edges.append((evidence_id, dependent_id, "support"))

    def consumers_of(self, evidence_id: str) -> list[str]:
        """Attempts and steps that cite `evidence_id` as support or consumption."""
        found = []
        for src, dst, etype in self.edges:
            if src != evidence_id or etype not in {"support", "consumes"}:
                continue
            node = self.nodes.get(dst)
            if node is not None and node.kind in {"step", "attempt"}:
                found.append(dst)
        return found

    def consumption_closure(self, start_ids: Iterable[str]) -> set[str]:
        """Walk claim → consuming attempt → dependent output, transitively.

        Support edges reach the attempt. The attempt's output edges reach the
        claims it produced. A support-only walk stops on the attempt, so it
        misses those outputs and any later consumer of them. Verified nodes are
        not entered and do not spread invalidation.
        """
        seen: set[str] = set()
        queue: deque[str] = deque()
        for nid in start_ids:
            node = self.nodes.get(nid)
            if node is not None and node.data.get("verification_status") == "verified":
                continue
            queue.append(nid)
        while queue:
            nid = queue.popleft()
            if nid in seen or nid not in self.nodes:
                continue
            node = self.nodes[nid]
            if node.data.get("verification_status") == "verified":
                continue
            seen.add(nid)
            for dst in self._targets(nid, "support") + self._targets(nid, "consumes"):
                queue.append(dst)
            if node.kind in {"step", "attempt"}:
                for dst in self._targets(nid, "output"):
                    queue.append(dst)
        return seen

    def descendants(self, nid: str, etypes: set[str] | None = None) -> set[str]:
        """Nodes reachable from `nid`, including itself.

        The default follows context edges (input, output, effect) and ignores
        support edges, so coarse lineage stays a prompt-context walk.
        """
        if etypes is None:
            etypes = {"input", "output", "effect"}
        adj: dict[str, list[str]] = defaultdict(list)
        for s, d, t in self.edges:
            if t in etypes:
                adj[s].append(d)
        seen = {nid}
        queue = deque([nid])
        while queue:
            for nxt in adj[queue.popleft()]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": getattr(self, "model_calls", []),
            "nodes": [asdict(n) for n in sorted(self.nodes.values(), key=lambda n: n.seq)],
            "edges": [{"src": s, "dst": d, "type": t} for s, d, t in self.edges],
        }


@dataclass
class Invalidation:
    evidence: tuple[EvidenceRef, ...]
    attempts: tuple[str, ...]


class ExecutionGraph:
    """Live, versioned dependency graph owned by the online monitor.

    The offline `Provenance` log stays append-only and coarse. This graph is
    the one the monitor updates: evidence versions, the attempts that consumed
    them, support links from those attempts to the claims they produced, plan
    membership, and the recovery events that changed the graph.
    """

    def __init__(self) -> None:
        self.evidence: dict[tuple[str, int], Evidence] = {}
        self.attempts: dict[str, AgentAttempt] = {}
        self.consumers: dict[tuple[str, int], set[str]] = defaultdict(set)
        self.produced_by: dict[str, list[EvidenceRef]] = defaultdict(list)
        self.support_links: list[tuple[EvidenceRef, str, EvidenceRef]] = []
        self.plan_tasks: dict[int, set[str]] = {}
        self.obsolete_attempts: set[str] = set()
        self.events: list[MonitorEvent] = []
        self._event_seq = 0

    def add_attempt(self, attempt: AgentAttempt) -> None:
        self.attempts[attempt.attempt_id] = attempt

    def versions(self, claim_id: str) -> list[Evidence]:
        return [item for (cid, _), item in self.evidence.items() if cid == claim_id]

    def get(self, ref: EvidenceRef) -> Evidence | None:
        return self.evidence.get((ref.claim_id, ref.version))

    def add_evidence(self, evidence: Evidence) -> None:
        key = (evidence.claim_id, evidence.version)
        if key in self.evidence:
            raise ValueError(f"evidence {evidence.claim_id}@{evidence.version} already exists")
        self.evidence[key] = evidence
        self.produced_by[evidence.producer_attempt].append(evidence.ref)
        self._append("evidence_added", evidence.producer_attempt, {
            "claim_id": evidence.claim_id,
            "version": evidence.version,
            "status": evidence.verification_status.value,
        })

    def register_consumption(self, attempt_id: str, ref: EvidenceRef) -> None:
        """Record a consumer before the evidence value is delivered."""
        self.consumers[(ref.claim_id, ref.version)].add(attempt_id)
        attempt = self.attempts[attempt_id]
        if ref not in attempt.consumed_evidence_versions:
            attempt.consumed_evidence_versions = (*attempt.consumed_evidence_versions, ref)
        self._append("consumption_registered", attempt_id, {
            "claim_id": ref.claim_id,
            "version": ref.version,
        })

    def link_support(self, consumed: EvidenceRef, attempt_id: str, produced: EvidenceRef) -> None:
        self.support_links.append((consumed, attempt_id, produced))
        self._append("support_linked", attempt_id, {
            "consumed": [consumed.claim_id, consumed.version],
            "produced": [produced.claim_id, produced.version],
        })

    def set_status(self, ref: EvidenceRef, status: EvidenceStatus) -> Evidence | None:
        current = self.get(ref)
        if current is None or current.verification_status == status:
            return current
        if current.verification_status != EvidenceStatus.PROVISIONAL:
            return current
        updated = transition_evidence(current, status)
        self.evidence[(ref.claim_id, ref.version)] = updated
        return updated

    def invalidate(self, roots: Iterable[EvidenceRef]) -> Invalidation:
        """Invalidate provisional roots and the attempts and outputs that consumed them.

        Verified evidence is left in place and does not spread. The producer of
        an invalidated claim is closed so it cannot add further outputs, but its
        other claims are not invalidated with it.
        """
        invalid_evidence: list[EvidenceRef] = []
        invalid_attempts: list[str] = []
        seen_evidence: set[tuple[str, int]] = set()
        seen_attempts: set[str] = set()
        queue = deque(roots)
        while queue:
            ref = queue.popleft()
            key = (ref.claim_id, ref.version)
            if key in seen_evidence:
                continue
            current = self.get(ref)
            if current is None or current.verification_status == EvidenceStatus.VERIFIED:
                continue
            seen_evidence.add(key)
            if current.verification_status == EvidenceStatus.PROVISIONAL:
                self.evidence[key] = transition_evidence(current, EvidenceStatus.INVALID)
            invalid_evidence.append(ref)
            producer = current.producer_attempt
            if producer not in seen_attempts and producer in self.attempts:
                seen_attempts.add(producer)
                invalid_attempts.append(producer)
                self._close_attempt(producer)
            for attempt_id in list(self.consumers.get(key, ())):
                if attempt_id in seen_attempts:
                    continue
                seen_attempts.add(attempt_id)
                invalid_attempts.append(attempt_id)
                self._close_attempt(attempt_id)
                for produced in self.produced_by.get(attempt_id, ()):
                    queue.append(produced)
        self._append("invalidated", "monitor:recovery", {
            "evidence": [[ref.claim_id, ref.version] for ref in invalid_evidence],
            "attempts": list(invalid_attempts),
        })
        return Invalidation(tuple(invalid_evidence), tuple(invalid_attempts))

    def _close_attempt(self, attempt_id: str) -> None:
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            return
        if attempt.status not in {AttemptStatus.OBSOLETE, AttemptStatus.CANCELLED, AttemptStatus.INVALIDATED}:
            attempt.status = AttemptStatus.INVALIDATED
        self.obsolete_attempts.add(attempt_id)

    def mark_plan(self, version: int, task_ids: Iterable[str]) -> None:
        self.plan_tasks[version] = set(task_ids)
        self._append("plan_activated", "monitor:recovery", {
            "version": version,
            "tasks": sorted(self.plan_tasks[version]),
        })

    def mark_obsolete(self, attempt_id: str) -> None:
        attempt = self.attempts.get(attempt_id)
        if attempt is not None and attempt.status != AttemptStatus.CANCELLED:
            attempt.status = AttemptStatus.OBSOLETE
        self.obsolete_attempts.add(attempt_id)
        self._append("attempt_obsolete", attempt_id, {})

    def record(self, event_type: str, attempt_id: str, payload: dict[str, Any]) -> None:
        self._append(event_type, attempt_id, payload)

    def _append(self, event_type: str, attempt_id: str, payload: dict[str, Any]) -> None:
        self._event_seq += 1
        self.events.append(MonitorEvent(
            event_id=f"event:{self._event_seq}",
            originating_attempt=attempt_id or "monitor:recovery",
            event_type=event_type,
            payload=payload,
        ))


# Measure provenance writes only; excludes graph traversal, serialization and audit.
def _timed_write(fn):
    from functools import wraps
    from time import perf_counter
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        start = perf_counter()
        try:
            return fn(self, *args, **kwargs)
        finally:
            self.write_s = getattr(self, "write_s", 0.0) + perf_counter() - start
    return wrapped

for _method in ("add_step", "add_artifact", "add_input", "add_effect", "update", "add_support"):
    setattr(Provenance, _method, _timed_write(getattr(Provenance, _method)))
