"""Freeze and run the coding pilot.

`freeze` writes `pilot.json` once: the verified registry identity, the selected
tasks with the selection rule, every eligible fault site with the chosen one,
the handoff order, the checker scopes, policies, seeds, models, limits, and the
termination rule. `run` executes every cell of a frozen pilot into an
append-only `rows.jsonl`, refusing a changed configuration or commit, and
skipping finished cells.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from .. import policies as policy_registry
from ..mechanism.harness import run_cell
from ..mechanism.suite import _git, finished_cells, load_rows
from .agents import ModelAgent, ReferenceAgent
from .case import CodingCase, choose_fault
from .freeze import (
    ModelSpec,
    PilotConfig,
    check_scopes,
    eligible_faults,
    handoff_order,
    reference_workspace,
    select_tasks,
)
from .protocol import DECLARATION
from .registry import load_registry
from .workspace import DockerExecutor, LocalExecutor, RepoCache

REFERENCE = ModelSpec("reference", "reference", "")


def config_sha(config: PilotConfig) -> str:
    return hashlib.sha256(config.to_json().encode()).hexdigest()


def freeze(
    registry_root: str | Path,
    out: str | Path,
    *,
    models: tuple[ModelSpec, ...],
    executor: str = "local",
    scratch: str | Path | None = None,
    allow_unpinned: bool = False,
    tasks: tuple[str, ...] | None = None,
) -> PilotConfig:
    path = Path(out) / "pilot.json"
    if path.exists():
        raise FileExistsError(f"{path} is frozen; write a new pilot directory")
    registry = load_registry(registry_root, allow_unpinned=allow_unpinned)
    cache = RepoCache(benchmark_root=registry.root)
    runner = make_executor(executor, registry)
    root = Path(scratch or Path(out) / "freeze")
    selection = select_tasks(registry, cache, runner, root) if tasks is None else {
        "rule": "given on the command line", "selected": [registry.get(name).task_id for name in tasks], "ranking": [],
    }
    faults: dict[str, list[dict[str, Any]]] = {}
    handoff: dict[str, dict[str, Any]] = {}
    scopes: dict[str, dict[str, list[str]]] = {}
    notes = []
    for task_id in selection["selected"]:
        task = registry.tasks[task_id]
        handoff[task_id] = handoff_order(task)
        sites = [asdict(site) for site in eligible_faults(task, cache, runner, root, limit=1)]
        chosen = choose_fault(sites, handoff[task_id])
        faults[task_id] = sites
        if chosen is None:
            notes.append(f"{task_id}: no eligible site on a kept handoff edge; interventions are skipped")
        scopes[task_id] = check_scopes(task, cache, runner, root, handoff[task_id])
    config = PilotConfig(
        registry=registry.identity(), selection=selection, faults=faults, handoff=handoff, scopes=scopes,
        models=models, executor=executor, notes=[*notes, f"protocol: {json.dumps(DECLARATION, sort_keys=True)}"],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.to_json())
    return config


def make_executor(kind: str, registry=None):
    if kind == "local":
        return LocalExecutor()
    if kind == "docker":
        return DockerExecutor()
    raise ValueError(f"unknown executor {kind}")


# Well above one 8k-token generation; a request still silent after this is retried as a hang.
REQUEST_TIMEOUT_S = 300.0


def llm_factory(spec: ModelSpec):
    from ..llm import ServerLLM

    def build(seed: int):
        return ServerLLM(
            base_url=spec.base_url, model=spec.model, temperature=spec.temperature, seed=seed,
            max_tokens=spec.max_tokens, timeout_s=REQUEST_TIMEOUT_S, api_key=os.environ.get("AGENTREC_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        )

    return build


def cells(config: PilotConfig, *, models: tuple[ModelSpec, ...] | None = None) -> Iterator[tuple[str, str, int, str, ModelSpec]]:
    for task_id in config.selection["selected"]:
        for model in models or config.models:
            for intervention in config.interventions:
                for seed in config.seeds:
                    for policy in config.policies:
                        yield task_id, intervention, seed, policy, model


def cell_key(task_id: str, intervention: str, seed: int, policy: str, model: str) -> str:
    return f"{task_id}|{intervention}|{seed}|{policy}|{model}"


def run(
    directory: str | Path,
    *,
    reference: bool = False,
    tasks: tuple[str, ...] | None = None,
    policies: tuple[str, ...] | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    """Run every missing cell. `reference` swaps every model for the reference agent (pipeline validation)."""
    directory = Path(directory)
    config = PilotConfig.load(directory / "pilot.json")
    sha = config_sha(config)
    rows_dir = directory / ("reference" if reference else "rows")
    rows_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"config_sha256": sha, "code": {"git_sha": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))},
                "python": sys.version, "protocol": DECLARATION, "reference": reference}
    manifest_path = rows_dir / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous["config_sha256"] != sha:
            raise RuntimeError(f"{manifest_path} was frozen with a different pilot configuration")
        if previous["code"]["git_sha"] != manifest["code"]["git_sha"]:
            raise RuntimeError(f"{manifest_path} was produced by commit {previous['code']['git_sha']}")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    root = Path(config.registry["root"])
    registry = load_registry(root, allow_unpinned=not config.registry.get("pinned", True))
    cache = RepoCache(benchmark_root=registry.root)
    executor = make_executor(config.executor, registry)
    done = finished_cells(load_rows(rows_dir))
    models = (REFERENCE,) if reference else config.models
    workdir = directory / "work"
    built: dict[tuple[str, str], Any] = {}
    ran = skipped = 0
    with (rows_dir / "rows.jsonl").open("a") as sink:
        for task_id, intervention, seed, policy_name, model in cells(config, models=models):
            if tasks and registry.tasks[task_id].short_name not in tasks and task_id not in tasks:
                continue
            if policies and policy_name not in policies:
                continue
            key = cell_key(task_id, intervention, seed, policy_name, model.name)
            if key in done:
                skipped += 1
                continue
            task = registry.tasks[task_id]
            site = choose_fault(config.faults.get(task_id, []), config.handoff[task_id])
            if intervention != "clean" and site is None:
                continue
            if (task_id, model.name) not in built:
                base, _ref, _patches = reference_workspace(cache, task, workdir / "trees")
                if model.name == REFERENCE.name:
                    agent = ReferenceAgent(cache)
                else:
                    agent = ModelAgent(
                        llm_factory(model), executor, name=model.name, steps=config.agent_steps,
                        tests=config.tests_per_agent, token_limit=config.token_limit_per_agent,
                        max_tokens=model.max_tokens,
                    )
                built[(task_id, model.name)] = CodingCase(
                    task, base=base, workdir=workdir / task.short_name, agent=agent, executor=executor,
                    handoff=config.handoff[task_id], fault=site, scopes=config.scopes.get(task_id),
                    goal=config.goal, corrector=agent, wall_clock_limit_s=config.wall_clock_limit_s,
                ).build()
            case = built[(task_id, model.name)]
            schedule = case.schedule(case.schedules[0].name if intervention == "clean" else intervention)
            policy = policy_registry.get(policy_name)
            result = run_cell(
                case, schedule, seed, policy, clean=intervention == "clean", horizon=config.wall_clock_limit_s,
                correction_budget=config.correction_budget, real_clock=True,
            )
            row = result.row | {
                "cell": key, "intervention": intervention, "model": model.name, "model_id": model.model,
                "template": f"pilot:{model.name}", "cluster": task.short_name,
                "config_sha256": sha, "protocol": DECLARATION["protocol"], "official_result_eligible": False,
            }
            sink.write(json.dumps(row, default=str) + "\n")
            sink.flush()
            ran += 1
            if progress:
                print(f"{key}: {row.get('outcome')} {row.get('makespan')}s", file=sys.stderr, flush=True)
    return {"ran": ran, "skipped": skipped, "out": str(rows_dir)}


def official_command(registry_root: str | Path, task: str, *, env_file: str, model_tag: str, run_version: str) -> list[str]:
    """The release's own Async-Manager run for the clean condition (official protocol, official budget)."""
    script = Path(registry_root) / "scripts" / "run_asyncodebench_five_protocols_env.sh"
    env = {
        "RUN_SINGLE": "0", "RUN_SERIAL": "0", "RUN_ASYNC_PRIVATE": "0", "RUN_CAID": "0", "RUN_ASYNC_MANAGER": "1",
        "ENV_FILE": env_file, "MODEL_TAG": model_tag, "RUN_VERSION": run_version,
    }
    return ["env", *(f"{key}={value}" for key, value in env.items()), str(script), task]


def official(registry_root: str | Path, task: str, *, env_file: str, model_tag: str, run_version: str,
             execute: bool = False) -> str:
    command = official_command(registry_root, task, env_file=env_file, model_tag=model_tag, run_version=run_version)
    if execute:
        subprocess.run(command, cwd=registry_root, check=True)
    return shlex.join(command)


__all__ = ["REFERENCE", "cell_key", "cells", "config_sha", "freeze", "official", "official_command", "run"]
