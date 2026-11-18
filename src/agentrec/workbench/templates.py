"""Decompositions of four WorkBench multi-domain templates into concurrent lookups and one decision.

Selection rule (frozen): multi-domain templates whose instruction needs at
least one lookup that decides a branch and whose outcome includes a write,
covering an irreversible effect (`email.send_email`) and reversible ones
(`calendar.create_event`, `project_management.create_task`). For each template
the first three rows in file order are taken whose deterministic reference
reproduces WorkBench's ground truth under WorkBench's own scorer; rows that do
not are recorded as exclusions.

Each lookup is answered from WorkBench read tools. Its value records the inputs
it was derived from, so the source check can re-derive it independently. The
fault for a template corrupts the branch-deciding lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .source import TODAY, WorkBench

Values = dict[str, Any]

METRICS = {
    "total visits": "analytics.total_visits_count",
    "engaged users": "analytics.engaged_users_count",
    "average session duration": "analytics.get_average_session_duration",
}
TOMORROW = "2023-12-01"
NEXT_FRIDAY = "2023-12-08"


@dataclass(frozen=True)
class Lookup:
    claim: str
    instruction: str
    tools: tuple[str, ...]
    inputs: tuple[str, ...]
    derive: Callable[[WorkBench, Values, Values], Any]
    answer_format: str
    # The fields a check compares; other fields are free-form.
    key_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Template:
    key: str
    marker: str
    description: str
    parse: Callable[[str], Values]
    lookups: tuple[Lookup, ...]
    decide: Callable[[Values, Values], list[tuple[str, dict[str, str]]]]
    effect_tools: tuple[str, ...]
    fault_claim: str
    corrupt: Callable[[Any], Any]
    params_shown: tuple[str, ...] = field(default_factory=tuple)

    def lookup(self, claim: str) -> Lookup:
        return next(item for item in self.lookups if item.claim == claim)


# ------------------------------------------------------------------ derivations

def _email_for(wb: WorkBench, params: Values, _inputs: Values) -> dict[str, Any]:
    found = wb.call("company_directory.find_email_address", name=params["name"])
    if not isinstance(found, list) or not found:
        return {"name": params["name"], "email": None}
    exact = [item for item in found if item.split("@")[0].split(".")[0] == params["name"].lower()]
    return {"name": params["name"], "email": (exact or found)[0]}


def _overdue(wb: WorkBench, _params: Values, inputs: Values) -> dict[str, Any]:
    email = inputs["assignee"]["email"]
    tasks = wb.call("project_management.search_tasks", assigned_to_email=email)
    tasks = tasks if isinstance(tasks, list) else []
    overdue = [task for task in tasks if task["due_date"] < TODAY and task["list_name"] != "Completed"]
    return {"email": email, "overdue": bool(overdue), "count": len(overdue)}


def _free_slot(wb: WorkBench, _params: Values, _inputs: Values) -> dict[str, Any]:
    """The first 30-minute gap tomorrow between 09:00 and 18:00, probed window by window."""
    start = datetime.fromisoformat(f"{TOMORROW} 09:00:00")
    end = datetime.fromisoformat(f"{TOMORROW} 18:00:00")
    slot = start
    while slot + timedelta(minutes=30) <= end:
        busy = wb.call(
            "calendar.search_events",
            time_min=str(slot + timedelta(seconds=1)), time_max=str(slot + timedelta(minutes=29, seconds=59)),
        )
        if not busy:
            return {"date": TOMORROW, "start": str(slot)}
        slot += timedelta(minutes=30)
    return {"date": TOMORROW, "start": None}


def _customer(wb: WorkBench, params: Values, _inputs: Values) -> dict[str, Any]:
    rows = wb.call("customer_relationship_manager.search_customers", customer_name=params["customer"])
    rows = [row for row in rows if row.get("customer_name") == params["customer"]] if isinstance(rows, list) else []
    if not rows:
        return {"customer": params["customer"], "assigned_to_email": None, "last_contact_date": None}
    return {"customer": params["customer"], "assigned_to_email": rows[0]["assigned_to_email"],
            "last_contact_date": rows[0]["last_contact_date"]}


def _crossed(wb: WorkBench, params: Values, _inputs: Values) -> dict[str, Any]:
    """WorkBench's own rule: 'less' when never above the threshold, 'more' when never below, else both."""
    series = wb.call(METRICS[params["metric"]], time_min=params["since"])
    values = [float(value) for value in (series or {}).values()] if isinstance(series, dict) else []
    threshold = float(params["threshold"])
    if not any(value > threshold for value in values):
        label = "less"
    elif not any(value < threshold for value in values):
        label = "more"
    else:
        label = "both more and less"
    return {"metric": params["metric"], "since": params["since"], "threshold": params["threshold"],
            "direction": params["direction"], "crossed": params["direction"] in label}


