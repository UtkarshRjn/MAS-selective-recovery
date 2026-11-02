"""Coding team for a hidden test suite.

Lap 1 writes a draft. Lap 2 writes the file the checker executes. Docs sits
between the laps and reads only the first spec. A dropped requirement is marked
on that first spec. Support follows one evidence hop and rewrites the draft.
The submitted file stays, so the suite still fails. Coarse follows the tester
and the review into the second coder and rewrites the submitted file.
"""

from __future__ import annotations

from typing import Any

from . import agents
from .programs import PROGRAMS, broken, run_suite
from .workflow import RunContext, TravelWorkflow
from .world import World

_CODING_SYSTEM = {
    "spec": (
        'Reply with {"contract": "..."}. '
        "Do not write Python. Copy every requirement sentence from the user request. "
        "On visit 2, keep those sentences and answer the review note."
    ),
    "coder": (
        'Reply with {"content": "..."}. '
        "content is one complete Python function. Encode newlines as \\n. "
        "The function name and parameters are the signature in the task. "
        "Implement every sentence in the contract and nothing else."
    ),
    "reviewer": (
        'Reply with {"decision": "revise" or "approve", "note": "..."}. '
        "Describe any failing checks in the report."
    ),
    "docs": (
        'Reply with {"content": "..."}. '
        "Write a short README from the contract only."
    ),
}


def make_coding_task(name: str = "clamp") -> tuple[dict[str, Any], dict[str, Any], tuple[list, list]]:
    program = PROGRAMS[name]
    task = {
        "kind": "coding",
        "name": name,
        "request": program["request"],
        "signature": program["signature"],
        "entry": program["entry"],
        "draft_path": f"{name}_draft.py",
        "path": f"{name}.py",
        "doc_path": "README.md",
    }
    truth = {"name": name, "path": task["path"], "doc_path": task["doc_path"]}
    return task, truth, ([], [])


def check_coding(world: World, task: dict[str, Any]) -> dict[str, Any]:
    report = run_suite(world.files.get(task["path"], ""), task["name"])
    readme = world.files.get(task["doc_path"], "")
    doc_ok = task["name"] in readme
    return {**report, "tests_ok": report["passed"], "doc_ok": doc_ok, "success": report["passed"]}


class CodingWorkflow:
    def run(self, ctx: RunContext) -> None:
        spec_id, spec = self._spec(ctx, "spec", 1, None)
        code_id, _ = self._coder(ctx, "coder", spec_id, spec)
        report_id, report = self._tester(ctx, "tester", ctx.task["draft_path"], code_id)
        review_id, _ = self._reviewer(ctx, "reviewer", spec_id, report_id, report)
        self._docs(ctx, "docs", spec_id, spec)
        spec2_id, spec2 = self._spec(ctx, "spec:2", 2, review_id)
        code2_id, _ = self._coder(ctx, "coder:2", spec2_id, spec2)
        report2_id, report2 = self._tester(ctx, "tester:2", ctx.task["path"], code2_id)
        self._reviewer(ctx, "reviewer:2", spec2_id, report2_id, report2)

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
        contract, fired = self._drop_requirement(ctx, key, contract)
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
        path = ctx.task["draft_path"] if key == "coder" else ctx.task["path"]
        code = {"path": path, "content": content}
        art = ctx.prov.add_artifact(
            key, code, producer=step, faulty=fired, fault=self._fault_name(ctx) if fired else None
        )
        self._write(ctx, step, path, content)
        return art, code

    def _tester(self, ctx: RunContext, key: str, path: str, code_id: str) -> tuple[str, dict[str, Any]]:
        reused = self._reuse(ctx, key, "tester", [code_id])
        if reused is not None:
            return reused
        step = ctx.prov.add_step(key, "tester")
        ctx.prov.add_input(code_id, step)
        ctx.prov.add_support(code_id, step)
        report = run_suite(ctx.world.files.get(path, ""), ctx.task["name"])
        art = ctx.prov.add_artifact(key, report, producer=step)
        return art, report

    def _reviewer(
        self,
        ctx: RunContext,
        key: str,
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
        ctx.llm.chat("reviewer", self._messages(ctx, "reviewer", 1 if key == "reviewer" else 2, report=report))
        review = {
            "decision": "approve" if report["passed"] else "revise",
            "note": "" if report["passed"] else _failure_note(report),
        }
        art = ctx.prov.add_artifact(key, review, producer=step)
        return art, review

    def _docs(self, ctx: RunContext, key: str, spec_id: str, spec: dict[str, Any]) -> None:
        if self._reuse(ctx, key, "docs", [spec_id]) is not None:
            return
        step = ctx.prov.add_step(key, "docs")
        ctx.prov.add_input(spec_id, step)
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
            matches = key == "coder" or key.startswith("coder:")
        elif fault_type == "drop_requirement":
            matches = key == "spec" or key.startswith("spec:")
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
        return broken(ctx.task["name"]), True

    def _drop_requirement(self, ctx: RunContext, key: str, contract: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not self._armed(ctx, "drop_requirement", key):
            return contract, False
        # Hand the coder the requirements with one sentence removed. The model's
        # own wording is not searched, so a paraphrase cannot keep the requirement.
        # Only the first spec is marked, so one support hop stops at the draft.
        program = PROGRAMS[ctx.task["name"]]
        kept = [item["text"] for item in program["requirements"] if item["id"] != program["drop"]]
        return {"text": " ".join(kept)}, key == "spec"

    def _fault_name(self, ctx: RunContext) -> str | None:
        spec = ctx.injector.spec if ctx.injector is not None else None
        return spec.name if spec is not None else None

    def _value(self, ctx: RunContext, nid: str | None) -> Any:
        if nid is None:
            return None
        return ctx.prov.nodes[nid].data.get("value")

    def _messages(self, ctx: RunContext, role: str, visit: int, **fields: Any) -> list[dict[str, str]]:
        task = ctx.task if role != "coder" else _coder_view(ctx.task)
        lines = [
            f"VISIT_JSON: {agents.dumps(visit)}",
            f"TASK_JSON: {agents.dumps(task)}",
        ]
        for key, value in fields.items():
            if value is not None:
                lines.append(f"{key.upper()}_JSON: {agents.dumps(value)}")
        if ctx.fault_note:
            lines.append("FAULT_JSON: " + agents.dumps(ctx.fault_note))
        return [
            {"role": "system", "content": _CODING_SYSTEM[role]},
            {"role": "user", "content": "\n".join(lines)},
        ]


def _coder_view(task: dict[str, Any]) -> dict[str, Any]:
    return {key: task[key] for key in ("kind", "name", "signature") if key in task}


def _failure_note(report: dict[str, Any]) -> str:
    parts = []
    for failure in report.get("failures", []):
        if "error" in failure and "args" not in failure:
            parts.append(failure["error"])
        else:
            parts.append(f"args {failure.get('args')} expected {failure.get('expect')} got {failure.get('got', failure.get('error'))}")
    return "; ".join(parts)
