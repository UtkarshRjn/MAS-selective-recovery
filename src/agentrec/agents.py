"""Prompts and message builders for the researcher, planner, executor, and verifier.

Structured payloads are placed on single lines of the form `KEY_JSON: {...}` so that
both the model and the mock LLM can read them unambiguously.
"""

from __future__ import annotations

import json
from typing import Any

Messages = list[dict[str, str]]


def dumps(x: Any) -> str:
    return json.dumps(x, separators=(", ", ": "))


def request_text(task: dict[str, Any]) -> str:
    return (
        f"Book the cheapest flight from {task['origin']} to {task['dest']} on {task['date']}, and the "
        f"cheapest hotel in {task['dest']} with a rating of at least {task['min_rating']} and a nightly "
        f"price of at most ${task['max_price']}, for {task['nights']} nights. Save the itinerary, "
        f"including the flight ID and hotel ID, to itinerary.txt, and email the same itinerary text "
        f"to {task['user_email']}."
    )


RESEARCHER_SYSTEM = """You are a travel research agent. Use the tools to gather data, then report findings.
Respond with exactly one JSON object per turn and nothing else.
To call a tool: {{"action": "<tool name>", "args": {{...}}}}
When finished: {{"action": "final", "findings": <object matching FINDINGS_FORMAT>}}
Tools:
{tools}
Tool results arrive in messages starting with "OBSERVATION:". Base your findings only on tool results.
Once a search has returned results, do not repeat it: respond with your final findings.
Use "notes" for brief remarks relevant to the booking; it may be empty."""

SUBTASKS: dict[str, dict[str, Any]] = {
    "research_flights": {
        "tools": (
            "- search_flights(origin, dest, date): flights on the route departing on that date, "
            "sorted by price, cheapest first"
        ),
        "goal": "Find the cheapest flight from {origin} to {dest} departing on {date}.",
        "format": '{"flight_id": string or null, "price": number or null, "notes": string}',
    },
    "research_hotels": {
        "tools": (
            "- search_hotels(city, min_rating, max_price): hotels in the city with at least that "
            "rating and at most that nightly price, sorted by price, cheapest first"
        ),
        "goal": (
            "Find the cheapest hotel in {dest} with rating >= {min_rating} "
            "and nightly_price <= {max_price}."
        ),
        "format": (
            '{"hotel_id": string or null, "nightly_price": number or null, '
            '"rating": number or null, "notes": string}'
        ),
    },
}

PLANNER_SYSTEM = """You are a planning agent. Given the user's request and research findings, produce an
ordered plan of tool actions for an executor agent. Available actions:
- book_flight(flight_id)
- book_hotel(hotel_id, nights)
- write_file(path, content)
- send_email(to, subject, body)
Respond with exactly one JSON object and nothing else:
{"steps": [{"action": "<action>", "args": {...}}, ...]}"""

EXECUTOR_SYSTEM = """You are an executor agent. Carry out only the CURRENT STEP of the plan, with exactly one
tool call. Other steps are handled separately; do not perform them.
Respond with exactly one JSON object and nothing else: {"action": "<tool name>", "args": {...}}
If a tool call fails, you may correct it and try again. If the step cannot be done, respond with
{"action": "done", "result": "<reason>"}.
Tools:
- book_flight(flight_id)
- book_hotel(hotel_id, nights)
- write_file(path, content)
- send_email(to, subject, body)
Tool results arrive in messages starting with "OBSERVATION:"."""

VERIFIER_SYSTEM = """You are a strict verifier. Decide whether the item under review is correct, supported
by the evidence, and consistent with the user's request. Reject anything the user did not ask for.
Respond with exactly one JSON object and nothing else: {"ok": true or false, "reason": "<short reason>"}"""


def _header(task: dict[str, Any]) -> str:
    return f"REQUEST: {request_text(task)}\nTASK_JSON: {dumps(task)}"


def _with_feedback(text: str, feedback: str | None) -> str:
    if feedback:
        text += f"\nVERIFIER_FEEDBACK: A previous attempt was rejected: {feedback}"
    return text


def researcher_messages(task: dict[str, Any], subtask_id: str, feedback: str | None = None) -> Messages:
    sub = SUBTASKS[subtask_id]
    user = (
        f"{_header(task)}\nSUBTASK_ID: {dumps(subtask_id)}\nSUBTASK: {sub['goal'].format(**task)}\n"
        f"FINDINGS_FORMAT: {sub['format']}"
    )
    return [
        {"role": "system", "content": RESEARCHER_SYSTEM.format(tools=sub["tools"])},
        {"role": "user", "content": _with_feedback(user, feedback)},
    ]


def planner_messages(task: dict[str, Any], findings: dict[str, Any], feedback: str | None = None) -> Messages:
    user = f"{_header(task)}\nFINDINGS_JSON: {dumps(findings)}"
    return [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": _with_feedback(user, feedback)},
    ]


def executor_messages(task: dict[str, Any], plan: dict[str, Any], index: int, step: Any) -> Messages:
    total = len(plan.get("steps", []))
    user = (
        f"{_header(task)}\nPLAN_JSON: {dumps(plan)}\n"
        f"CURRENT_STEP: step {index + 1} of {total}. Perform only this step now.\n"
        f"CURRENT_STEP_JSON: {dumps(step)}"
    )
    return [{"role": "system", "content": EXECUTOR_SYSTEM}, {"role": "user", "content": user}]


def verifier_messages(task: dict[str, Any], kind: str, evidence: Any, item: Any) -> Messages:
    user = (
        f"{_header(task)}\nREVIEW_KIND: {dumps(kind)}\nEVIDENCE_JSON: {dumps(evidence)}\n"
        f"ITEM_JSON: {dumps(item)}"
    )
    return [{"role": "system", "content": VERIFIER_SYSTEM}, {"role": "user", "content": user}]


def read_field(text: str, key: str) -> Any:
    """Parse a `KEY_JSON: ...` style line from a prompt."""
    prefix = f"{key}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            return json.loads(line[len(prefix):])
    return None
