"""Dispatch an external action only after the monitor has approved its exact arguments.

Transport runs after that record, and outside the monitor lock. A transport
error does not roll the approval back into "unverified": the arguments were
accepted, and the tool call failed separately. Writes to the same path share
a resource lock.
"""

from __future__ import annotations

from typing import Any

from .contract import ApprovalStatus, Evidence, EvidenceStatus, ProposedAction, canonical


class TransportError(Exception):
    """The tool call failed after the action was approved."""


def resource_key(tool: str, arguments: dict[str, Any]) -> str:
    if tool == "write_file":
        return f"file:{arguments.get('path', '')}"
    return f"{tool}:{canonical(arguments)}"


def dispatch_blocker(
    action: ProposedAction,
    evidence: list[Evidence],
    *,
    attempt_eligible: bool,
    task_in_plan: bool,
    approved_canonical: str,
) -> str | None:
    """Return why this action must not be dispatched, or None when it may proceed."""
    if action.approval_status in {ApprovalStatus.DISPATCHED, ApprovalStatus.TRANSPORT_FAILED}:
        return "already_dispatched"
    if action.approval_status == ApprovalStatus.REJECTED:
        return "rejected"
    if not attempt_eligible:
        return "attempt_ineligible"
    if not task_in_plan:
        return "not_in_active_plan"
    if not approved_canonical or action.canonical_arguments != approved_canonical:
        return "approval_mismatch"
    if any(item.verification_status != EvidenceStatus.VERIFIED for item in evidence):
        return "evidence_unverified"
    if not evidence:
        return "evidence_unverified"
    return None
