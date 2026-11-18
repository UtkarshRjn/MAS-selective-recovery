"""ParaRecover-derived two-fault cases (a new experimental condition, not a ParaRecover score).

Each frozen motif's plan becomes a workflow: one step per tool node, reading
its plan dependencies, plus the final response. A node's output records the
tool, the recorded rollout result where one exists (a deterministic stand-in
otherwise), and digests of the inputs it consumed; a node whose tool changes
external state proposes that effect through the gateway, bound to its output,
and the response is delivered by an irreversible `respond` effect.

Every policy gets the same derivational check (recompute a node from the
input versions it cites), the same corrector (recompute from current inputs),
and the same contracts. The two plan nodes ParaRecover's error analysis names
first are the fault sites; each fault is a value error tagged with the
record's error type. Schedules release the second failed check together with
the first (`simultaneous`), one second later (`staggered`), or as the first
repair's plan revision activates (`during_activation`).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..action_contracts import ActionContract, approve, cited, reject
from ..contract import canonical
from ..fault_schedule import FaultSpec
from ..mechanism.cases import _derived_checks, append_to, lookup_tools
from ..mechanism.spec import ActionSpec, CaseSpec, Schedule, StepSpec, Trigger, World, effect_tool

MOTIFS = Path(__file__).with_name("motifs.json")
SLOTS = {
    f"PR_{relation}_{index}": (relation, index)
    for relation in ("nested", "overlap", "disjoint") for index in (1, 2)
}
UNDO = "undo:"


@lru_cache(maxsize=1)
def motifs() -> dict[str, Any]:
    return json.loads(MOTIFS.read_text())


def motif_for(family: str, template: str) -> dict[str, Any]:
    relation, index = SLOTS[family]
    offset = (index - 1) + (2 if template == "test" else 0)
    return motifs()["motifs"][relation][offset]


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:16]


def _claim(node_id: str) -> str:
    return f"out-{node_id}"


def build(family: str, template: str = "dev") -> CaseSpec:
    motif = motif_for(family, template)
    nodes = {node["id"]: node for node in motif["nodes"]}
    order = [node["id"] for node in motif["nodes"]]
    ref: list[CaseSpec] = []

    def node_step(node: dict[str, Any], position: int) -> StepSpec:
        claim = _claim(node["id"])
        reads = tuple(_claim(dep) for dep in node["deps"] if dep in nodes)
        result = node["recorded_result"] or f"{node['tool']} completed for plan node {node['id']}"

        def compute(values: dict[str, Any], _ctx) -> dict[str, Any]:
            inputs = {read: values[read]["digest"] for read in reads}
            return {claim: {"node": node["id"], "tool": node["tool"], "result": result, "inputs": inputs,
                            "digest": _digest([node["tool"], node["id"], inputs])}}

        actions: tuple[ActionSpec, ...] = ()
        if node["effect"]:
            actions = (ActionSpec(node["tool"], lambda v, _c, claim=claim: {"node": v[claim]["node"],
                                                                             "digest": v[claim]["digest"]}),)
        return StepSpec(
            f"call-{node['id']}", f"{node['tool']}_agent", (claim,), compute, reads=reads,
            duration=3.0 + (position % 3) * 1.5, tokens=(700, 180), actions=actions, goal=True,
        )

    def respond(values: dict[str, Any], _ctx) -> dict[str, Any]:
        covers = {read: values[read]["digest"] for read in response_reads}
        return {"answer": {"covers": covers, "digest": _digest(covers)}}

    response_reads = tuple(_claim(dep) for dep in motif["response_deps"] if dep in nodes)
    steps = [node_step(nodes[node_id], position) for position, node_id in enumerate(order)]
    steps.append(StepSpec(
        "response", "responder", ("answer",), respond, reads=response_reads, duration=3.0, tokens=(1200, 400),
        actions=(ActionSpec("respond", lambda v, _c: {"answer": v["answer"]["digest"]}),),
    ))

    def contracts() -> list[ActionContract]:
        out = []

        def bound(claim: str):
            def validate(arguments, evidence, _ctx):
                source = cited(evidence, claim)
                if source is None or source.value.get("digest") != arguments.get("digest", arguments.get("answer")):
                    return reject(f"effect is not bound to the cited {claim}")
                return approve(arguments)

            return validate

        for node in motif["nodes"]:
            if not node["effect"]:
                continue
            tool = node["tool"]
            irreversible = node["effect"] == "irreversible"
            out.append(ActionContract(
                tool, bound(_claim(node["id"])), resource=lambda a, tool=tool: f"{tool}:{a.get('node')}",
                semantic_key=lambda a, tool=tool: f"{tool}:{a.get('node')}:{a.get('digest')}",
                irreversible=irreversible,
                compensate=None if irreversible else (lambda a, tool=tool: (UNDO + tool, dict(a))),
            ))
            if not irreversible and not any(item.tool == UNDO + tool for item in out):
                out.append(ActionContract(UNDO + tool, lambda a, _e, _c: approve(a),
                                          resource=lambda a, tool=tool: f"{tool}:{a.get('node')}",
                                          semantic_key=lambda a, tool=tool: f"{UNDO}{tool}:{a.get('node')}:{a.get('digest')}"))
        out.append(ActionContract("respond", bound("answer"), resource=lambda _a: "user:reply",
                                  semantic_key=lambda a: f"respond:{a.get('answer')}", irreversible=True))
        return out

    def tools(world: World) -> dict[str, Any]:
        out: dict[str, Any] = {"respond": effect_tool(world, "respond", lambda _a: "user:reply", append_to("replies"))}
        for node in motif["nodes"]:
            if node["effect"]:
                tool = node["tool"]
                out[tool] = effect_tool(world, tool, lambda a, tool=tool: f"{tool}:{a['node']}", append_to(tool))
                if node["effect"] == "reversible":
                    out[UNDO + tool] = effect_tool(world, UNDO + tool, lambda a, tool=tool: f"{tool}:{a['node']}",
                                                   append_to(UNDO + tool))
        return out

    def corrector(claim_id: str, world: World, values: dict[str, Any]) -> Any:
        case = ref[0]
        for step in case.steps:
            if claim_id in step.writes and all(read in values for read in step.all_reads):
                return step.compute({read: values[read] for read in step.all_reads}, None)[claim_id]
        return None

    first, second = motif["sites"]
    tags = motif["tags"]

    def corrupt(tag: str):
        def apply(value: dict[str, Any]) -> dict[str, Any]:
            return dict(value, result=f"{value['result']} [{tag}]", digest=_digest([value["digest"], tag]))

        return apply

    faults = (
        FaultSpec("first", f"call-{first}", _claim(first), corrupt(tags[0]), "transient",
                  f"node {first} ({nodes[first]['tool']}): {tags[0]}"),
        FaultSpec("second", f"call-{second}", _claim(second), corrupt(tags[1]), "transient",
                  f"node {second} ({nodes[second]['tool']}): {tags[1]}"),
    )
    lead = Trigger("timeline", point="inflight")
    opened = {"event": "obligation_opened", "claim": _claim(first)}
    schedules = (
        Schedule("simultaneous", ("first", "second"), {"first": lead, "second": Trigger("after_event", delay=0.0, **opened)}),
        Schedule("staggered", ("first", "second"), {"first": lead, "second": Trigger("after_event", delay=1.0, **opened)}),
        Schedule("during_activation", ("first", "second"), {
            "first": lead, "second": Trigger("after_event", delay=0.0, event="activation", claim=_claim(first)),
        }),
    )
    case = CaseSpec(
        family, template,
        f"ParaRecover Level-2 record {motif['record_id']} ({motif['relation']} cones; {', '.join(tags)})",
        tuple(steps), (*(_claim(node_id) for node_id in order), "answer"), {}, {}, faults, schedules,
        _derived_checks(ref, {}), contracts, lookup_tools(tools), corrector,
        check_latency=0.5, correction_latency=2.0, suite="pararecover",
    )
    ref.append(case)
    return case


__all__ = ["MOTIFS", "SLOTS", "build", "motif_for", "motifs"]
