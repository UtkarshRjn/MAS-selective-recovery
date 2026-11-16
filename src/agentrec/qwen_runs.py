"""Qwen runs for the six graph shapes and the four travel faults.

Each seed builds its own client. Graph steps and the repair proposal are model
calls. The monitor publishes a repair only when the proposal matches the claim.
A rejected proposal is not booked. The numbers below are means over seeds.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from .fault_cases import FAULTS, run_fault_case
from .graph_monitor import GRAPH_POLICIES, run_graph_case

GRAPH_SLICE = (
    ("chain", "b"),
    ("diamond", "b"),
    ("join", "p"),
    ("backedge", "b"),
    ("collaborative", "a"),
    ("hierarchical", "b"),
)


@dataclass(frozen=True)
class QwenRow:
    seed: int
    case: str
    fault: str
    policy: str
    success: bool
    calls: int
    repeated: int
    blocked: int
    stale: int
    preserved: tuple[str, ...]
    llm_calls: int
    rejected: int


async def evaluate_qwen(make_llm, seeds: tuple[int, ...] = (0, 1, 2)) -> list[QwenRow]:
    rows: list[QwenRow] = []
    for seed in seeds:
        for template, fault in GRAPH_SLICE:
            for policy in GRAPH_POLICIES:
                llm = make_llm(seed)
                before = llm.usage.calls
                report = await run_graph_case(template, fault, policy, llm=llm)
                rows.append(QwenRow(
                    seed, template, fault, policy, report.success, report.calls, report.repeated,
                    report.blocked, report.stale, report.preserved, llm.usage.calls - before, 0,
                ))
        for fault in FAULTS:
            for policy in GRAPH_POLICIES:
                llm = make_llm(seed)
                before = llm.usage.calls
                report = await run_fault_case(fault, policy, llm=llm)
                rows.append(QwenRow(
                    seed, "travel", fault, policy, report.success, report.calls, report.repeated,
                    report.blocked, report.stale, report.preserved, llm.usage.calls - before,
                    report.rejected,
                ))
    return rows


def format_qwen(rows: list[QwenRow]) -> str:
    groups: dict[tuple[str, str, str], list[QwenRow]] = defaultdict(list)
    for row in rows:
        groups[(row.case, row.fault, row.policy)].append(row)
    lines = [
        f"{'case':<16}{'fault':<20}{'policy':<10}{'ok':>6}{'calls':>8}{'repeated':>10}"
        f"{'blocked':>9}{'stale':>7}{'llm':>6}  preserved"
    ]
    for key in groups:
        items = groups[key]
        count = len(items)
        preserved = items[0].preserved
        shown = ",".join(preserved) if all(item.preserved == preserved for item in items) else "mixed"
        lines.append(
            f"{key[0]:<16}{key[1]:<20}{key[2]:<10}{sum(item.success for item in items) / count:>6.2f}"
            f"{sum(item.calls for item in items) / count:>8.1f}"
            f"{sum(item.repeated for item in items) / count:>10.1f}"
            f"{sum(item.blocked for item in items) / count:>9.1f}"
            f"{sum(item.stale for item in items) / count:>7.1f}"
            f"{sum(item.llm_calls for item in items) / count:>6.1f}  {shown or '-'}"
        )
    return "\n".join(lines)
