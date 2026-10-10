from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any


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

    def descendants(self, nid: str) -> set[str]:
        """All nodes reachable from `nid`, including itself."""
        adj: dict[str, list[str]] = defaultdict(list)
        for s, d, _ in self.edges:
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
            "nodes": [asdict(n) for n in sorted(self.nodes.values(), key=lambda n: n.seq)],
            "edges": [{"src": s, "dst": d, "type": t} for s, d, t in self.edges],
        }
