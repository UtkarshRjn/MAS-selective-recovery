"""Task selection and the frozen coding-pilot configuration.

Selection: cachetools and marshmallow are fixed by the evaluation plan. Two
more tasks come from the frozen release by a rule over the executable
dependency manifests, not by repository name:

1. official-result-eligible Commit0 tasks (the compiler tasks need a separate
   toolchain and are out of scope for the pilot);
2. rank by executable cross-agent dependency points, then by interface-type
   points among them, then by fewer writable files, then by task id;
3. take candidates in rank order whose reference workspace passes every
   integrated probe and the full evaluator, and whose base fails at least one
   probe, in the pilot's executor.

Fault eligibility: for each executable dependency point, a producer function
is eligible when reverting it to the Commit0 stub in the reference workspace
makes that point's upstream probe fail. The intervention reverts the same
function in the producer's first artifact.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .registry import Registry, TaskRecord
from .workspace import RepoCache, functions, integrate, make_patch, revert_function

REQUIRED = ("cachetools", "marshmallow")
EXTRA = 2


@dataclass
class Candidate:
    task_id: str
    executable_points: int
    interface_points: int
    writable_files: int
    rank: int = 0
    executable_locally: bool | None = None
    reason: str = ""


def rank_candidates(registry: Registry) -> list[Candidate]:
    out = []
    for task in registry.tasks.values():
        if not task.official_result_eligible or task.benchmark != "Commit0":
            continue
        executable = task.executable_points
        out.append(Candidate(
            task.task_id,
            len(executable),
            sum(1 for point in executable if point.dependency_type == "interface_dependency"),
            sum(len(item.writable_paths) for item in task.assignments),
        ))
    out.sort(key=lambda item: (-item.executable_points, -item.interface_points, item.writable_files, item.task_id))
    for index, item in enumerate(out, 1):
        item.rank = index
    return out


def reference_workspace(cache: RepoCache, task: TaskRecord, root: Path) -> tuple[Path, Path, list[dict[str, Any]]]:
    base = cache.materialize(task, root / task.short_name / "base")
    patches = [make_patch(item.agent_id, cache.reference_files(task, item.writable_paths)) for item in task.assignments]
    ref = root / task.short_name / "reference"
    integrate(base, patches, ref)
    return base, ref, patches


def executable(task: TaskRecord, cache: RepoCache, executor, root: Path) -> tuple[bool, str]:
    base, ref, _patches = reference_workspace(cache, task, root)
    probes = task.probes
    ref_probes = executor.run(task, ref, probes)
    if not ref_probes.all_passed(probes):
        return False, f"reference fails probes {ref_probes.failing(probes)[:3]}"
    full = executor.run(task, ref, task.evaluator_targets)
    if full.failed or not full.passed:
        return False, f"reference fails the evaluator ({len(full.failed)} failures)"
    base_probes = executor.run(task, base, probes)
    if base_probes.all_passed(probes):
        return False, "the base already passes every probe"
    return True, f"reference passes {len(probes)} probes and {len(full.passed)} evaluator tests"


def select_tasks(registry: Registry, cache: RepoCache, executor, root: Path) -> dict[str, Any]:
    ranked = rank_candidates(registry)
    chosen = [registry.get(name).task_id for name in REQUIRED]
    for item in ranked:
        if item.task_id in chosen:
            item.reason = "required by the plan"
            continue
        if len(chosen) >= len(REQUIRED) + EXTRA:
            break
        ok, reason = executable(registry.tasks[item.task_id], cache, executor, root)
        item.executable_locally = ok
        item.reason = reason
        if ok:
            chosen.append(item.task_id)
    for name in REQUIRED:
        task = registry.get(name)
        ok, reason = executable(task, cache, executor, root)
        for item in ranked:
            if item.task_id == task.task_id:
                item.executable_locally, item.reason = ok, f"required by the plan; {reason}"
        if not ok:
            raise RuntimeError(f"required task {name} is not executable: {reason}")
    return {"rule": __doc__.split("Fault eligibility")[0].strip(), "selected": chosen,
            "ranking": [asdict(item) for item in ranked]}


@dataclass
class FaultSite:
    dependency_id: str
    producer: str
    consumer: str
    path: str
    function: str
    upstream_failures: tuple[str, ...]


def eligible_faults(task: TaskRecord, cache: RepoCache, executor, root: Path, *, limit: int = 3) -> list[FaultSite]:
    """Producer functions whose reversion to the stub breaks the point's upstream probe in the reference."""
    base, ref, patches = reference_workspace(cache, task, root)
    by_agent = {patch["agent"]: patch for patch in patches}
    sites: list[FaultSite] = []
    for point in task.executable_points:
        found = 0
        for path in point.producer_files:
            reference = by_agent.get(point.producer_agent, {}).get("files", {}).get(path)
            stub = (base / path).read_text() if (base / path).exists() else None
            if reference is None or stub is None:
                continue
            stubbed = set(functions(stub))
            for name in functions(reference):
                if name not in stubbed:
                    continue
                mutated = revert_function(reference, stub, name)
                if mutated is None:
                    continue
                trial = [
                    make_patch(patch["agent"], {**patch["files"], path: mutated})
                    if patch["agent"] == point.producer_agent else patch
                    for patch in patches
                ]
                workspace = root / task.short_name / "trial"
                integrate(base, trial, workspace)
                run = executor.run(task, workspace, point.upstream_probes)
                failing = run.failing(point.upstream_probes)
                if failing:
                    sites.append(FaultSite(point.dependency_id, point.producer_agent, point.consumer_agent,
                                           path, name, failing))
                    found += 1
                    if found >= limit:
                        break
            if found >= limit:
                break
    return sites


