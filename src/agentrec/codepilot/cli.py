"""`agentrec codepilot ...`: verify the registry, freeze, run, summarize, and the official clean run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _models(values: list[str]) -> tuple:
    from .freeze import ModelSpec

    out = []
    for value in values:
        name, model, base_url = value.split("=", 1)[0], *value.split("=", 1)[1].split("@", 1)
        out.append(ModelSpec(name, model, base_url))
    return tuple(out)


def cmd_verify(args: argparse.Namespace) -> None:
    from .registry import load_registry

    registry = load_registry(args.registry, allow_unpinned=args.allow_unpinned)
    print(json.dumps(registry.identity() | {"tasks": len(registry.tasks)}, indent=2))


def cmd_freeze(args: argparse.Namespace) -> None:
    from .pilot import config_sha, freeze

    config = freeze(
        args.registry, args.out, models=_models(args.model), executor=args.executor,
        allow_unpinned=args.allow_unpinned, tasks=tuple(args.tasks.split(",")) if args.tasks else None,
    )
    print(json.dumps({"selected": config.selection["selected"], "config_sha256": config_sha(config),
                      "notes": config.notes[:-1]}, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    from .pilot import run

    result = run(
        args.dir, reference=args.reference,
        tasks=tuple(args.tasks.split(",")) if args.tasks else None,
        policies=tuple(args.policies.split(",")) if args.policies else None,
    )
    print(json.dumps(result, indent=2))


def cmd_summary(args: argparse.Namespace) -> None:
    from ..mechanism.stats import crsr_safe, summarize
    from ..mechanism.suite import latest_by_cell, load_rows

    rows = latest_by_cell(load_rows(Path(args.dir)))
    for row in rows:
        row["condition"] = "clean" if row.get("intervention") == "clean" else "fault"
    groups = sorted({row.get("model", "") for row in rows})
    report = {}
    for model in groups:
        subset = [row for row in rows if row.get("model", "") == model]
        summary = summarize(subset)
        report[model] = {"summary": summary, "crsr_safe": crsr_safe(subset, sorted(summary))}
    print(json.dumps(report, indent=2, default=str))


def cmd_official(args: argparse.Namespace) -> None:
    from .pilot import official

    print(official(args.registry, args.task, env_file=args.env_file, model_tag=args.model_tag,
                   run_version=args.run_version, execute=args.execute))


def add_cli(sub) -> None:
    root = sub.add_parser("codepilot", help="AsynCodeBench-derived coding pilot (protocol extension, not official)")
    commands = root.add_subparsers(dest="codepilot_command", required=True)

    verify = commands.add_parser("verify-registry", help="verify the pinned release and every manifest hash")
    verify.add_argument("--registry", required=True)
    verify.add_argument("--allow-unpinned", action="store_true")
    verify.set_defaults(fn=cmd_verify)

    freeze = commands.add_parser("freeze", help="select tasks, fault sites, handoff, scopes; write pilot.json once")
    freeze.add_argument("--registry", required=True)
    freeze.add_argument("--out", required=True)
    freeze.add_argument("--model", action="append", default=[],
                        help="name=model@base_url, e.g. qwen=Qwen/Qwen3-Coder-30B-A3B-Instruct@http://localhost:8000/v1")
    freeze.add_argument("--executor", choices=("local", "docker"), default="local")
    freeze.add_argument("--tasks", default="", help="comma-separated task names instead of the selection rule")
    freeze.add_argument("--allow-unpinned", action="store_true")
    freeze.set_defaults(fn=cmd_freeze)

    run = commands.add_parser("run", help="run every missing cell of a frozen pilot")
    run.add_argument("dir")
    run.add_argument("--reference", action="store_true", help="reference agent instead of models (validation only)")
    run.add_argument("--tasks", default="")
    run.add_argument("--policies", default="")
    run.set_defaults(fn=cmd_run)

    summary = commands.add_parser("summary", help="per-model, per-policy summary and CRSR_safe")
    summary.add_argument("dir")
    summary.set_defaults(fn=cmd_summary)

    official = commands.add_parser("official", help="the release's own async_manager run for the clean condition")
    official.add_argument("--registry", required=True)
    official.add_argument("--task", required=True)
    official.add_argument("--env-file", required=True)
    official.add_argument("--model-tag", required=True)
    official.add_argument("--run-version", required=True)
    official.add_argument("--execute", action="store_true", help="run it; otherwise print the command")
    official.set_defaults(fn=cmd_official)


__all__ = ["add_cli"]
