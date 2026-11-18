"""Freeze and run the WorkBench stateful pilot.

`freeze` writes `pilot.json` once: the WorkBench commit, the selection rule
with the selected rows and every exclusion and its reason, the templates'
decompositions and faults, policies, seeds, models, agent limits, and the
termination rule. `run` executes every missing cell into an append-only
`rows.jsonl`, refusing a changed configuration or commit.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .. import policies as policy_registry
from ..codepilot.freeze import ModelSpec
from ..codepilot.pilot import llm_factory
from ..mechanism.harness import run_cell
from ..mechanism.suite import _git, finished_cells, load_rows
from .agents import ModelAgent, ReferenceAgent
from .case import INTERVENTIONS, PROTOCOL, WorkBenchCase
from .source import WorkBench, action_string
from .templates import BY_KEY, TEMPLATES

REFERENCE = ModelSpec("reference", "reference", "")
PER_TEMPLATE = 3
SELECTION_RULE = (
    f"For each template in templates.TEMPLATES, the first {PER_TEMPLATE} rows of "
    "multi_domain_tasks_and_outcomes.csv in file order whose instruction parses and whose deterministic "
    "reference reproduces the ground truth under WorkBench's is_correct."
)


@dataclass
class WorkBenchPilotConfig:
    source: dict[str, Any]
    selection: dict[str, Any]
    templates: dict[str, Any]
    interventions: tuple[str, ...] = ("clean", *INTERVENTIONS)
    policies: tuple[str, ...] = ("restart", "suffix", "claim_rerun", "online", "atomix_style")
    seeds: tuple[int, ...] = (0, 1, 2)
    models: tuple[ModelSpec, ...] = ()
    max_turns: int = 8
    max_tokens: int = 1024
    token_limit_per_agent: int = 30000
    trigger_max_wait_s: float = 120.0
    wall_clock_limit_s: float = 900.0
    correction_budget: int = 2
    termination: str = "stop at safe completion, budget exhaustion, or the wall-clock limit"
    official_result_eligible: bool = False
    protocol: str = PROTOCOL
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    @classmethod
    def load(cls, path: str | Path) -> "WorkBenchPilotConfig":
        data = json.loads(Path(path).read_text())
        data["models"] = tuple(ModelSpec(**item) for item in data.get("models", ()))
        for key in ("interventions", "policies", "seeds"):
            data[key] = tuple(data[key])
        return cls(**data)


def config_sha(config: WorkBenchPilotConfig) -> str:
    return hashlib.sha256(config.to_json().encode()).hexdigest()


def select(wb: WorkBench, per_template: int = PER_TEMPLATE) -> dict[str, Any]:
    agent = ReferenceAgent(wb)
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    tasks = wb.tasks()
    for template in TEMPLATES:
        taken = 0
        for task in tasks:
            if taken >= per_template:
                break
            if template.marker not in task.base_template:
                continue
            try:
                params = template.parse(task.task)
            except ValueError:
                excluded.append({"row": task.index, "template": template.key, "reason": "instruction does not parse"})
                continue
            found: dict[str, Any] = {}
            for lookup in template.lookups:
                found[lookup.claim] = lookup.derive(wb, params, found)
            actions = [action_string(tool, args) for tool, args in template.decide(params, found)]
            if not wb.score(actions, list(task.outcome))["workbench_correct"]:
                excluded.append({"row": task.index, "template": template.key,
                                 "reason": "reference does not reproduce the ground truth"})
                continue
            selected.append({"row": task.index, "template": template.key, "task": task.task,
                             "outcome": list(task.outcome)})
            taken += 1
    return {"rule": SELECTION_RULE, "selected": selected, "excluded": excluded}


def describe_templates() -> dict[str, Any]:
    return {
        template.key: {
            "marker": template.marker,
            "description": template.description,
            "lookups": [{"claim": item.claim, "instruction": item.instruction, "tools": list(item.tools),
                         "inputs": list(item.inputs), "checked_fields": list(item.key_fields)}
                        for item in template.lookups],
            "effect_tools": list(template.effect_tools),
            "fault_claim": template.fault_claim,
        }
        for template in TEMPLATES
    }


def freeze(root: str | Path, out: str | Path, *, models: tuple[ModelSpec, ...], commit: str | None = None,
           **overrides: Any) -> WorkBenchPilotConfig:
    path = Path(out) / "pilot.json"
    if path.exists():
        raise FileExistsError(f"{path} is frozen; write a new pilot directory")
    wb = WorkBench(root, commit=commit)
    config = WorkBenchPilotConfig(
        source=wb.identity(), selection=select(wb), templates=describe_templates(), models=models, **overrides,
        notes=[f"{PROTOCOL}: decomposed WorkBench tasks with injected lookup faults; not a WorkBench result"],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.to_json())
    return config


def cells(config: WorkBenchPilotConfig, models: tuple[ModelSpec, ...]) -> Iterator[tuple[dict[str, Any], str, int, str, ModelSpec]]:
    for item in config.selection["selected"]:
        for model in models:
            for intervention in config.interventions:
                for seed in config.seeds:
                    for policy in config.policies:
                        yield item, intervention, seed, policy, model


def cell_key(row: int, intervention: str, seed: int, policy: str, model: str) -> str:
    return f"{row}|{intervention}|{seed}|{policy}|{model}"


def run(directory: str | Path, *, reference: bool = False, rows: tuple[int, ...] | None = None,
        policies: tuple[str, ...] | None = None, progress: bool = True) -> dict[str, Any]:
    directory = Path(directory)
    config = WorkBenchPilotConfig.load(directory / "pilot.json")
    sha = config_sha(config)
    out = directory / ("reference" if reference else "rows")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"config_sha256": sha, "code": {"git_sha": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))},
                "python": sys.version, "protocol": PROTOCOL, "reference": reference}
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous["config_sha256"] != sha:
            raise RuntimeError(f"{manifest_path} was frozen with a different pilot configuration")
        if previous["code"]["git_sha"] != manifest["code"]["git_sha"]:
            raise RuntimeError(f"{manifest_path} was produced by commit {previous['code']['git_sha']}")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    wb = WorkBench(config.source["root"], commit=config.source["commit"])
    tasks = {task.index: task for task in wb.tasks()}
    done = finished_cells(load_rows(out))
    models = (REFERENCE,) if reference else config.models
    built: dict[tuple[int, str], Any] = {}
    ran = skipped = 0
    with (out / "rows.jsonl").open("a") as sink:
        for item, intervention, seed, policy_name, model in cells(config, models):
            if rows and item["row"] not in rows:
                continue
            if policies and policy_name not in policies:
                continue
            key = cell_key(item["row"], intervention, seed, policy_name, model.name)
            if key in done:
                skipped += 1
                continue
            if (item["row"], model.name) not in built:
                if model.name == REFERENCE.name:
                    agent = ReferenceAgent(wb)
                else:
                    agent = ModelAgent(llm_factory(model), wb, name=model.name, max_turns=config.max_turns,
                                       max_tokens=config.max_tokens, token_limit=config.token_limit_per_agent)
                built[(item["row"], model.name)] = WorkBenchCase(
                    wb, tasks[item["row"]], BY_KEY[item["template"]], agent, corrector=agent,
                    max_wait_s=config.trigger_max_wait_s,
                ).build()
            case = built[(item["row"], model.name)]
            result = run_cell(
                case, case.schedule(intervention), seed, policy_registry.get(policy_name),
                clean=intervention == "clean", horizon=config.wall_clock_limit_s,
                correction_budget=config.correction_budget, real_clock=True,
            )
            row = result.row | {
                "cell": key, "intervention": intervention, "model": model.name, "model_id": model.model,
                "template": f"pilot:{model.name}", "cluster": item["template"],
                "config_sha256": sha, "protocol": PROTOCOL, "official_result_eligible": False,
            }
            sink.write(json.dumps(row, default=str) + "\n")
            sink.flush()
            ran += 1
            if progress:
                print(f"{key}: {row.get('outcome')} {row.get('makespan')}s", file=sys.stderr, flush=True)
    return {"ran": ran, "skipped": skipped, "out": str(out)}


def official_command(directory: str | Path, *, model_name: str) -> tuple[list[str], Path, Path]:
    """WorkBench's own single-agent inference on the selected rows (clean, official protocol)."""
    import csv

    from .source import TASK_FILE

    directory = Path(directory).resolve()
    config = WorkBenchPilotConfig.load(directory / "pilot.json")
    root = Path(config.source["root"])
    subset = directory / "official_tasks.csv"
    wanted = {item["row"] for item in config.selection["selected"]}
    with (root / TASK_FILE).open(newline="") as handle:
        reader = csv.DictReader(handle)
        kept = [row for index, row in enumerate(reader) if index in wanted]
        fields = reader.fieldnames or []
    with subset.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    command = ["uv", "run", "workbench-inference", "--model_name", model_name, "--tasks_path", str(subset)]
    return command, root, subset


def official(directory: str | Path, *, model_name: str, execute: bool = False) -> str:
    import shlex
    import subprocess

    command, root, _subset = official_command(directory, model_name=model_name)
    if execute:
        subprocess.run(command, cwd=root, check=True)
    return f"cd {shlex.quote(str(root))} && {shlex.join(command)}"


__all__ = ["REFERENCE", "SELECTION_RULE", "WorkBenchPilotConfig", "cell_key", "cells", "config_sha", "freeze",
           "official", "official_command", "run", "select"]
