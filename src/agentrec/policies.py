"""Recovery policies as explicit switches over one runtime.

Every policy runs on the same monitor, tool gateway, agents, checker, and
budget. A policy changes the recovery mechanism, not the information it gets.

Switches:

``recovery``        graph edit after a failed check: ``online`` (claim cone),
                    ``coarse`` (every claim of the producer), ``suffix``
                    (faulty task and every later task), ``restart`` (all),
                    ``superstep`` (checkpoint replay from the faulty
                    superstep), ``local_repair`` (publish a correction only),
                    ``verify_before`` (legacy: publish only), ``none``.
``correction``      ask the corrector for a replacement value and publish it
                    only after the same checker accepts it.
``invalidation``    spread a failure to the provisional consumer cone.
``fencing``         reject outputs and actions from invalidated or obsolete
                    attempts, and from dead evidence.
``cancellation``    preempt invalidated attempts that are still running.
``action_gate``     hold effects until their support is verified and the
                    typed contract approves the exact arguments.
``consume_provisional``  let consumers read unverified evidence.
``read_version_validation``  reject a write whose reads are not the latest
                    live versions (STORM-style write-time validation).
``enforce_applicability``  a report applies only to the exact artifact,
                    environment, and suite identities it is bound to.
``timing``          ``online`` or ``posthoc`` (failures are applied only
                    after the forward run has quiesced).
``barrier``         ``none`` or ``superstep`` (BSP scheduling).
``effect_consistency``  ``per_call_lock``, ``resource_lock``, ``cas``, or
                    ``none`` for how dispatch touches the target.
``reconcile``       after a lost acknowledgment, query the target by
                    idempotency key instead of retrying (``naive_retry``
                    retries blindly; ``none`` leaves the outcome uncertain).
``compensate``      issue registered inverses for reversible effects whose
                    support was invalidated after dispatch.

Adaptations of published systems are labeled "-style". Their failures in this
harness are failures of the adaptation, not of the published system.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class PolicyConfig:
    name: str
    recovery: str
    correction: bool = False
    invalidation: bool = True
    fencing: bool = True
    cancellation: bool = True
    action_gate: bool = True
    consume_provisional: bool = True
    read_version_validation: bool = False
    enforce_applicability: bool = True
    timing: str = "online"
    barrier: str = "none"
    effect_consistency: str = "resource_lock"
    reconcile: str = "reconcile"
    compensate: bool = False
    advisory: bool = False
    adaptation_of: str = ""
    description: str = ""

    def with_(self, **changes) -> "PolicyConfig":
        return replace(self, **changes)


# Configurations behind the legacy policy names used by the first online slices.
LEGACY = {
    "online": PolicyConfig("online", "online", correction=True),
    "verify_before": PolicyConfig("verify_before", "verify_before", correction=True, consume_provisional=False),
    "restart": PolicyConfig("restart", "restart"),
    "support": PolicyConfig("support", "support"),
    "coarse": PolicyConfig("coarse", "coarse"),
    "suffix": PolicyConfig("suffix", "suffix"),
}


def legacy_config(name: str) -> PolicyConfig:
    if name not in LEGACY:
        raise ValueError(f"unknown recovery policy {name}")
    return LEGACY[name]


CORE = (
    PolicyConfig("restart", "restart", description="Full restart: discard every attempt and rerun the plan."),
    PolicyConfig("suffix", "suffix", description="Suffix replay: rerun the faulty task and every later task."),
    PolicyConfig(
        "coarse", "coarse",
        description="Producer-level selective recovery: discard every claim of the faulty producer and its cone.",
    ),
    PolicyConfig(
        "claim_rerun", "online",
        description="Claim-level selective rerun without correction: invalidate the claim cone and rerun it.",
    ),
    PolicyConfig(
        "online", "online", correction=True,
        description="Online claim-level checked correction: invalidate the claim cone, publish a checked "
        "replacement, and rerun only consumers.",
    ),
    PolicyConfig(
        "verify_before", "online", correction=True, consume_provisional=False,
        description="Verify-before: consumers read only verified evidence; same checker and corrector.",
    ),
    PolicyConfig(
        "advisory", "local_repair", correction=True, invalidation=False, fencing=False, cancellation=False,
        advisory=True, adaptation_of="CoAgent-style",
        description="Version-aware advisory repair: publish a checked corrected version and notify every "
        "consumer of a superseded version; each agent decides whether to reread and rerun. No mechanical "
        "invalidation, cancellation, or fencing.",
    ),
)

EXTENDED = (
    PolicyConfig(
        "posthoc_repair", "online", action_gate=False, timing="posthoc", compensate=True,
        adaptation_of="Wang et al.-style",
        description="Dependency-guided repair after the forward run completes; selective replay in trace "
        "order; reversible effects compensated.",
    ),
    PolicyConfig(
        "storm_style", "local_repair", correction=True, invalidation=False, fencing=False, cancellation=False,
        action_gate=False, read_version_validation=True,
        adaptation_of="STORM-style",
        description="Write-time validation of observed read versions; local repair of the failed claim.",
    ),
    PolicyConfig(
        "atomix_style", "coarse", read_version_validation=True, compensate=True,
        adaptation_of="Atomix-style",
        description="Transaction gate: buffered effects settle only after their read scope is verified; "
        "stale-read abort; producer-level abort with cascade; compensation for reversible effects.",
    ),
    PolicyConfig(
        "langgraph_style", "superstep", barrier="superstep",
        adaptation_of="LangGraph-style",
        description="Superstep checkpoints; successful sibling writes kept; replay from the faulty "
        "superstep; tools behind an interrupt with the same checker.",
    ),
)

ABLATIONS = (
    PolicyConfig("inv_on_gate_off", "online", correction=True, action_gate=False,
                 description="2x2 ablation: claim invalidation on, external action gate off."),
    PolicyConfig("inv_off_gate_on", "online", correction=True, invalidation=False,
                 description="2x2 ablation: claim invalidation off, external action gate on."),
    PolicyConfig("inv_off_gate_off", "online", correction=True, invalidation=False, action_gate=False,
                 description="2x2 ablation: claim invalidation off, external action gate off."),
    PolicyConfig("no_cancel", "online", correction=True, cancellation=False,
                 description="Cancellation off, stale-output fencing kept."),
    PolicyConfig("cancel_only", "online", correction=True, fencing=False,
                 description="Cancel flag only: cancellation requested, completed late outputs accepted."),
    PolicyConfig("last_writer_wins", "online", correction=True, fencing=False, cancellation=False,
                 description="No cancellation and no fencing: the last result stored wins."),
    PolicyConfig("no_correction_model", "online",
                 description="Claim-level invalidation without a correction model (isolates granularity)."),
    PolicyConfig("unversioned_handoff", "online", correction=True, enforce_applicability=False,
                 description="Reports are handed off without artifact identity (global latest-pass flag)."),
    PolicyConfig("file_lock", "online", correction=True, enforce_applicability=False,
                 effect_consistency="per_call_lock",
                 description="Ordinary per-path locking without artifact identity."),
    PolicyConfig("langgraph_style_nogate", "superstep", barrier="superstep", action_gate=False,
                 adaptation_of="LangGraph-style", description="Native checkpoint replay without an interrupt."),
    PolicyConfig("online_cas", "online", correction=True, effect_consistency="cas",
                 description="Online checked correction with target-side compare-and-set on observed resource versions."),
    PolicyConfig("online_no_lock", "online", correction=True, effect_consistency="none",
                 description="Online checked correction; effects dispatched without any resource serialization."),
    PolicyConfig("naive_retry", "online", correction=True, reconcile="naive_retry",
                 description="After a lost acknowledgment, retry without an idempotency key."),
    PolicyConfig("no_reconcile", "online", correction=True, reconcile="none",
                 description="After a lost acknowledgment, leave the outcome uncertain."),
    PolicyConfig("no_applicability", "online", correction=True, enforce_applicability=False,
                 description="Applicability check off: any passing report may approve any artifact."),
)

ALL = {config.name: config for config in (*CORE, *EXTENDED, *ABLATIONS)}
CORE_NAMES = tuple(config.name for config in CORE)
EXTENDED_NAMES = tuple(config.name for config in EXTENDED)
ABLATION_NAMES = tuple(config.name for config in ABLATIONS)


def get(name: str) -> PolicyConfig:
    if name not in ALL:
        raise KeyError(f"unknown policy {name}; known: {', '.join(ALL)}")
    return ALL[name]


def select(spec: str) -> tuple[PolicyConfig, ...]:
    """`core`, `extended`, `ablations`, `all`, or a comma-separated list of names."""
    groups = {
        "core": CORE,
        "extended": EXTENDED,
        "ablations": ABLATIONS,
        "all": (*CORE, *EXTENDED, *ABLATIONS),
    }
    out: list[PolicyConfig] = []
    for part in (item.strip() for item in spec.split(",") if item.strip()):
        for config in groups.get(part, (get(part),) if part not in groups else ()):
            if config not in out:
                out.append(config)
    return tuple(out)
