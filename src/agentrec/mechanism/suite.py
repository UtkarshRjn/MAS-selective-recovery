"""Frozen suite runs: every cell, its clean twin, a manifest, and an append-only row log.

A suite directory holds `manifest.json` (what was run, under which code) and
`rows.jsonl` (one row per cell). The manifest is frozen: a rerun into the same
directory must have the same configuration hash, and finished cells are skipped.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .. import policies as policy_registry
from ..policies import PolicyConfig
from . import cases
from .harness import VERIFIER_MODES, run_cell
from .spec import CaseSpec, Schedule

BOUNDARY_ABLATIONS = ("online_cas", "online_no_lock", "naive_retry", "no_reconcile")


@dataclass(frozen=True)
class SuiteConfig:
    name: str
    families: tuple[str, ...]
    policies: tuple[str, ...]
    seeds: tuple[int, ...]
    template: str = "dev"
    schedules: str = "core"
    verifier: str = "source_check"
    clean: bool = True
    horizon: float = 400.0
    correction_budget: int = 3
    keep_events: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verifier not in VERIFIER_MODES:
            raise ValueError(f"unknown verifier mode {self.verifier}")
        if self.template not in cases.TEMPLATES:
            raise ValueError(f"unknown template {self.template}")
        if self.schedules not in {"core", "all"}:
            raise ValueError("schedules must be 'core' or 'all'")
        for family in self.families:
            if family not in cases.ALL_FAMILIES:
                raise ValueError(f"unknown family {family}")
        for name in self.policies:
            policy_registry.get(name)


def preset(name: str, *, seeds: int = 10, template: str = "dev", verifier: str = "source_check") -> SuiteConfig:
    """The staged suites from the evaluation plan."""
    seed_range = tuple(range(seeds))
    core = tuple(config.name for config in policy_registry.CORE)
    mechanism = tuple(cases.MECHANISM_FAMILIES)
    if name == "mechanism":
        return SuiteConfig(name, mechanism, core, seed_range, template, "core", verifier)
    if name == "extended":
        names = ("online", *(config.name for config in policy_registry.EXTENDED))
        return SuiteConfig(name, mechanism, names, seed_range, template, "core", verifier)
    if name == "ablation":
        names = ("online", *(config.name for config in policy_registry.ABLATIONS))
        return SuiteConfig(name, mechanism, names, seed_range, template, "core", verifier)
    if name == "boundary":
        return SuiteConfig(
            name, tuple(cases.BOUNDARY_FAMILIES), (*core, *BOUNDARY_ABLATIONS), seed_range, template, "all", verifier,
        )
    if name == "pararecover":
        return SuiteConfig(name, tuple(cases.PARARECOVER_FAMILIES), core, seed_range, template, "all", verifier)
    raise KeyError(f"unknown suite {name}; known: mechanism, extended, ablation, boundary, pararecover")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _policy_dict(config: PolicyConfig) -> dict[str, Any]:
    return {key: value for key, value in asdict(config).items()}


def schedules_for(case: CaseSpec, which: str) -> tuple[Schedule, ...]:
    return cases.core_schedules(case) if which == "core" else tuple(case.schedules)


def manifest(config: SuiteConfig) -> dict[str, Any]:
    """Everything that determines the rows, plus the code identity it ran under."""
    built = {family: cases.build(family, config.template) for family in config.families}
    frozen = {
        "suite": asdict(config),
        "policies": {name: _policy_dict(policy_registry.get(name)) for name in config.policies},
        "cases": {family: case.manifest() for family, case in built.items()},
        "schedules": {
            family: [schedule.name for schedule in schedules_for(case, config.schedules)]
            for family, case in built.items()
        },
    }
    digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "config_sha256": digest,
        "frozen": frozen,
        "code": {
            "git_sha": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain", "--", "src")),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "created": datetime.now(timezone.utc).isoformat(),
    }


def cells(config: SuiteConfig) -> Iterator[tuple[CaseSpec, Schedule, int, PolicyConfig, bool]]:
    """Fault cells first, then one clean cell per (family, seed, policy)."""
    for family in config.families:
        case = cases.build(family, config.template)
        for schedule in schedules_for(case, config.schedules):
            for seed in config.seeds:
                for name in config.policies:
                    yield case, schedule, seed, policy_registry.get(name), False
        if config.clean and case.has_clean_condition:
            reference = schedules_for(case, config.schedules)[0]
            for seed in config.seeds:
                for name in config.policies:
                    yield case, reference, seed, policy_registry.get(name), True


def cell_key(case: CaseSpec, schedule: Schedule, seed: int, policy: PolicyConfig, clean: bool) -> str:
    return "|".join((case.family, case.template, "clean" if clean else schedule.name, str(seed), policy.name))


def _row_key(row: dict[str, Any]) -> str:
    schedule = "clean" if row.get("condition") == "clean" else row["schedule"]
    return "|".join((row["family"], row["template"], schedule, str(row["seed"]), row["policy"]))


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if target.is_dir():
        target = target / "rows.jsonl"
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text().splitlines() if line.strip()]


def run_suite(config: SuiteConfig, out: str | Path, *, progress: bool = True) -> dict[str, Any]:
    """Run every missing cell of a frozen suite; return counts."""
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    current = manifest(config)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous.get("config_sha256") != current["config_sha256"]:
            raise RuntimeError(
                f"{manifest_path} was frozen with a different configuration; use a new output directory",
            )
        if previous.get("code", {}).get("git_sha") != current["code"]["git_sha"]:
            raise RuntimeError(f"{manifest_path} was produced by commit {previous['code'].get('git_sha')}; "
                               "rerun into a new directory to change code")
    else:
        manifest_path.write_text(json.dumps(current, indent=2, sort_keys=True, default=str))
    done = {_row_key(row) for row in load_rows(directory)}
    ran = skipped = errors = 0
    events_dir = directory / "events"
    with (directory / "rows.jsonl").open("a") as sink:
        for case, schedule, seed, policy, clean in cells(config):
            key = cell_key(case, schedule, seed, policy, clean)
            if key in done:
                skipped += 1
                continue
            result = run_cell(
                case, schedule, seed, policy,
                verifier=config.verifier, clean=clean, horizon=config.horizon,
                correction_budget=config.correction_budget, keep_events=config.keep_events,
            )
            row = result.row | {"suite_name": config.name, "config_sha256": current["config_sha256"]}
            sink.write(json.dumps(row, default=str) + "\n")
            sink.flush()
            if config.keep_events:
                events_dir.mkdir(exist_ok=True)
                name = key.replace("|", "__").replace(":", "_") + ".jsonl.gz"
                with gzip.open(events_dir / name, "wt") as handle:
                    for event in result.events:
                        handle.write(json.dumps(event, default=str) + "\n")
            ran += 1
            if row.get("outcome") == "infrastructure_error":
                errors += 1
            if progress and ran % 100 == 0:
                print(f"{config.name}: {ran} cells run, {skipped} skipped", file=sys.stderr, flush=True)
    return {"ran": ran, "skipped": skipped, "infrastructure_errors": errors, "out": str(directory)}


__all__ = ["BOUNDARY_ABLATIONS", "SuiteConfig", "cells", "load_rows", "manifest", "preset", "run_suite"]