# ------------------------------------------------------------------- parsing

def _month_day(text: str) -> str:
    return str(datetime.strptime(f"{text} 2023", "%B %d %Y").date())


def _parse_overdue(text: str) -> Values:
    match = re.search(r"(?:check if|If) (\w+) has any overdue tasks", text)
    if match is None:
        raise ValueError(text)
    return {"name": match.group(1).lower()}


def _parse_crm(text: str) -> Values:
    match = re.search(r"(?:spoke|spoken) to ([A-Z]\w+ [A-Z]\w+)", text)
    if match is None:
        raise ValueError(text)
    return {"customer": match.group(1)}


def _parse_metric(text: str) -> Values:
    metric = re.search(r"(total visits|engaged users|average session duration)", text)
    direction = re.search(r"(more|less) than (\d+)", text)
    since = re.search(r"since (\w+ \d+)", text)
    if not (metric and direction and since):
        raise ValueError(text)
    return {"metric": metric.group(1), "direction": direction.group(1), "threshold": direction.group(2),
            "since": _month_day(since.group(1))}


def _parse_metric_email(text: str) -> Values:
    params = _parse_metric(text)
    recipient = re.search(r"send an email to ([\w.]+@[\w.]+\w)", text)
    if recipient is None:
        raise ValueError(text)
    return {**params, "recipient": recipient.group(1)}


def _parse_metric_task(text: str) -> Values:
    params = _parse_metric(text)
    name = re.search(r"for (\w+) on the front-end", text)
    if name is None:
        raise ValueError(text)
    return {**params, "name": name.group(1).lower()}


# ------------------------------------------------------------------ decisions

def _decide_overdue(_params: Values, found: Values) -> list[tuple[str, dict[str, str]]]:
    email = found["assignee"]["email"]
    if found["overdue"]["overdue"]:
        return [
            ("calendar.create_event", {"event_name": "Catch up on overdue tasks", "participant_email": email,
                                       "event_start": found["slot"]["start"], "duration": "30"}),
            ("email.send_email", {"recipient": email, "subject": "Discuss overdue tasks",
                                  "body": "I noticed you have a few overdue tasks - let's catch up tomorrow."}),
        ]
    return [("email.send_email", {"recipient": email, "subject": "Good work this sprint",
                                  "body": "Nice work keeping on top of your tasks this sprint!"})]


def _decide_crm(params: Values, found: Values) -> list[tuple[str, dict[str, str]]]:
    last = found["customer"]["last_contact_date"]
    if last is None:
        return []
    stale = datetime.fromisoformat(f"{TODAY} 00:00:00") - datetime.fromisoformat(str(last)[:10] + " 00:00:00")
    if stale <= timedelta(days=14):
        return []
    return [("calendar.create_event", {"event_name": f"Update on {params['customer']}",
                                       "participant_email": found["customer"]["assigned_to_email"],
                                       "event_start": found["slot"]["start"], "duration": "30"})]


def _decide_metric_email(params: Values, found: Values) -> list[tuple[str, dict[str, str]]]:
    if not found["metric"]["crossed"]:
        return []
    metric, direction, threshold = params["metric"], params["direction"], params["threshold"]
    return [("email.send_email", {"recipient": params["recipient"], "subject": f"Update on {metric}",
                                  "body": f"I noticed {metric} was {direction} than {threshold} - can you update me?"})]


def _decide_metric_task(params: Values, found: Values) -> list[tuple[str, dict[str, str]]]:
    email = found["assignee"]["email"]
    metric = params["metric"]
    if found["metric"]["crossed"]:
        return [("project_management.create_task", {"task_name": f"Improve {metric}", "board": "Front end",
                                                    "assigned_to_email": email, "due_date": NEXT_FRIDAY,
                                                    "list_name": "Backlog"})]
    return [("email.send_email", {"recipient": email, "subject": f"Recent {metric}",
                                  "body": f"I noticed {metric} has been stable, nice work!"})]


# ------------------------------------------------------------------ faults

def _flip_overdue(value: Values) -> Values:
    return {**value, "overdue": not value["overdue"], "count": 0 if value["overdue"] else 2}


def _shift_contact(value: Values) -> Values:
    last = value.get("last_contact_date")
    if last is None:
        return value
    day = date.fromisoformat(str(last)[:10])
    moved = day - timedelta(days=30) if (date.fromisoformat(TODAY) - day).days <= 14 else date.fromisoformat(TODAY) - timedelta(days=3)
    return {**value, "last_contact_date": str(moved)}


