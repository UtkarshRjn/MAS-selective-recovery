"""Thin CLI adapter for the official UndoBench runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def _root(args: Any) -> Path:
    root = Path(args.data)
    if not (root / "undobench" / "cli.py").is_file():
        raise FileNotFoundError(f"{root} is missing. Run `agentrec undobench download`.")
    return root


def cmd_download(args: Any) -> None:
    root = Path(args.data)
    if (root / "undobench" / "cli.py").is_file():
        print(f"already present: {root}")
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/tradertanmay/undobench.git", str(root)],
        check=True,
    )


def cmd_smoke(args: Any) -> None:
    subprocess.run([sys.executable, "-m", "undobench.cli", "smoke", "--task", args.task], cwd=_root(args), check=True)


def cmd_trial(args: Any) -> None:
    command = [
        sys.executable, "-m", "undobench.cli", "run", "--suite", args.suite,
        "--recovery", args.recovery, "--seeds", args.seeds, "--output", str(Path(args.out).resolve()),
    ]
    if args.model:
        command += ["--model", args.model]
    if args.dry_run:
        command.append("--dry-run")
    subprocess.run(command, cwd=_root(args), check=True)
