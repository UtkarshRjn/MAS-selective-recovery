"""Small provenance shapes used as recovery cases.

Each template is a fixed call order plus the artifacts each step reads.
A `corrupt` fault replaces one step's output. The same recovery methods used
on the booking workflow then rerun the tainted visits.

  chain     a reads nothing, then each step reads the previous one
  diamond   b and c both read a; d reads b and c
  join      p, q, and r read nothing; join reads all three
  backedge  a, b, c, then a:2 reads c. side runs after that return and reads only a

`a:2` is a second visit in the log. The edge into it is recorded once.
"""

from __future__ import annotations

import copy
from typing import Any

from .faults import FaultSpec
from .workflow import RunContext, TravelWorkflow
from .world import World

# (step key, keys it reads), in call order.
TEMPLATES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "chain": [
        ("a", ()),
        ("b", ("a",)),
        ("c", ("b",)),
        ("d", ("c",)),
    ],
    "diamond": [
        ("a", ()),
        ("b", ("a",)),
        ("c", ("a",)),
        ("d", ("b", "c")),
    ],
    "join": [
        ("p", ()),
        ("q", ()),
        ("r", ()),
        ("join", ("p", "q", "r")),
    ],
    "backedge": [
        ("a", ()),
        ("b", ("a",)),
        ("c", ("b",)),
        ("a:2", ("c",)),
        ("side", ("a",)),
    ],
}

GRAPH_METHODS = ("restart", "suffix", "coarse", "support")


def make_graph_task(template: str) -> dict[str, Any]:
    if template not in TEMPLATES:
        raise KeyError(template)
    return {"kind": "graph", "template": template}


def step_value(key: str, inputs: list[str]) -> str:
    if not inputs:
        return key
    return f"{key}({','.join(inputs)})"


