"""Rebuild the Qwen tables in the Phase 1 report from the committed rows.

    uv run python analysis/phase1_tables.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent / "artifacts" / "phase1"
BOOKING = ("restart", "suffix", "coarse", "monitor", "support", "support_monitor")
GRAPH = ("restart", "suffix", "coarse", "support", "monitor", "support_monitor")


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _tokens(usage: dict) -> int:
    return usage["prompt_tokens"] + usage["completion_tokens"]


def booking(path: Path) -> None:
    rows = _load(path)
    clean = [r for r in rows if r["method"] == "clean"]
    used = {(r["seed"], r["fault"]) for r in rows if r["method"] == "none" and r["fault_used"]}
    print(f"Booking: {len(clean)} clean runs, {sum(r['success'] for r in clean)} succeeded")
    print(f"Faulty runs whose outcome changed: {len(used)}\n")
    print("| Method | Recovered | Steps redone | Tokens | Seconds |")
    print("|---|---|---|---|---|")
    for method in BOOKING:
        rs = [r for r in rows if r["method"] == method and (r["seed"], r["fault"]) in used]
        total = rs[0]["steps_total"]
        print(
            f"| {method} | {sum(r['success'] for r in rs)}/{len(rs)} "
            f"| {mean(r['steps_recomputed'] for r in rs):.1f}/{total} "
            f"| {mean(_tokens(r['recovery_usage']) for r in rs):,.0f} "
            f"| {mean(r['recovery_wall_s'] for r in rs):.1f} |"
        )
    hotel = [r for r in rows if r["fault"] == "wrong_hotel_id@research_hotels" and r["method"] in ("coarse", "support")]
    print("\nwrong_hotel_id, steps redone per seed:")
    for r in sorted(hotel, key=lambda r: (r["method"], r["seed"])):
        print(f"  {r['method']:8} seed {r['seed']}: {r['steps_recomputed']}/{r['steps_total']}  {r['tainted_steps']}")


def coding_and_graphs(path: Path) -> None:
    rows = _load(path)
    print("\nCoding programs (one pass):\n")
    print("| Case | Method | Suite | Steps | Tokens | Seconds |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        if not r["topology"].startswith("coding") or r["method"] not in GRAPH:
            continue
        print(
            f"| {r['topology']} {r['fault']} | {r['method']} | {'pass' if r['success'] else 'fail'} "
            f"| {r['steps_recomputed']}/{r['steps_total']} | {_tokens(r['recovery_usage'])} "
            f"| {r['recovery_wall_s']:.2f} |"
        )
    by = defaultdict(list)
    for r in rows:
        if not r["topology"].startswith("coding") and r["method"] in GRAPH:
            by[(r["topology"], r["method"])].append(r)
    print("\nFormula graphs, restored / faults:\n")
    print("| Graph | " + " | ".join(GRAPH) + " |")
    print("|---" * (len(GRAPH) + 1) + "|")
    for topo in ("chain", "diamond", "join", "backedge"):
        cells = [f"{sum(r['success'] for r in by[(topo, m)])}/{len(by[(topo, m)])}" for m in GRAPH]
        print(f"| {topo} | " + " | ".join(cells) + " |")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT
    booking(root / "qwen-booking" / "rows.jsonl")
    coding_and_graphs(root / "qwen-coding-and-graphs" / "rows.jsonl")


if __name__ == "__main__":
    main()
