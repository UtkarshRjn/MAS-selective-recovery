"""Checks for claims and for the exact external action that cites them.

Correct evidence does not make an action appropriate. Action approval is an
equality check against the arguments the task requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contract import Evidence, EvidenceRef, canonical


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    suspect_claims: tuple[EvidenceRef, ...]
    ambiguous: bool
    approved_arguments: dict[str, Any] | None = None
    pending: bool = False
    wait_for: tuple[str, ...] = ()


def verify_claim(evidence: Evidence, truth: dict[str, Any]) -> Verdict:
    """Supplied truth for the mock slice. A wrong date is attributed to that claim."""
    claim = evidence.claim_id
    value = evidence.value
    if claim == "claim:restaurant_date":
        ok = isinstance(value, dict) and value.get("date") == truth["date"]
        return Verdict(ok, "" if ok else "wrong date", () if ok else (evidence.ref,), False)
    if claim == "claim:refundable":
        flag = value.get("refundable") if isinstance(value, dict) else value
        ok = flag == truth.get("refundable")
        return Verdict(ok, "" if ok else "refundable flag does not match the hotel", () if ok else (evidence.ref,), False)
    if claim == "claim:plan":
        required = truth.get("plan_actions")
        if required is not None:
            steps = value.get("steps") if isinstance(value, dict) else None
            got = [step.get("action") for step in (steps or []) if isinstance(step, dict)]
            ok = got == list(required)
            return Verdict(
                ok,
                "" if ok else "plan dropped a required step",
                () if ok else (evidence.ref,),
                not ok,
            )
        text = "" if not isinstance(value, dict) else str(value.get("itinerary", ""))
        flight_ok = isinstance(value, dict) and value.get("flight_id") == truth["flight_id"]
        ok = flight_ok and truth["bad_date"] not in text
        # The plan is a computation over several claims. If it fails, the
        # caller treats attribution as ambiguous unless a suspect was supplied.
        return Verdict(ok, "" if ok else "plan inconsistent with the request", () if ok else (evidence.ref,), not ok)
    expected = truth.get(claim)
    if expected is not None and value != expected:
        return Verdict(False, "value does not match the required claim", (evidence.ref,), True)
    return Verdict(True, "", (), False)


def verify_action(
    tool: str,
    arguments: dict[str, Any],
    evidence: list[Evidence],
    required_arguments: dict[str, Any],
) -> Verdict:
    """Approve only when the evidence is verified and the arguments match exactly."""
    if any(item.verification_status.value != "verified" for item in evidence):
        return Verdict(False, "supporting evidence is not verified", tuple(item.ref for item in evidence), False)
    if tool != "book_flight":
        return Verdict(False, "action is not part of the task", (), False)
    if canonical(arguments) != canonical(required_arguments):
        return Verdict(False, "arguments differ from the approved action", (), False)
    flight = next((item for item in evidence if item.claim_id == "claim:flight_id"), None)
    if flight is None or flight.value.get("flight_id") != arguments.get("flight_id"):
        return Verdict(False, "flight evidence does not support these arguments", (), False)
    plan = next((item for item in evidence if item.claim_id == "claim:plan"), None)
    if plan is None or plan.value.get("flight_id") != arguments.get("flight_id"):
        return Verdict(False, "plan does not call for these arguments", (), False)
    return Verdict(True, "", (), False, approved_arguments=dict(required_arguments))