def clean_values(template: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, inputs in TEMPLATES[template]:
        values[key] = step_value(key, [values[k] for k in inputs])
    return values


def check_graph(world: World, template: str) -> dict[str, Any]:
    expected = clean_values(template)
    files_ok = {key: world.files.get(f"{key}.txt") == value for key, value in expected.items()}
    return {"files_ok": files_ok, "success": all(files_ok.values())}


class GraphWorkflow:
    def run(self, ctx: RunContext) -> None:
        done: dict[str, tuple[str, str]] = {}
        for key, inputs in TEMPLATES[ctx.task["template"]]:
            done[key] = self._step(ctx, key, [done[k] for k in inputs])

    def _step(self, ctx: RunContext, key: str, inputs: list[tuple[str, str]]) -> tuple[str, str]:
        ids = [art for art, _ in inputs]
        reused = TravelWorkflow()._reuse(ctx, key, "graph", ids)
        if reused is not None:
            art, payload = reused
            value = payload["value"] if isinstance(payload, dict) else str(payload)
            return art, value
        step = ctx.prov.add_step(key, "graph")
        for art, _ in inputs:
            ctx.prov.add_input(art, step)
            ctx.prov.add_support(art, step)
        parent_values = [value for _, value in inputs]
        out = ctx.llm.chat("graph", self._messages(ctx, key, parent_values))
        value = str(out.get("value", "")).strip().replace(", ", ",")
        value, fired = self._corrupt(ctx, key, value)
        art = ctx.prov.add_artifact(
            key, {"value": value}, producer=step, faulty=fired, fault=self._fault_name(ctx) if fired else None
        )
        self._write(ctx, step, f"{key}.txt", value)
        return art, value

    def _corrupt(self, ctx: RunContext, key: str, value: str) -> tuple[str, bool]:
        inj = ctx.injector
        spec = inj.spec if inj is not None else None
        if spec is None or spec.type != "corrupt" or spec.target != key:
            return value, False
        if not spec.persistent and inj.fired:
            return value, False
        inj.fired += 1
        return "BAD", True

    def _fault_name(self, ctx: RunContext) -> str | None:
        spec = ctx.injector.spec if ctx.injector is not None else None
        return spec.name if spec is not None else None

    def _write(self, ctx: RunContext, step: str, path: str, content: str) -> None:
        _, effect = ctx.world.write_file(path, content)
        ctx.prov.add_effect(step, effect)

    def _messages(self, ctx: RunContext, key: str, inputs: list[str]) -> list[dict[str, str]]:
        from . import agents

        lines = [
            f"KEY_JSON: {agents.dumps(key)}",
            f"INPUTS_JSON: {agents.dumps(inputs)}",
            f"TASK_JSON: {agents.dumps(ctx.task)}",
        ]
        if ctx.fault_note:
            lines.append("FAULT_JSON: " + agents.dumps(ctx.fault_note))
        return [
            {
                "role": "system",
                "content": (
                    'Reply with one JSON object {"value": "..."}. '
                    "If INPUTS_JSON is empty, value is the KEY_JSON string. "
                    "Otherwise value is the key, then '(', then the inputs joined by commas, then ')'."
                ),
            },
            {"role": "user", "content": "\n".join(lines)},
        ]


def evaluate_graphs() -> list[dict[str, Any]]:
    """One corrupt fault at each step of each template, then four recovery methods.

    The mock model is enough: the tainted set is fixed by the recorded edges.
    """
    from .llm import MockLLM
    from .recovery import coarse_lineage, full_restart, suffix_recover, support_recover
    from .runner import run_workflow

    methods = {
        "restart": full_restart,
        "suffix": suffix_recover,
        "coarse": coarse_lineage,
        "support": support_recover,
    }
    rows: list[dict[str, Any]] = []
    for template in TEMPLATES:
        task = make_graph_task(template)
        for fault_at, _ in TEMPLATES[template]:
            world, prov, _, _ = run_workflow(task, ([], []), MockLLM(), FaultSpec("corrupt", fault_at, persistent=False))
            for name, fn in methods.items():
                recovered, report = _recover(fn, task, world, prov)
                rows.append({
                    "template": template,
                    "fault": fault_at,
                    "method": name,
                    "tainted": list(report.tainted_steps),
                    "steps_recomputed": report.steps_recomputed,
                    "steps_total": report.steps_total,
                    "success": check_graph(recovered, template)["success"],
                })
    return rows


def sweep_topologies(llm: Any) -> list[dict[str, Any]]:
    """Clean run and one corrupt fault at every step, then each recovery method.

    `llm` is the same client the booking experiment uses. Rows carry that client's
    token and wall-time counts.
    """
    from .coding import check_coding, make_coding_task
    from .monitor import monitor_recover, support_monitor_recover
    from .programs import PROGRAMS
    from .recovery import coarse_lineage, full_restart, suffix_recover, support_recover
    from .runner import run_workflow

    methods = {
        "restart": full_restart,
        "suffix": suffix_recover,
        "coarse": coarse_lineage,
        "support": support_recover,
        "monitor": monitor_recover,
        "support_monitor": support_monitor_recover,
    }
    cases: list[tuple[str, str | None, dict[str, Any], tuple, FaultSpec | None, Any]] = []
    for template in TEMPLATES:
        task = make_graph_task(template)
        check = lambda world, template=template: check_graph(world, template)["success"]
        cases.append((template, None, task, ([], []), None, check))
        for fault, _ in TEMPLATES[template]:
            cases.append((template, fault, task, ([], []), FaultSpec("corrupt", fault, persistent=False), check))
    for name in PROGRAMS:
        coding_task, _, catalog = make_coding_task(name)
        coding_check = lambda world, task=coding_task: check_coding(world, task)["success"]
        label = f"coding:{name}"
        cases.append((label, None, coding_task, catalog, None, coding_check))
        cases.append((
            label, "drop_requirement@spec", coding_task, catalog,
            FaultSpec("drop_requirement", "spec", persistent=True), coding_check,
        ))
    clamp_task, _, clamp_catalog = make_coding_task("clamp")
    clamp_check = lambda world, task=clamp_task: check_coding(world, task)["success"]
    cases.append((
        "coding:clamp", "bad_patch@coder", clamp_task, clamp_catalog,
        FaultSpec("bad_patch", "coder", persistent=True), clamp_check,
    ))

    rows: list[dict[str, Any]] = []
    for topology, fault, task, catalog, spec, check in cases:
        world, prov, inj, stats = run_workflow(task, catalog, llm, spec)
        base = {
            "topology": topology,
            "fault": fault,
            "fault_fired": inj.fired > 0,
            "steps_total": len(prov.of_kind("step")),
        }
        if spec is None:
            rows.append({
                **base,
                "method": "clean",
                "success": bool(check(world)),
                "steps_recomputed": base["steps_total"],
                "steps_reused": 0,
                "tainted_steps": [],
                "usage": stats["usage"],
                "wall_s": stats["wall_s"],
            })
            print(f"{topology:10} clean success={check(world)} wall={stats['wall_s']}s", flush=True)
            continue
        faulty_ok = bool(check(world))
        rows.append({
            **base,
            "method": "none",
            "success": faulty_ok,
            "steps_recomputed": 0,
            "steps_reused": 0,
            "tainted_steps": [],
            "usage": stats["usage"],
            "wall_s": stats["wall_s"],
        })
        print(f"{topology:10} {fault} fired={inj.fired > 0} broken={not faulty_ok}", flush=True)
        for name, fn in methods.items():
            recovered, report = _recover(fn, task, world, prov, llm)
            rows.append({
                **base,
                "method": name,
                "success": bool(check(recovered)),
                "steps_recomputed": report.steps_recomputed,
                "steps_reused": report.steps_reused,
                "tainted_steps": list(report.tainted_steps),
                "recovery_usage": report.usage,
                "recovery_wall_s": round(report.wall_s, 3),
                "usage": stats["usage"],
                "wall_s": stats["wall_s"],
            })
            print(
                f"  {name:16} {report.steps_recomputed}/{report.steps_total} "
                f"success={check(recovered)} wall={report.wall_s:.1f}s",
                flush=True,
            )
    return rows


def _recover(fn: Any, task: dict[str, Any], world: World, prov: Any, llm: Any | None = None) -> tuple[World, Any]:
    import copy

    from .llm import MockLLM

    world, prov = copy.deepcopy(world), copy.deepcopy(prov)
    report = fn(task, world, llm or MockLLM(), prov)
    return world, report


def format_table(rows: list[dict[str, Any]]) -> str:
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["template"], row["fault"]), {})[row["method"]] = row
    header = "| template | fault | " + " | ".join(GRAPH_METHODS) + " |"
    rule = "|---|---|" + "|".join("---" for _ in GRAPH_METHODS) + "|"
    lines = [header, rule]
    for (template, fault), cells in grouped.items():
        cols = []
        for method in GRAPH_METHODS:
            cell = cells[method]
            mark = "ok" if cell["success"] else "stale"
            cols.append(f"{' '.join(cell['tainted'])} · {cell['steps_recomputed']}/{cell['steps_total']} {mark}")
        lines.append(f"| {template} | {fault} | " + " | ".join(cols) + " |")
    return "\n".join(lines)
