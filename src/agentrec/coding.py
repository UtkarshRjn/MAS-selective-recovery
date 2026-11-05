"""Coding workflow: spec, coder, tester, reviewer, then the same lap again.

The reviewer asks for a revision, so the log contains two visits of each agent.
Docs sits between the two laps and reads only the first spec. A bad first patch
is earlier than docs, so a suffix rerun redoes docs, while coarse lineage does
not: docs never reads the patch. A bad revised spec is later than docs, so both
methods keep docs and the first lap.
"""

from __future__ import annotations

from typing import Any

from . import agents
from .workflow import RunContext, TravelWorkflow
from .world import World

BUGGY = "def clamp(x, lo, hi):\n    return x\n"
THIN_CONTRACT = "return the input unchanged"
CLAMP = "def clamp(x, lo, hi):\n    return min(max(x, lo), hi)\n"
CLAMP_PRE = "def clamp(x, lo, hi):\n    # lo <= hi\n    return min(max(x, lo), hi)\n"

_CODING_SYSTEM = {
    "spec": (
        'Reply with {"contract": "..."}. '
        'Visit 1 text is "clamp <name> into [lo, hi]". '
        'Visit 2 appends a newline and "precondition: <precondition>" from the task.'
    ),
    "coder": (
        'Reply with {"path": "<task path>", "content": "<python>"}. '
        f'If the contract contains "{THIN_CONTRACT}", content is {BUGGY!r}. '
        f'If the contract contains the task precondition, content is {CLAMP_PRE!r}. '
        f'Otherwise content is {CLAMP!r}.'
    ),
    "tester": (
        'Reply with {"passed": true} or {"passed": false}. '
        'passed is true only when FILE_JSON content contains "min(max(x, lo), hi)" '
        'and does not contain "return x\\n".'
    ),
    "reviewer": (
        'Reply with {"decision": "...", "note": "..."}. '
        'Visit 1: decision "revise", note "require the precondition". '
        'Visit 2: decision "approve" when REPORT_JSON passed is true, otherwise "revise". Note is empty.'
    ),
    "docs": 'Reply with {"content": "<task name>\\n"} using the name in TASK_JSON.',
}


def make_coding_task() -> tuple[dict[str, Any], dict[str, Any], tuple[list, list]]:
    task = {
        "kind": "coding",
        "name": "clamp",
        "path": "clamp.py",
        "doc_path": "README.md",
        "precondition": "lo <= hi",
    }
    truth = {"name": task["name"], "path": task["path"], "doc_path": task["doc_path"]}
    return task, truth, ([], [])


def check_coding(world: World, task: dict[str, Any]) -> dict[str, Any]:
    src = world.files.get(task["path"], "")
    readme = world.files.get(task["doc_path"], "")
    tests_ok = "min(max(x, lo), hi)" in src and "return x\n" not in src
    doc_ok = task["name"] in readme
    return {"tests_ok": tests_ok, "doc_ok": doc_ok, "success": tests_ok and doc_ok}


