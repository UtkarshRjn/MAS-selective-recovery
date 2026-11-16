"""`agentrec mechanism ...`: run frozen suites, single cells, and paired comparisons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def cmd_run(args: argparse.Namespace) -> None:
    from .suite import SuiteConfig, preset, run_suite

    config = preset(args.suite, seeds=args.seeds, template=args.template, verifier=args.verifier)
    overrides = {}
    if args.families:
        overrides["families"] = tuple(part for part in args.families.split(",") if part)
    if args.policies:
        from .. import policies

        overrides["policies"] = tuple(config.name for config in policies.select(args.policies))
    if args.keep_events:
        overrides["keep_events"] = True
    if overrides:
        config = SuiteConfig(**{**config.__dict__, **overrides})
    out = Path(args.out) / f"{config.name}-{config.template}-{config.verifier}"
    print(json.dumps(run_suite(config, out), indent=2))


def cmd_summary(args: argparse.Namespace) -> None:
    from .stats import crsr_safe, summarize
    from .suite import load_rows

    rows = load_rows(args.dir)
    summary = summarize(rows)
    crsr = crsr_safe(rows, sorted(summary))
    if args.json:
        print(json.dumps({"summary": summary, "crsr_safe": crsr}, indent=2, default=str))
        return
    columns = (
        ("n", "n"), ("safe_completion_rate", "safe"), ("unsafe_rate", "unsafe"),
        ("makespan_p50", "mk50"), ("makespan_p95", "mk95"), ("restore_p95", "rest95"),
        ("tokens_mean", "tokens"), ("tokens_after_cancel", "tok_cx"), ("stale_outputs_accepted", "stale"),
        ("dispatched_on_dead_support", "dead_disp"), ("forbidden_effects", "forb"), ("duplicate_effects", "dup"),
        ("missing_effects", "miss"), ("preserved_fraction", "pres"),
        ("invalidation_precision", "inv_p"), ("invalidation_recall", "inv_r"),
    )
    print("policy".ljust(24) + "crsr".rjust(8) + "".join(label.rjust(10) for _key, label in columns))
    for name, item in summary.items():
        line = name.ljust(24) + _fmt(crsr.get(name, {}).get("crsr_safe")).rjust(8)
        line += "".join(_fmt(item.get(key)).rjust(10) for key, _label in columns)
        print(line)


def cmd_compare(args: argparse.Namespace) -> None:
    from .stats import compare
    from .suite import load_rows

    rows = load_rows(args.dir)
    print(json.dumps(compare(rows, args.a, args.b, iterations=args.iterations, seed=args.seed), indent=2))


def cmd_cell(args: argparse.Namespace) -> None:
    from .. import policies
    from . import cases
    from .harness import run_cell

    case = cases.build(args.family, args.template)
    schedule = next(item for item in case.schedules if item.name == args.schedule)
    result = run_cell(
        case, schedule, args.seed, policies.get(args.policy),
        verifier=args.verifier, clean=args.clean, keep_events=args.events,
    )
    if args.events:
        for event in result.events:
            print(json.dumps(event, default=str))
    print(json.dumps(result.row, indent=2, default=str))


def add_cli(sub) -> None:
    m = sub.add_parser("mechanism", help="controlled mechanism, ablation, and boundary suites")
    msub = m.add_subparsers(dest="mechanism_cmd", required=True)

    run = msub.add_parser("run", help="run a frozen suite (resumes into the same directory)")
    run.add_argument("--suite", choices=("mechanism", "extended", "ablation", "boundary"), default="mechanism")
    run.add_argument("--seeds", type=int, default=10)
    run.add_argument("--template", choices=("dev", "test"), default="dev")
    run.add_argument("--verifier", choices=("source_check", "oracle"), default="source_check")
    run.add_argument("--families", help="comma-separated subset")
    run.add_argument("--policies", help="comma-separated names or groups (core, extended, ablations)")
    run.add_argument("--keep-events", action="store_true")
    run.add_argument("--out", default="results/mechanism")
    run.set_defaults(fn=cmd_run)

    summary = msub.add_parser("summary", help="per-policy outcomes, latency, cost, effects, and CRSR_safe")
    summary.add_argument("dir")
    summary.add_argument("--json", action="store_true")
    summary.set_defaults(fn=cmd_summary)

    comp = msub.add_parser("compare", help="paired cluster bootstrap and exact McNemar between two policies")
    comp.add_argument("dir")
    comp.add_argument("a")
    comp.add_argument("b")
    comp.add_argument("--iterations", type=int, default=5000)
    comp.add_argument("--seed", type=int, default=0)
    comp.set_defaults(fn=cmd_compare)

    cell = msub.add_parser("cell", help="run one cell and print its row (and events)")
    cell.add_argument("family")
    cell.add_argument("schedule")
    cell.add_argument("policy")
    cell.add_argument("--seed", type=int, default=0)
    cell.add_argument("--template", choices=("dev", "test"), default="dev")
    cell.add_argument("--verifier", choices=("source_check", "oracle"), default="source_check")
    cell.add_argument("--clean", action="store_true")
    cell.add_argument("--events", action="store_true")
    cell.set_defaults(fn=cmd_cell)
