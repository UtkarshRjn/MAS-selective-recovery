"""Recovery methods applied to a world and provenance graph left behind by a faulty run.

Every method is told which nodes are faulty (via the `faulty` flag set by the injector),
so recovery quality is measured independently of fault detection.

  full_restart    undo every effect, then rerun the whole workflow
  coarse_lineage  undo only effects downstream of the fault, reuse untainted steps,
                  and rerun the rest; dependencies are everything in a step's context

The always-verify baseline is not a recovery method: it runs the workflow forward with a
verifier call on every intermediate result (see `RunContext.verify`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .llm import LLM
from .provenance import Provenance
from .workflow import RunContext, run_agents
from .world import World


@dataclass
class RecoveryReport:
    method: str
    tainted_steps: list[str]
    steps_total: int
    steps_recomputed: int
    steps_reused: int
    undone: int = 0
    compensated: int = 0
    irreversible: int = 0
    usage: dict[str, float] = field(default_factory=dict)
    wall_s: float = 0.0
    prov: Provenance | None = None

    def row(self) -> dict[str, Any]:
        return {
            "tainted_steps": self.tainted_steps,
            "steps_total": self.steps_total,
            "steps_recomputed": self.steps_recomputed,
            "steps_reused": self.steps_reused,
            "undone": self.undone,
            "compensated": self.compensated,
            "irreversible": self.irreversible,
            "recovery_usage": self.usage,
            "recovery_wall_s": round(self.wall_s, 3),
        }


def undo_effects(world: World, prov: Provenance, effect_ids: list[str]) -> dict[str, int]:
    """Undo or compensate effects newest-first; irreversible effects are only recorded."""
    counts = {"undone": 0, "compensated": 0, "irreversible": 0}
    for eid in sorted(effect_ids, key=lambda e: prov.nodes[e].seq, reverse=True):
        effect = prov.nodes[eid].data
        if effect["status"] != "applied":
            continue
        if effect["class"] == "irreversible":
            status = "irreversible"
        else:
            world.apply_undo(effect["undo"])
            status = "undone" if effect["class"] == "reversible" else "compensated"
        prov.update(eid, status=status)
        counts[status] += 1
    return counts


def _rerun(
    method: str,
    task: dict[str, Any],
    world: World,
    llm: LLM,
    tainted: list[str],
    counts: dict[str, int],
    reuse: dict[str, dict[str, Any]] | None = None,
    fault_note: dict[str, Any] | None = None,
) -> tuple[RecoveryReport, RunContext]:
    before, t0 = llm.usage.copy(), time.perf_counter()
    ctx = RunContext(task, world, llm, reuse=reuse or {}, fault_note=fault_note)
    run_agents(ctx)
    steps = ctx.prov.of_kind("step")
    reused = sum(1 for s in steps if s.data.get("reused"))
    report = RecoveryReport(
        method=method,
        tainted_steps=tainted,
        steps_total=len(steps),
        steps_recomputed=len(steps) - reused,
        steps_reused=reused,
        usage=llm.usage.since(before),
        wall_s=time.perf_counter() - t0,
        prov=ctx.prov,
        **counts,
    )
    return report, ctx


def full_restart(task: dict[str, Any], world: World, llm: LLM, prov: Provenance) -> RecoveryReport:
    counts = undo_effects(world, prov, [n.id for n in prov.of_kind("effect")])
    tainted = [s.key for s in prov.of_kind("step")]
    report, _ = _rerun("restart", task, world, llm, tainted, counts)
    return report


def coarse_taint(prov: Provenance) -> set[str]:
    """Everything downstream of the step that produced each faulty artifact.

    Starting from the producer rather than the artifact itself ensures that the step
    which emitted the bad result is recomputed instead of reused.
    """
    taint: set[str] = set()
    for nid in prov.faulty_nodes():
        taint |= prov.descendants(prov.producer_of(nid) or nid)
    return taint


def suffix_taint(prov: Provenance) -> set[str]:
    """Every node from the earliest faulty step onward, in execution order.

    A later step is included even when it never read the faulty artifact.
    """
    producers = []
    for nid in prov.faulty_nodes():
        node = prov.nodes[nid]
        producers.append(prov.producer_of(nid) if node.kind == "artifact" else nid)
    producers = [p for p in producers if p is not None]
    if not producers:
        return set()
    earliest = min(prov.nodes[p].seq for p in producers)
    return {n.id for n in prov.nodes.values() if n.seq >= earliest}


def _has_support_edge(prov: Provenance, nid: str) -> bool:
    return any(src == nid and etype == "support" for src, _, etype in prov.edges)


def support_taint(prov: Provenance) -> set[str]:
    """Replay the producer of a faulty claim and steps that cite it as evidence.

    A faulty artifact with no support edges falls back to coarse lineage for
    that artifact. Context alone does not spread the taint.
    """
    taint: set[str] = set()
    for nid in prov.faulty_nodes():
        node = prov.nodes[nid]
        if node.kind == "artifact" and _has_support_edge(prov, nid):
            producer = prov.producer_of(nid)
            if producer is not None:
                taint.add(producer)
            taint |= prov.descendants(nid, {"support"})
        else:
            taint |= prov.descendants(prov.producer_of(nid) or nid)
    return taint


def selective_recover(
    method: str,
    task: dict[str, Any],
    world: World,
    llm: LLM,
    prov: Provenance,
    fault_note: dict[str, Any] | None = None,
    taint_fn: Any = coarse_taint,
) -> RecoveryReport:
    """Undo tainted effects, reuse the rest, and rerun the tainted steps.

    `fault_note` is appended only to prompts of steps that are redone. Reused steps
    are replayed from the log and make no LLM call.
    """
    taint = taint_fn(prov)
    steps = prov.of_kind("step")
    tainted_steps = [s for s in steps if s.id in taint]
    tainted = [s.key for s in tainted_steps]
    # Support edges reach the step, not the effect it caused. Undoing the step's
    # effects is what cancels a booking when that step is redone.
    effect_ids = [eid for s in tainted_steps for eid in prov.effects_of(s.id)]
    counts = undo_effects(world, prov, effect_ids)

    reuse: dict[str, dict[str, Any]] = {}
    for s in steps:
        if s.id in taint or s.data.get("rejected"):
            continue
        effect_ids = prov.effects_of(s.id)
        reuse[s.key] = {
            "fingerprint": s.data.get("fingerprint"),
            "artifacts": [(a.key, a.data["value"]) for a in prov.outputs_of(s.id)],
            "effects": [dict(prov.nodes[e].data) for e in effect_ids],
            "effect_ids": effect_ids,
        }

    report, ctx = _rerun(method, task, world, llm, tainted, counts, reuse, fault_note)

    # A kept step whose plan item changed was not reused, so its old effects are now stale.
    orphaned = [e for key, r in reuse.items() if key not in ctx.consumed for e in r["effect_ids"]]
    for k, v in undo_effects(world, prov, orphaned).items():
        setattr(report, k, getattr(report, k) + v)
    return report


def coarse_lineage(task: dict[str, Any], world: World, llm: LLM, prov: Provenance) -> RecoveryReport:
    return selective_recover("coarse", task, world, llm, prov)


def suffix_recover(task: dict[str, Any], world: World, llm: LLM, prov: Provenance) -> RecoveryReport:
    return selective_recover("suffix", task, world, llm, prov, taint_fn=suffix_taint)


def support_recover(task: dict[str, Any], world: World, llm: LLM, prov: Provenance) -> RecoveryReport:
    return selective_recover("support", task, world, llm, prov, taint_fn=support_taint)
