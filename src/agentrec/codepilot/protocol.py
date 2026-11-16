"""The protocol extension the coding pilot declares, recorded in every manifest and row.

AsynCodeBench's official protocols are `single`, `serial_specialists`,
`async_private`, `caid_manager`, and `async_manager`. Runtime recovery under
injected interventions is none of them, so these runs are a new experimental
condition and are never reported as official AsynCodeBench results.
"""

from __future__ import annotations

from typing import Any

from .case import INTERVENTIONS, PROTOCOL

DECLARATION: dict[str, Any] = {
    "protocol": PROTOCOL,
    "official_result_eligible": False,
    "base_protocol": "async_private",
    "differences_from_base": [
        "Specialist outputs are content-hashed patch artifacts with versions, not private worktree branches.",
        "Consumers start from provisional upstream artifacts along a frozen acyclic handoff order; edges against "
        "that order are resolved only at integration.",
        "A checker per specialist runs that specialist's dependency probes on a frozen checkpoint and writes a "
        "report bound to every artifact in the checkpoint, the environment, and the probe suite.",
        "Merge is a gated tool call whose contract requires a passing, applicable report for every merged artifact.",
        "Interventions revert one frozen-eligible producer function to its Commit0 stub in the producer's first "
        "artifact.",
        "Agents run a bounded edit-and-test loop against their primary tests, not the official 100-response "
        "OpenHands budget.",
    ],
    "interventions": {
        "clean": "No intervention.",
        INTERVENTIONS[0]: "The failed check of the regressed artifact is released 0.5 s after the consumer starts.",
        INTERVENTIONS[1]: "The failed check is released 0.5 s after the merge is proposed.",
    },
    "agent_inputs": [
        "problem statement", "role", "writable paths", "primary test targets", "workspace files",
        "upstream artifacts handed to it",
    ],
    "withheld_from_agents": [
        "dependency contracts", "probe lists", "hidden evaluator tests", "reference implementation",
        "the intervention and its schedule",
    ],
    "checker_inputs": [
        "dependency probes (upstream, downstream along kept edges) from the frozen metrics manifest",
    ],
    "same_for_every_policy": [
        "agents", "models", "seeds", "checker", "probe suite", "token and step limits", "corrector",
        "intervention schedule",
    ],
    "goal": "the official evaluator targets on the merged workspace",
    "metrics": ["CRSR_safe", "outcome class", "ADPR", "goal pass fraction", "makespan", "tokens", "effects"],
}

__all__ = ["DECLARATION"]