@dataclass
class ModelSpec:
    name: str
    model: str
    base_url: str
    temperature: float = 0.0
    # Reasoning models can think for tens of thousands of tokens before writing a file; let them finish.
    max_tokens: int = 131072


@dataclass
class PilotConfig:
    """Everything a coding-pilot row depends on. Written once; runs refuse a different config."""

    registry: dict[str, Any]
    selection: dict[str, Any]
    faults: dict[str, list[dict[str, Any]]]
    handoff: dict[str, dict[str, Any]]
    scopes: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    interventions: tuple[str, ...] = ("clean", "inflight", "pre_merge")
    policies: tuple[str, ...] = ("restart", "suffix", "claim_rerun", "online", "asyncodebench_manager_style")
    seeds: tuple[int, ...] = (0, 1, 2)
    models: tuple[ModelSpec, ...] = ()
    goal: str = "evaluator"
    executor: str = "local"
    agent_steps: int = 3
    tests_per_agent: int = 3
    token_limit_per_agent: int = 500000
    wall_clock_limit_s: float = 14400.0
    correction_budget: int = 2
    termination: str = "stop at safe completion, budget exhaustion, a stall of 30 min, or the wall-clock limit"
    official_result_eligible: bool = False
    protocol: str = "agentrec-versioned-artifacts-v1"
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    @classmethod
    def load(cls, path: str | Path) -> "PilotConfig":
        data = json.loads(Path(path).read_text())
        data["models"] = tuple(ModelSpec(**item) for item in data.get("models", ()))
        for key in ("interventions", "policies", "seeds"):
            data[key] = tuple(data[key])
        return cls(**data)


def check_scopes(task: TaskRecord, cache: RepoCache, executor, root: Path, handoff: dict[str, Any]) -> dict[str, list[str]]:
    """Which patches each specialist's checker integrates before running its probes.

    The narrow scope is the specialist plus its upstream producers. AsynCodeBench
    defines probes on integrated checkpoints, and some suites cannot even be
    collected without other layers (marshmallow's conftest builds schemas), so a
    specialist whose probes fail in the narrow scope on the reference is checked on
    the integrated checkpoint of every patch instead. The reference only configures
    the checker; no agent sees it.
    """
    base, _ref, patches = reference_workspace(cache, task, root)
    by_agent = {patch["agent"]: patch for patch in patches}
    order = handoff["order"]
    scopes: dict[str, list[str]] = {}
    for agent in order:
        narrow = [src for src, dst in handoff["edges"] if dst == agent] + [agent]
        narrow = [name for name in order if name in narrow]
        tests = tuple(dict.fromkeys(
            test for point in task.executable_points
            for test in (
                (*point.upstream_probes,) if point.producer_agent == agent else ()
            ) + (
                (*point.downstream_probes,)
                if point.consumer_agent == agent and point.producer_agent in narrow else ()
            )
        )) or task.assignment(agent).primary_tests
        workspace = root / task.short_name / "scope"
        integrate(base, [by_agent[name] for name in narrow], workspace)
        run = executor.run(task, workspace, tests)
        scopes[agent] = narrow if run.all_passed(tests) else list(order)
    return scopes


def handoff_order(task: TaskRecord) -> dict[str, Any]:
    """A frozen acyclic handoff: keep producer->consumer edges that follow the scenario's agent order.

    Edges against that order would form cycles (marshmallow's field and schema layers depend
    on each other); they are recorded and resolved only at integration.
    """
    order = [item.agent_id for item in task.assignments]
    kept: set[tuple[str, str]] = set()
    dropped: set[tuple[str, str]] = set()
    for point in task.executable_points:
        edge = (point.producer_agent, point.consumer_agent)
        if point.producer_agent not in order or point.consumer_agent not in order:
            continue
        (kept if order.index(edge[0]) < order.index(edge[1]) else dropped).add(edge)
    return {"order": order, "edges": sorted(kept), "deferred_to_integration": sorted(dropped)}


__all__ = [
    "Candidate", "EXTRA", "FaultSite", "ModelSpec", "PilotConfig", "REQUIRED", "check_scopes", "eligible_faults",
    "executable",
    "handoff_order", "rank_candidates", "reference_workspace", "select_tasks",
]
