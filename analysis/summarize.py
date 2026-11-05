"""Summarize an experiment's rows.jsonl into a per-(fault, method) table.

Usage: uv run python analysis/summarize.py results/<run-dir>/rows.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from statistics import mean


def tokens(usage: dict | None) -> float:
    usage = usage or {}
    return usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)


def main(path: str) -> None:
    rows = [json.loads(line) for line in open(path)]
    clean = [r for r in rows if r["method"] == "clean"]
    if clean:
        clean_ok = sum(r["task_recovery_success"] and r["final_effect_integrity"] for r in clean)
        print(f"clean success: {clean_ok}/{len(clean)}  "
              f"mean tokens: {mean(tokens(r['usage']) for r in clean):.0f}\n")

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["method"] != "clean" and r.get("fault_fired", True) and r.get("fault_used", True):
            groups[(r["fault"], r["method"])].append(r)

    injected = [r for r in rows if r["method"] == "none"]
    if injected:
        fired = sum(r.get("fault_fired", True) for r in injected)
        used = sum(r.get("fault_used", True) for r in injected)
        print(f"eligible faults: {used}/{len(injected)} changed outcome; {fired - used} fired but had no effect\n")

    restarts = {
        (r["seed"], r["fault"]): r for r in rows
        if r["method"] == "restart" and r.get("fault_fired", True)
    }
    header = (
        "fault", "method", "n", "task_ok", "final_effects", "recovery_effects",
        "irrev", "recovery_irrev", "any_unauth", "recovery_unauth",
        "token/restart", "latency/restart", "valid_work",
    )
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    order = {
        "none": 0, "restart": 1, "suffix": 2, "coarse": 3, "support": 4,
        "monitor": 5, "support_monitor": 6, "verify": 7,
    }
    for (fault, method), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 9))):
        paired = [(r, restarts.get((r["seed"], r["fault"]))) for r in rs]
        paired = [(r, restart) for r, restart in paired if restart is not None]
        token_ratio = latency_ratio = "-"
        if method not in {"none", "verify"} and paired:
            token_ratio = f"{mean(tokens(r['recovery_usage']) / max(tokens(b['recovery_usage']), 1) for r, b in paired):.2f}"
            latency_ratio = f"{mean(r['recovery_wall_s'] / max(b['recovery_wall_s'], 1e-9) for r, b in paired):.2f}"
        valid_work = "-"
        if "valid_work_preservation" in rs[0]:
            valid_work = f"{mean(r['valid_work_preservation'] for r in rs):.0%}"
        recovery_harms = [
            r["recovery_irreversible_harm"]
            for r in rs if r["recovery_irreversible_harm"] is not None
        ]
        recovery_effects = [
            r["recovery_effect_integrity"]
            for r in rs if r["recovery_effect_integrity"] is not None
        ]
        recovery_unauthorized = [
            r["recovery_unauthorized_emails"] > 0
            for r in rs if r["recovery_unauthorized_emails"] is not None
        ]
        cells = (
            fault, method, len(rs),
            f"{mean(r['task_recovery_success'] for r in rs):.0%}",
            f"{mean(r['final_effect_integrity'] for r in rs):.0%}",
            f"{mean(recovery_effects):.0%}" if recovery_effects else "-",
            f"{mean(r['irreversible_harm'] for r in rs):.0%}",
            f"{mean(recovery_harms):.0%}" if recovery_harms else "-",
            f"{mean(r['unauthorized_emails'] > 0 for r in rs):.0%}",
            f"{mean(recovery_unauthorized):.0%}" if recovery_unauthorized else "-",
            token_ratio, latency_ratio, valid_work,
        )
        print("| " + " | ".join(str(c) for c in cells) + " |")
    print("\nFinal effects cover the whole run; recovery effects cover only recovery-introduced actions. "
          "Ratios use recovery-only cost after the shared faulty run. Lower is better.")


if __name__ == "__main__":
    main(sys.argv[1])
