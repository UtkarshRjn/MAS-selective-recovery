"""Coding agents for the pilot.

`ReferenceAgent` returns the upstream implementation of its writable files. It
is deterministic and spends no tokens; it validates the pipeline (clean runs
complete, interventions are detected and repaired) and is never reported as a
model condition.

`ModelAgent` is an iterative edit-and-test loop over an OpenAI-compatible
endpoint. It sees what the official assignment gives a specialist: the task
statement, its role, its writable paths, its primary test targets, the files
in its workspace, and the upstream artifacts handed to it. It does not see the
dependency-point contracts or probe lists, which are evaluator material.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from ..llm import TRANSPORT_FAILURE
from .registry import Assignment, TaskRecord
from .workspace import RepoCache, write_files

Meter = Callable[[int, int, float, float], None]


@dataclass
class SolveRequest:
    task: TaskRecord
    assignment: Assignment
    workspace: Path
    upstream: list[dict[str, Any]]
    attempt: int
    seed: int
    previous: dict[str, str] | None = None
    feedback: str = ""


class CodingAgent(Protocol):
    name: str

    def solve(self, request: SolveRequest, meter: Meter | None) -> dict[str, str]: ...


@dataclass
class ReferenceAgent:
    cache: RepoCache
    name: str = "reference"

    def solve(self, request: SolveRequest, meter: Meter | None) -> dict[str, str]:
        files = self.cache.reference_files(request.task, request.assignment.writable_paths)
        for path in request.assignment.writable_paths:
            if path not in files:
                target = request.workspace / path
                files[path] = target.read_text() if target.exists() else ""
        return files


_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def extract_code(text: str) -> str | None:
    blocks = _BLOCK.findall(text)
    if not blocks:
        return None
    return max(blocks, key=len)


RETRY_DELAYS_S = (5.0, 20.0)


def _transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def call_model(llm: Any, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, int, int]:
    """One request; returns (text, prompt tokens, completion tokens) for this call alone.

    Timeouts, dropped connections, 429 and 5xx are retried; if they persist the error names
    `TRANSPORT_FAILURE` so the run is scored as an infrastructure error, not a model outcome.
    """
    if hasattr(llm, "client") and hasattr(llm, "_consume"):
        body = llm._body(messages, json_mode=False, max_tokens=max_tokens)
        for delay in (*RETRY_DELAYS_S, None):
            started = time.perf_counter()
            try:
                response = llm.client.post("/chat/completions", json=body)
                response.raise_for_status()
            except Exception as exc:
                if not _transient(exc):
                    raise
                if delay is None:
                    raise RuntimeError(f"{TRANSPORT_FAILURE}: {exc!r}") from exc
                time.sleep(delay)
                continue
            text, usage = llm._consume(response.json(), started)
            return text, int(usage["prompt_tokens"]), int(usage["completion_tokens"])
    text = llm.complete(messages, max_tokens=max_tokens)
    return text, sum(len(item["content"]) for item in messages) // 4, len(text) // 4


@dataclass
class ModelAgent:
    llm_factory: Callable[[int], Any]
    executor: Any
    name: str = "model"
    steps: int = 3
    tests: int = 3
    token_limit: int = 60000
    max_tokens: int = 8192
    context_files: int = 4
    context_chars: int = 24000
    usage: dict[str, int] = field(default_factory=dict)

    def _context(self, request: SolveRequest) -> str:
        parts = []
        for patch in request.upstream:
            for path, text in patch["files"].items():
                parts.append(f"# Upstream artifact {patch['digest']} from {patch['agent']}: {path}\n{text}")
        budget = self.context_chars
        out = []
        for part in parts[: self.context_files]:
            out.append(part[:budget])
            budget -= len(out[-1])
            if budget <= 0:
                break
        return "\n\n".join(out)

    def _messages(self, request: SolveRequest, path: str, current: str, feedback: str) -> list[dict[str, str]]:
        assignment = request.assignment
        system = (
            f"You are the {assignment.role} on a team implementing a Python repository. "
            f"You may change only these files: {', '.join(assignment.writable_paths)}. "
            "Do not change tests. Write a working implementation from the task, the tests and the files "
            "you are given; do not try to reproduce the original library's source from memory. Reply with "
            "the complete new content of the requested file in a single ```python code block and nothing else."
        )
        user = [
            f"Task:\n{request.task.problem_statement}",
            f"Your primary tests: {', '.join(assignment.primary_tests)}",
        ]
        context = self._context(request)
        if context:
            user.append(f"Artifacts handed to you by upstream specialists:\n{context}")
        user.append(f"Current content of {path}:\n```python\n{current}\n```")
        if feedback:
            user.append(f"Your last test run failed:\n{feedback[-4000:]}")
        user.append(f"Write the complete new {path}.")
        return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]

    def solve(self, request: SolveRequest, meter: Meter | None) -> dict[str, str]:
        llm = self.llm_factory(request.seed * 1000 + request.attempt)
        files = dict(request.previous or {})
        for path in request.assignment.writable_paths:
            if path not in files:
                target = request.workspace / path
                files[path] = target.read_text() if target.exists() else ""
        spent = 0
        feedback = request.feedback
        tests_left = self.tests
        with tempfile.TemporaryDirectory(prefix="agentrec-agent-") as tmp:
            scratch = Path(tmp) / "ws"
            shutil.copytree(request.workspace, scratch, ignore=shutil.ignore_patterns(".git"))
            for _step in range(self.steps):
                for path in request.assignment.writable_paths:
                    if spent >= self.token_limit:
                        break
                    messages = self._messages(request, path, files[path], feedback)
                    started = time.monotonic()
                    text, prompt, completion = call_model(llm, messages, self.max_tokens)
                    ended = time.monotonic()
                    spent += prompt + completion
                    if meter is not None:
                        meter(prompt, completion, started, ended)
                    code = extract_code(text)
                    if code is not None:
                        files[path] = code if code.endswith("\n") else code + "\n"
                write_files(scratch, files)
                if tests_left <= 0 or spent >= self.token_limit:
                    break
                tests_left -= 1
                run = self.executor.run(request.task, scratch, request.assignment.primary_tests)
                if not run.failed and run.passed:
                    break
                feedback = run.output
        self.usage = {"tokens": spent}
        return files


__all__ = ["CodingAgent", "ModelAgent", "ReferenceAgent", "SolveRequest", "call_model", "extract_code"]
