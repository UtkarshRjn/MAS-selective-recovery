"""The upstream WorkBench checkout: its tools, its tasks, and its own outcome scoring.

WorkBench is used as released: lookups call its read tools on its sandbox
databases, and final outcomes are scored with its `is_correct` (state
comparison after replaying the actions) and `has_side_effects`. Only the
decomposition into concurrent agents, the faults, and the monitor are ours.
"""

from __future__ import annotations

import csv
import importlib
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

TASK_FILE = "data/processed/tasks_and_outcomes/multi_domain_tasks_and_outcomes.csv"
CURRENT_TIME = "2023-11-30 00:00:00"
TODAY = "2023-11-30"
_load_lock = threading.Lock()


class WorkBenchError(RuntimeError):
    pass


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@dataclass(frozen=True)
class Task:
    index: int
    task: str
    outcome: tuple[str, ...]
    base_template: str
    chosen_template: str
    domains: str


class WorkBench:
    """One pinned WorkBench checkout, imported in-process."""

    def __init__(self, root: str | Path, *, commit: str | None = None) -> None:
        self.root = Path(root).resolve()
        if not (self.root / TASK_FILE).exists():
            raise WorkBenchError(f"{self.root} is not a WorkBench checkout")
        self.commit = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
        if commit is not None and commit != self.commit:
            raise WorkBenchError(f"WorkBench is at {self.commit}, the frozen configuration requires {commit}")
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        with _load_lock, _cwd(self.root):
            self.state = importlib.import_module("src.tools.state")
            self.toolkits = importlib.import_module("src.tools.toolkits")
            self.evaluation = importlib.import_module("src.evals.evaluation")
            self.actions = importlib.import_module("src.evals.actions")
            self.state._pristine_state()
        self.tools = {tool.name: tool for tool in self.toolkits.all_tools}
        self.side_effect_tools = {tool.name for tool in self.toolkits.tools_with_side_effects}

    def identity(self) -> dict[str, Any]:
        return {"root": str(self.root), "commit": self.commit, "task_file": TASK_FILE}

    def tasks(self) -> list[Task]:
        with (self.root / TASK_FILE).open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        out = []
        for index, row in enumerate(rows):
            outcome = tuple(_parse_outcome(row["outcome"]))
            out.append(Task(index, row["task"], outcome, row["base_template"], row["chosen_template"], row["domains"]))
        return out

    def call(self, tool_name: str, /, **arguments: Any) -> Any:
        """Run one read tool on the pristine sandbox and decode its JSON result."""
        if tool_name in self.side_effect_tools:
            raise WorkBenchError(f"{tool_name} has side effects; effects go through the gateway")
        tool = self.tools[tool_name]
        raw = tool.func(**{key: value for key, value in arguments.items() if value is not None})
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    def describe(self, names: tuple[str, ...]) -> str:
        lines = []
        for name in names:
            tool = self.tools[name]
            summary = (tool.description or "").strip().split("\n\n")[0]
            lines.append(f"- {tool.signature_str}: {summary}")
        return "\n".join(lines)

    def score(self, actions: list[str], ground_truth: list[str]) -> dict[str, bool]:
        """WorkBench's own outcome metrics for a final action list."""
        with _load_lock:
            correct = bool(self.evaluation.is_correct(list(actions), list(ground_truth), ""))
            side_effects = bool(self.evaluation.has_side_effects(list(actions), correct))
            exact = bool(self.evaluation.is_exact_match(list(actions), list(ground_truth)))
        return {"workbench_correct": correct, "workbench_side_effects": side_effects, "workbench_exact": exact}


def _parse_outcome(text: str) -> list[str]:
    import ast

    value = ast.literal_eval(text)
    return [str(item) for item in value]


def action_string(tool: str, arguments: dict[str, Any]) -> str:
    """The WorkBench action string for a tool call, as its evaluator replays it."""
    def escape(value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    body = ", ".join(f'{key}="{escape(value)}"' for key, value in arguments.items())
    return f"{tool}.func({body})"


def parse_action(action: str) -> tuple[str, dict[str, str]]:
    import ast

    call = ast.parse(action, mode="eval").body
    parts = []
    node = call.func
    while hasattr(node, "attr"):
        parts.append(node.attr)
        node = node.value
    parts.append(node.id)
    parts.reverse()
    if parts[-1] == "func":
        parts = parts[:-1]
    return ".".join(parts), {kw.arg: str(ast.literal_eval(kw.value)) for kw in call.keywords}


__all__ = ["CURRENT_TIME", "TODAY", "Task", "WorkBench", "WorkBenchError", "action_string", "parse_action"]
