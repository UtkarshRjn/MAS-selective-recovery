"""Claim checkers the monitor can run, kept apart from evaluator truth.

`OracleVerifier` reads a truth dictionary. It is a labeled experiment mode for
detection-isolated runs, never the default for an end-to-end comparison.
`SourceCheckVerifier` sees only what any agent could observe: source tools and
evidence already in the graph. Every method in a comparison gets the same one.

Local checks judge one claim. A `GlobalInvariant` judges a combination of
claims that can each pass locally. How a global failure may touch evidence that
already passed locally depends on the trust model:

``local_commit``   a local pass is a commitment; a later global failure on
                   committed evidence is reported as blocked.
``global_settle``  claims named by an invariant stay provisional after their
                   local pass until the invariant passes.
``revocable``      a local pass commits, and a global failure or a source
                   change may revoke the commitment explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .contract import DEAD_STATUSES, ArtifactRef, Evidence, EvidenceStatus, applies
from .verification import Verdict, verify_claim

TRUST_MODELS = ("local_commit", "global_settle", "revocable")


@dataclass
class CheckContext:
    """What a checker may read: graph evidence by claim, and runtime source tools."""

    versions: Callable[[str], list[Evidence]]
    enforce_applicability: bool = True
    tools: Any = None
    required: dict[str, tuple[ArtifactRef, ...]] = field(default_factory=dict)


class Verifier(Protocol):
    mode: str

    def check(self, evidence: Evidence, ctx: CheckContext) -> Verdict | Awaitable[Verdict]: ...


def passed(_evidence: Evidence | None = None) -> Verdict:
    return Verdict(True, "", (), False)


def failed(evidence: Evidence, reason: str, *, ambiguous: bool = False) -> Verdict:
    return Verdict(False, reason, (evidence.ref,), ambiguous)


def pending(*claims: str) -> Verdict:
    return Verdict(False, "waiting for evidence", (), False, pending=True, wait_for=tuple(claims))


class OracleVerifier:
    """Compares claims with supplied truth. Label results produced this way as oracle runs."""

    mode = "oracle"

    def __init__(self, truth: dict[str, Any]) -> None:
        self.truth = truth

    def check(self, evidence: Evidence, ctx: CheckContext) -> Verdict:
        return verify_claim(evidence, self.truth)


ClaimCheck = Callable[[Evidence, CheckContext], "Verdict | Awaitable[Verdict]"]


class SourceCheckVerifier:
    """Runs per-claim checks against runtime-observable sources.

    A claim without a registered check passes and is counted as unchecked, so
    a report can say how much of the graph was actually checked.
    """

    mode = "source_check"

    def __init__(self, checks: dict[str, ClaimCheck]) -> None:
        self.checks = dict(checks)
        self.unchecked: list[str] = []

    def check(self, evidence: Evidence, ctx: CheckContext) -> Verdict | Awaitable[Verdict]:
        check = self.checks.get(evidence.claim_id)
        if check is None:
            self.unchecked.append(evidence.claim_id)
            return passed(evidence)
        return check(evidence, ctx)


def live_versions(ctx: CheckContext, claim_id: str) -> list[Evidence]:
    return [item for item in ctx.versions(claim_id) if item.verification_status not in DEAD_STATUSES]


def applicable_report_check(
    report_claim: str,
    *,
    passed_field: str = "passed",
    extra: tuple[ArtifactRef, ...] = (),
) -> ClaimCheck:
    """CI-style check of an artifact claim: it passes when a passing report applies to it.

    With applicability enforced, a report counts only when it is bound to this
    exact content hash and to the environment and suite identities in `extra`.
    Without it, the latest report counts, which is the unversioned handoff.
    """

    def check(evidence: Evidence, ctx: CheckContext) -> Verdict:
        reports = live_versions(ctx, report_claim)
        if ctx.enforce_applicability:
            required = (evidence.artifact, *extra)
            reports = [item for item in reports if applies(item.applies_to, required)]
        if not reports:
            return pending(report_claim)
        settled = [item for item in reports if item.verification_status == EvidenceStatus.VERIFIED]
        if not settled:
            return pending(report_claim)
        latest = max(settled, key=lambda item: item.version)
        value = latest.value if isinstance(latest.value, dict) else {}
        if value.get(passed_field):
            return passed(evidence)
        return failed(evidence, f"{report_claim}@{latest.version} reports a failure")

    return check


@dataclass(frozen=True)
class GlobalInvariant:
    """A check over several claims.

    `check` receives claim values and returns (ok, reason, attributed claims).
    An empty attribution on failure means attribution is ambiguous: every
    member that is not committed becomes a suspect.
    """

    name: str
    claims: tuple[str, ...]
    check: Callable[[dict[str, Any]], tuple[bool, str, tuple[str, ...]]]
