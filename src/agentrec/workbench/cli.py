"""`agentrec workbench ...`: freeze, run, summarize, and WorkBench's own clean inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def cmd_freeze(args: argparse.Namespace) -> None:
    from ..codepilot.cli import _models
    from .pilot import config_sha, freeze

    config = freeze(args.root, args.out, models=_models(args.model), commit=args.commit or None)
    print(json.dumps({
        "selected": [(item["row"], item["template"]) for item in config.selection["selected"]],
        "excluded": config.selection["excluded"], "config_sha256": config_sha(config),
    }, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    from .pilot import run

    result = run(
        args.dir, reference=args.reference,
        rows=tuple(int(item) for item in args.rows.split(",")) if args.rows else None,
        policies=tuple(args.policies.split(",")) if args.policies else None,
    )
    print(json.dumps(result, indent=2))


def cmd_summary(args: argparse.Namespace) -> None:
    from ..codepilot.cli import cmd_summary as summary

    summary(args)


def cmd_official(args: argparse.Namespace) -> None:
    from .pilot import official

    print(official(args.dir, model_name=args.model_name, execute=args.execute))


def add_cli(sub) -> None:
    root = sub.add_parser("workbench", help="WorkBench-derived stateful pilot (protocol extension, not official)")
    commands = root.add_subparsers(dest="workbench_command", required=True)

    freeze = commands.add_parser("freeze", help="select rows by the frozen rule; write pilot.json once")
    freeze.add_argument("--root", required=True, help="WorkBench checkout")
    freeze.add_argument("--commit", default="", help="refuse a checkout at any other commit")
    freeze.add_argument("--out", required=True)
    freeze.add_argument("--model", action="append", default=[], help="name=model@base_url")
    freeze.set_defaults(fn=cmd_freeze)

    run = commands.add_parser("run", help="run every missing cell of a frozen pilot")
    run.add_argument("dir")
    run.add_argument("--reference", action="store_true", help="reference agent instead of models (validation only)")
    run.add_argument("--rows", default="", help="comma-separated WorkBench row indices")
    run.add_argument("--policies", default="")
    run.set_defaults(fn=cmd_run)

    summary = commands.add_parser("summary", help="per-model, per-policy summary and CRSR_safe")
    summary.add_argument("dir")
    summary.set_defaults(fn=cmd_summary)

    official = commands.add_parser("official", help="WorkBench's own inference on the selected rows (clean)")
    official.add_argument("dir")
    official.add_argument("--model-name", required=True, help="a WorkBench model name, e.g. gpt-4o")
    official.add_argument("--execute", action="store_true", help="run it; otherwise print the command")
    official.set_defaults(fn=cmd_official)


__all__ = ["add_cli"]
