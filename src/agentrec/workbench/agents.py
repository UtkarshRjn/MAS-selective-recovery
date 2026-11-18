"""Agents for the WorkBench pilot.

`ReferenceAgent` runs the frozen derivation of each lookup and the frozen
decision rule. It is deterministic and spends no tokens; it validates the
pipeline and is never reported as a model condition.

`ModelAgent` answers a lookup with a tool loop over the WorkBench read tools
that lookup may use, and makes the decision from the task text and the lookup
answers it was handed. Prompts carry WorkBench's own date/time prefix. Agents
never see ground-truth outcomes, the checker, or the decision rule.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..codepilot.agents import call_model
from .source import WorkBench
from .templates import Lookup, Template

Meter = Callable[[int, int, float, float], None]
Effects = list[tuple[str, dict[str, str]]]

DATETIME_PREFIX = (
    "Today's date is Thursday, 2023-11-30 and the current time is 00:00:00. "
    "Remember the current date and time when completing tasks. "
    "Meetings must not start before 9am or end after 6pm."
)


@dataclass
class LookupRequest:
    task: str
    template: Template
    lookup: Lookup
    params: dict[str, Any]
    inputs: dict[str, Any]
    attempt: int
    seed: int
    feedback: str = ""


@dataclass
class DecideRequest:
    task: str
    template: Template
    params: dict[str, Any]
    found: dict[str, Any]
    attempt: int
    seed: int
    feedback: str = ""


class WorkBenchAgent(Protocol):
    name: str

    def lookup(self, request: LookupRequest, meter: Meter | None) -> Any: ...

    def decide(self, request: DecideRequest, meter: Meter | None) -> Effects: ...


def fill(text: str, params: dict[str, Any], inputs: dict[str, Any]) -> str:
    def value(match: re.Match[str]) -> str:
        path = match.group(1).split(".")
        current: Any = inputs if path[0] in inputs else params
        for part in path:
            current = current.get(part) if isinstance(current, dict) else None
        return str(current)

    return re.sub(r"\{([\w.]+)\}", value, text)


@dataclass
class ReferenceAgent:
    wb: WorkBench
    name: str = "reference"

    def lookup(self, request: LookupRequest, meter: Meter | None) -> Any:
        return request.lookup.derive(self.wb, request.params, request.inputs)

    def decide(self, request: DecideRequest, meter: Meter | None) -> Effects:
        return request.template.decide(request.params, request.found)


_JSON = re.compile(r"\{.*\}|\[.*\]", re.S)


def parse_reply(text: str) -> tuple[str, Any] | None:
    """`CALL {...}` or `ANSWER {...}` / `ANSWER [...]`; the last such line wins."""
    for keyword in ("ANSWER", "CALL"):
        index = text.rfind(keyword)
        if index < 0:
            continue
        match = _JSON.search(text[index + len(keyword):])
        if match is None:
            continue
        try:
            return keyword, json.loads(match.group(0))
        except ValueError:
            continue
    return None


@dataclass
class ModelAgent:
    llm_factory: Callable[[int], Any]
    wb: WorkBench
    name: str = "model"
    max_turns: int = 8
    max_tokens: int = 1024
    token_limit: int = 30000
    result_chars: int = 4000
    usage: dict[str, int] = field(default_factory=dict)

    def _ask(self, llm: Any, messages: list[dict[str, str]], meter: Meter | None) -> tuple[str, int]:
        started = time.monotonic()
        text, prompt, completion = call_model(llm, messages, self.max_tokens)
        if meter is not None:
            meter(prompt, completion, started, time.monotonic())
        return text, prompt + completion

    def lookup(self, request: LookupRequest, meter: Meter | None) -> Any:
        lookup = request.lookup
        llm = self.llm_factory(request.seed * 1000 + request.attempt)
        system = (
            f"{DATETIME_PREFIX}\nYou are one agent on a team. Answer one question using these read-only tools:\n"
            f"{self.wb.describe(lookup.tools)}\n\nTo call a tool reply with exactly one line\n"
            'CALL {"tool": "<name>", "arguments": {...}}\n'
            f"When you know the answer reply with one line\nANSWER {lookup.answer_format}"
        )
        user = [f"Team task: {request.task}", f"Your question: {fill(lookup.instruction, request.params, request.inputs)}"]
        if request.feedback:
            user.append(request.feedback)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]
        spent = 0
        for _turn in range(self.max_turns):
            if spent >= self.token_limit:
                break
            text, used = self._ask(llm, messages, meter)
            spent += used
            messages.append({"role": "assistant", "content": text})
            reply = parse_reply(text)
            if reply is None:
                messages.append({"role": "user", "content": "Reply with one CALL line or one ANSWER line."})
                continue
            kind, payload = reply
            if kind == "ANSWER":
                self.usage = {"tokens": spent}
                return payload
            tool = payload.get("tool") if isinstance(payload, dict) else None
            if tool not in lookup.tools:
                result = f"Unknown tool {tool}; use one of {', '.join(lookup.tools)}."
            else:
                try:
                    result = json.dumps(self.wb.call(tool, **dict(payload.get("arguments") or {})), default=str)
                except Exception as exc:  # noqa: BLE001 - tool errors go back to the agent
                    result = f"Error: {exc}"
            messages.append({"role": "user", "content": f"Result: {result[: self.result_chars]}"})
        self.usage = {"tokens": spent}
        return {"error": "no answer"}

    def decide(self, request: DecideRequest, meter: Meter | None) -> Effects:
        template = request.template
        llm = self.llm_factory(request.seed * 1000 + 500 + request.attempt)
        system = (
            f"{DATETIME_PREFIX}\nYou are the team member who acts. Teammates have already looked things up. "
            f"Decide which of these actions the task requires, with exact arguments:\n"
            f"{self.wb.describe(template.effect_tools)}\n\nReply with one line\n"
            'ANSWER [{"tool": "<name>", "arguments": {...}}, ...]\n'
            "using an empty list when no action is required."
        )
        user = [f"Task: {request.task}", "Teammates found:\n" + json.dumps(request.found, indent=1, default=str)]
        if request.feedback:
            user.append(request.feedback)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user)}]
        spent = 0
        for _turn in range(2):
            text, used = self._ask(llm, messages, meter)
            spent += used
            reply = parse_reply(text)
            if reply is not None and reply[0] == "ANSWER" and isinstance(reply[1], list):
                self.usage = {"tokens": spent}
                return [
                    (str(item.get("tool")), {str(k): str(v) for k, v in dict(item.get("arguments") or {}).items()})
                    for item in reply[1] if isinstance(item, dict)
                ]
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "Reply with one ANSWER line holding a JSON list."}]
        self.usage = {"tokens": spent}
        return []


__all__ = ["DATETIME_PREFIX", "DecideRequest", "LookupRequest", "ModelAgent", "ReferenceAgent", "WorkBenchAgent",
           "fill", "parse_reply"]