def _flip_crossed(value: Values) -> Values:
    return {**value, "crossed": not value["crossed"]}


DIRECTORY = ("company_directory.find_email_address",)
CALENDAR = ("calendar.search_events",)

TEMPLATES: tuple[Template, ...] = (
    Template(
        "overdue_meeting_email",
        "If {name} has any overdue tasks, book a half hour meeting with them called 'Catch up on overdue tasks'",
        "Directory lookup, then overdue-task lookup; free-slot lookup in parallel; meeting plus email, or a "
        "different email.",
        _parse_overdue,
        (
            Lookup("assignee", "Find the work email address of the employee named {name}.", DIRECTORY, (),
                   _email_for, '{"name": "...", "email": "..."}', ("email",)),
            Lookup("overdue", "Does the employee with email {assignee.email} have any overdue tasks (due before "
                   "today and not in the Completed list)?", ("project_management.search_tasks",), ("assignee",),
                   _overdue, '{"email": "...", "overdue": true or false, "count": N}', ("email", "overdue")),
            Lookup("slot", "Find the earliest time tomorrow when a 30-minute meeting fits in the calendar between "
                   "09:00 and 18:00.", CALENDAR, (), _free_slot, '{"date": "YYYY-MM-DD", "start": "YYYY-MM-DD HH:MM:SS"}',
                   ("start",)),
        ),
        _decide_overdue,
        ("calendar.create_event", "email.send_email"),
        "overdue",
        _flip_overdue,
    ),
    Template(
        "crm_stale_meeting",
        "If we haven't spoke to {current_customer_name} in the past fortnight book a 30-minute meeting",
        "CRM lookup of the customer's assignee and last contact; free-slot lookup in parallel; a meeting or nothing.",
        _parse_crm,
        (
            Lookup("customer", "Look up the customer named {customer} in the CRM: who is assigned to them and when "
                   "were they last contacted?", ("customer_relationship_manager.search_customers",), (), _customer,
                   '{"customer": "...", "assigned_to_email": "...", "last_contact_date": "YYYY-MM-DD"}',
                   ("assigned_to_email", "last_contact_date")),
            Lookup("slot", "Find the earliest time tomorrow when a 30-minute meeting fits in the calendar between "
                   "09:00 and 18:00.", CALENDAR, (), _free_slot, '{"date": "YYYY-MM-DD", "start": "YYYY-MM-DD HH:MM:SS"}',
                   ("start",)),
        ),
        _decide_crm,
        ("calendar.create_event",),
        "customer",
        _shift_contact,
    ),
    Template(
        "metric_alert_email",
        "If {natural_language_metric} was {more_or_less} than {threshold} at any time since {natural_language_date} "
        "send an email to {sender}",
        "Analytics lookup decides whether an irreversible email is sent.",
        _parse_metric_email,
        (
            Lookup("metric", "Was {metric} {direction} than {threshold} at any time since {since}? Use the daily "
                   "values.", tuple(METRICS.values()), (), _crossed,
                   '{"metric": "...", "since": "YYYY-MM-DD", "threshold": "...", "direction": "...", '
                   '"crossed": true or false}', ("crossed",)),
        ),
        _decide_metric_email,
        ("email.send_email",),
        "metric",
        _flip_crossed,
    ),
    Template(
        "metric_task_or_email",
        "make a backlog task 'Improve {natural_language_metric}' for {name} on the front-end board",
        "Analytics lookup and directory lookup in parallel; a reversible task or an irreversible email.",
        _parse_metric_task,
        (
            Lookup("metric", "Was {metric} {direction} than {threshold} at any time since {since}? Use the daily "
                   "values.", tuple(METRICS.values()), (), _crossed,
                   '{"metric": "...", "since": "YYYY-MM-DD", "threshold": "...", "direction": "...", '
                   '"crossed": true or false}', ("crossed",)),
            Lookup("assignee", "Find the work email address of the employee named {name}.", DIRECTORY, (),
                   _email_for, '{"name": "...", "email": "..."}', ("email",)),
        ),
        _decide_metric_task,
        ("project_management.create_task", "email.send_email"),
        "metric",
        _flip_crossed,
    ),
)

BY_KEY = {template.key: template for template in TEMPLATES}


def template_for(task) -> Template | None:
    for template in TEMPLATES:
        if template.marker in task.base_template:
            return template
    return None


__all__ = ["BY_KEY", "Lookup", "METRICS", "TEMPLATES", "Template", "template_for"]
