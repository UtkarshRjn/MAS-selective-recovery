"""Fault-triggered monitor.

The provenance log is written on every handoff while agents run. This module is not
called on a clean run. After a fault is reported it reads that log once, keeps the
same restart set as coarse lineage, and attaches a note naming the faulty artifacts.
The note is built from fields already stored on those artifacts. There is no monitor
model call.
"""

from __future__ import annotations

from typing import Any

from .llm import LLM
from .provenance import Provenance
from .recovery import RecoveryReport, selective_recover, support_taint
from .world import World


def fault_note(prov: Provenance) -> dict[str, Any] | None:
    """Why the previous run was discarded, using only the provenance log."""
    discarded = []
    for nid in prov.faulty_nodes():
        node = prov.nodes[nid]
        if node.kind != "artifact":
            continue
        producer = prov.producer_of(nid)
        discarded.append({
            "artifact": node.key,
            "step": None if producer is None else prov.nodes[producer].key,
            "fault": node.data.get("fault"),
        })
    if not discarded:
        return None
    return {
        "discarded": discarded,
        "instruction": "These artifacts were faulty in the previous run. Discard them and produce this step again.",
    }


def monitor_recover(task: dict[str, Any], world: World, llm: LLM, prov: Provenance) -> RecoveryReport:
    """Coarse-lineage restart, with the log's fault note on steps that are redone."""
    return selective_recover("monitor", task, world, llm, prov, fault_note=fault_note(prov))


def support_monitor_recover(
    task: dict[str, Any], world: World, llm: LLM, prov: Provenance
) -> RecoveryReport:
    """Independent-support restart, with the same fault note on steps that are redone."""
    return selective_recover(
        "support_monitor", task, world, llm, prov, fault_note=fault_note(prov), taint_fn=support_taint
    )