class CodingWorkflow:
    def run(self, ctx: RunContext) -> None:
        ctx.world.files.setdefault(ctx.task["path"], BUGGY)
        spec_id, spec = self._spec(ctx, "spec", 1, None)
        code_id, _ = self._coder(ctx, "coder", spec_id, spec)
        report_id, report = self._tester(ctx, "tester", code_id)
        review_id, _ = self._reviewer(ctx, "reviewer", 1, spec_id, report_id, report)
        self._docs(ctx, "docs", spec_id, spec)
        spec2_id, spec2 = self._spec(ctx, "spec:2", 2, review_id)
        code2_id, _ = self._coder(ctx, "coder:2", spec2_id, spec2)
        report2_id, report2 = self._tester(ctx, "tester:2", code2_id)
        self._reviewer(ctx, "reviewer:2", 2, spec2_id, report2_id, report2)

    def _spec(
        self, ctx: RunContext, key: str, visit: int, review_id: str | None
    ) -> tuple[str, dict[str, Any]]:
        inputs = [review_id] if review_id else []
        reused = self._reuse(ctx, key, "spec", inputs)
        if reused is not None:
            return reused
        step = ctx.prov.add_step(key, "spec")
        if review_id:
            ctx.prov.add_input(review_id, step)
            ctx.prov.add_support(review_id, step)
        out = ctx.llm.chat("spec", self._messages(ctx, "spec", visit, review=self._value(ctx, review_id)))
        contract = {"text": str(out.get("contract", ""))}
        contract, fired = self._thin_spec(ctx, key, contract)
        art = ctx.prov.add_artifact(
            key, contract, producer=step, faulty=fired, fault=self._fault_name(ctx) if fired else None
        )
        return art, contract

    def _coder(
        self, ctx: RunContext, key: str, spec_id: str, spec: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        reused = self._reuse(ctx, key, "coder", [spec_id])
        if reused is not None:
            return reused
        step = ctx.prov.add_step(key, "coder")
        ctx.prov.add_input(spec_id, step)
        ctx.prov.add_support(spec_id, step)
        out = ctx.llm.chat("coder", self._messages(ctx, "coder", 1 if key == "coder" else 2, contract=spec))
        content, fired = self._bad_patch(ctx, key, str(out.get("content", "")))
        code = {"path": ctx.task["path"], "content": content}
        art = ctx.prov.add_artifact(
            key, code, producer=step, faulty=fired, fault=self._fault_name(ctx) if fired else None
        )
        self._write(ctx, step, ctx.task["path"], content)
        return art, code

    def _tester(self, ctx: RunContext, key: str, code_id: str) -> tuple[str, dict[str, Any]]:
        reused = self._reuse(ctx, key, "tester", [code_id])
        if reused is not None:
            return reused
        step = ctx.prov.add_step(key, "tester")
        ctx.prov.add_input(code_id, step)
        ctx.prov.add_support(code_id, step)
        src = ctx.world.files.get(ctx.task["path"], "")
        out = ctx.llm.chat("tester", self._messages(ctx, "tester", 1 if key == "tester" else 2, file={"content": src}))
        report = {"passed": bool(out.get("passed"))}
        art = ctx.prov.add_artifact(key, report, producer=step)
        return art, report

    def _reviewer(
        self,
        ctx: RunContext,
        key: str,
        visit: int,
        spec_id: str,
        report_id: str,
        report: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        reused = self._reuse(ctx, key, "reviewer", [spec_id, report_id])
        if reused is not None:
            return reused
        step = ctx.prov.add_step(key, "reviewer")
        ctx.prov.add_input(spec_id, step)
        ctx.prov.add_input(report_id, step)
        ctx.prov.add_support(report_id, step)
        out = ctx.llm.chat(
            "reviewer",
            self._messages(ctx, "reviewer", visit, report=report, contract=self._value(ctx, spec_id)),
        )
        review = {"decision": out.get("decision", "revise"), "note": str(out.get("note", ""))}
        art = ctx.prov.add_artifact(key, review, producer=step)
        return art, review

    def _docs(self, ctx: RunContext, key: str, spec_id: str, spec: dict[str, Any]) -> None:
        if self._reuse(ctx, key, "docs", [spec_id]) is not None:
            return
        step = ctx.prov.add_step(key, "docs")
        ctx.prov.add_input(spec_id, step)
        ctx.prov.add_support(spec_id, step)
        out = ctx.llm.chat("docs", self._messages(ctx, "docs", 1, contract=spec))
        content = str(out.get("content", ""))
        ctx.prov.add_artifact(key, {"path": ctx.task["doc_path"], "content": content}, producer=step)
        self._write(ctx, step, ctx.task["doc_path"], content)

    def _reuse(self, ctx: RunContext, key: str, agent: str, inputs: list[str]) -> tuple[str, Any] | None:
        found = TravelWorkflow()._reuse(ctx, key, agent, [i for i in inputs if i])
        if found is None:
            return None
        art, value = found
        if not isinstance(value, dict):
            return None
        return art, value

    def _write(self, ctx: RunContext, step: str, path: str, content: str) -> None:
        _, effect = ctx.world.write_file(path, content)
        ctx.prov.add_effect(step, effect)

    def _armed(self, ctx: RunContext, fault_type: str, key: str) -> bool:
        inj = ctx.injector
        spec = inj.spec if inj is not None else None
        if spec is None or spec.type != fault_type:
            return False
        if fault_type == "bad_patch":
            matches = spec.target == "coder" and key.startswith("coder")
        else:
            matches = spec.target == key
        if not matches:
            return False
        if not spec.persistent and inj.fired:
            return False
        inj.fired += 1
        return True

    def _bad_patch(self, ctx: RunContext, key: str, content: str) -> tuple[str, bool]:
        if not self._armed(ctx, "bad_patch", key):
            return content, False
        return BUGGY, True

    def _thin_spec(self, ctx: RunContext, key: str, contract: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not self._armed(ctx, "thin_spec", key):
            return contract, False
        return {"text": THIN_CONTRACT}, True

    def _fault_name(self, ctx: RunContext) -> str | None:
        spec = ctx.injector.spec if ctx.injector is not None else None
        return spec.name if spec is not None else None

    def _value(self, ctx: RunContext, nid: str | None) -> Any:
        if nid is None:
            return None
        return ctx.prov.nodes[nid].data.get("value")

    def _messages(self, ctx: RunContext, role: str, visit: int, **fields: Any) -> list[dict[str, str]]:
        lines = [f"VISIT_JSON: {agents.dumps(visit)}", f"TASK_JSON: {agents.dumps(ctx.task)}"]
        for key, value in fields.items():
            if value is not None:
                lines.append(f"{key.upper()}_JSON: {agents.dumps(value)}")
        if ctx.fault_note:
            lines.append("FAULT_JSON: " + agents.dumps(ctx.fault_note))
        return [
            {"role": "system", "content": _CODING_SYSTEM[role]},
            {"role": "user", "content": "\n".join(lines)},
        ]
