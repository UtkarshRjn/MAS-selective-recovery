"""An AsynCodeBench task as a monitored, versioned-artifact workflow.

Protocol extension `agentrec-versioned-artifacts-v1` (not an official
AsynCodeBench protocol; rows carry `official_result_eligible = false`):

* Each specialist of the release's async-private decomposition is a step that
  writes one patch artifact (the full content of its writable files) whose
  digest covers paths and contents.
* A consumer starts from the base tree plus the provisional patches of its
  upstream producers along a frozen acyclic handoff order. Edges against that
  order are resolved only at integration.
* A checker step per specialist runs that specialist's dependency probes
  (upstream probes where it produces, downstream probes where it consumes along
  a kept edge) and writes a report bound to the patch digest, the environment,
  and the probe suite.
* Integration merges the patches in the frozen order and proposes `merge`
  through the gated tool; the contract requires a passing, applicable report
  for every merged patch.
* An intervention reverts one frozen-eligible producer function to its Commit0
  stub in the producer's first artifact. The failed check is released after
  the consumer starts (`inflight`) or after the merge is proposed (`pre_merge`).
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Any

from ..action_contracts import ActionContract, approve, reject
from ..contract import ArtifactRef, Evidence, applies, content_digest
from ..fault_schedule import FaultSpec
from ..verifiers import CheckContext, applicable_report_check, failed, live_versions, passed
from .agents import CodingAgent, SolveRequest
from .registry import TaskRecord
from .workspace import TestRun, integrate, make_patch, revert_function
from ..mechanism.cases import lookup_tools
from ..mechanism.spec import (
    ActionSpec,
    CaseSpec,
    Reference,
    Schedule,
    StepContext,
    StepSpec,
    Trigger,
    World,
    effect_tool,
)

PROTOCOL = "agentrec-versioned-artifacts-v1"
INTERVENTIONS = ("inflight", "pre_merge")


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:20]


class CodingCase:
    """Builds the CaseSpec for one task, agent, and executor."""

    def __init__(
        self,
        task: TaskRecord,
        *,
        base: Path,
        workdir: Path,
        agent: CodingAgent,
        executor: Any,
        handoff: dict[str, Any],
        fault: dict[str, Any] | None,
        scopes: dict[str, list[str]] | None = None,
        goal: str = "evaluator",
        corrector: CodingAgent | None = None,
        wall_clock_limit_s: float = 1800.0,
    ) -> None:
        self.task = task
        self.base = base
        self.workdir = workdir
        self.agent = agent
        self.executor = executor
        self.handoff = handoff
        self.fault = fault
        self.goal = goal
        self.corrector_agent = corrector
        self.wall_clock_limit_s = wall_clock_limit_s
        self.faulty: set[str] = set()
        self.agents = [item.agent_id for item in task.assignments]
        self.upstream = {agent: [src for src, dst in handoff["edges"] if dst == agent] for agent in self.agents}
        self.scopes = scopes or {agent: [*self.upstream[agent], agent] for agent in self.agents}
        self.env = ArtifactRef("env", _digest(getattr(executor, "label", type(executor).__name__)))
        self.suite = ArtifactRef("suite", _digest("\n".join(task.probes)))

    # ------------------------------------------------------------ helpers

    def run_dir(self, world: World) -> Path:
        path = world.scratch.get("run_dir")
        if path is None:
            path = self.workdir / "runs" / uuid.uuid4().hex[:12]
            path.mkdir(parents=True, exist_ok=True)
            world.scratch["run_dir"] = path
        return path

    def probes_for(self, agent: str) -> tuple[str, ...]:
        tests: list[str] = []
        for point in self.task.executable_points:
            if point.producer_agent == agent:
                tests.extend(point.upstream_probes)
            if point.consumer_agent == agent and point.producer_agent in self.scopes[agent]:
                tests.extend(point.downstream_probes)
        if not tests:
            tests.extend(self.task.assignment(agent).primary_tests)
        return tuple(dict.fromkeys(tests))

    def _workspace(self, world: World, label: str, patches: list[dict[str, Any]]) -> tuple[Path, dict[str, Any]]:
        path = self.run_dir(world) / f"{label}-{uuid.uuid4().hex[:8]}"
        info = integrate(self.base, patches, path)
        return path, info

    def _ordered(self, patches: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [patches[agent] for agent in self.handoff["order"] if agent in patches]

    # -------------------------------------------------------------- steps

    def _impl(self, agent: str):
        assignment = self.task.assignment(agent)

        async def compute(values: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
            upstream = [values[f"patch:{src}"] for src in self.upstream[agent]]
            workspace, _info = await asyncio.to_thread(self._workspace, ctx.world, f"impl-{agent}", upstream)
            request = SolveRequest(self.task, assignment, workspace, upstream, ctx.attempt_number,
                                   int(ctx.world.scratch.get("seed", 0)))
            files = await asyncio.to_thread(self.agent.solve, request, ctx.meter)
            return {f"patch:{agent}": make_patch(agent, files, upstream={p["agent"]: p["digest"] for p in upstream})}

        return compute

    def _probe(self, agent: str):
        tests = self.probes_for(agent)

        async def compute(values: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
            patches = {name: values[f"patch:{name}"] for name in self.scopes[agent]}
            workspace, _info = await asyncio.to_thread(self._workspace, ctx.world, f"probe-{agent}", self._ordered(patches))
            run: TestRun = await asyncio.to_thread(self.executor.run, self.task, workspace, tests)
            failing = run.failing(tests)
            return {f"report:{agent}": {
                "patch": ctx.evidence[f"patch:{agent}"].digest,
                "passed": not failing,
                "failing": list(failing)[:40],
                "tests": len(tests),
                "output": "" if not failing else run.output[-3000:],
            }}

        return compute

    def _integrate(self, values: dict[str, Any], ctx: StepContext) -> Any:
        patches = {agent: values[f"patch:{agent}"] for agent in self.agents}

        async def run() -> dict[str, Any]:
            workspace, info = await asyncio.to_thread(self._workspace, ctx.world, "merged", self._ordered(patches))
            cited = {agent: ctx.evidence[f"patch:{agent}"].digest for agent in self.agents}
            return {"merged": {**info, "patches": cited, "path": str(workspace)}}

        return run()

    def _merge_arguments(self, values: dict[str, Any], _ctx: StepContext) -> dict[str, Any] | None:
        merged = values["merged"]
        if merged["conflicts"]:
            return None
        return {"workspace": merged["digest"], "patches": dict(merged["patches"]), "path": merged["path"]}

    def steps(self) -> tuple[StepSpec, ...]:
        out: list[StepSpec] = []
        for agent in self.agents:
            out.append(StepSpec(
                f"impl:{agent}", agent, (f"patch:{agent}",), self._impl(agent),
                reads=tuple(f"patch:{src}" for src in self.upstream[agent]), duration=0, tokens=(0, 0),
            ))
        for agent in self.agents:
            out.append(StepSpec(
                f"probe:{agent}", "tester", (f"report:{agent}",), self._probe(agent),
                reads=(f"patch:{agent}", *(f"patch:{name}" for name in self.scopes[agent] if name != agent)),
                applies={f"report:{agent}": f"patch:{agent}"},
                context={f"report:{agent}": tuple(f"patch:{name}" for name in self.scopes[agent] if name != agent)},
                goal=True, checker_step=True,
                duration=0, tokens=(0, 0),
            ))
        out.append(StepSpec(
            "integrate", "integrator", ("merged",), self._integrate,
            reads=tuple(f"patch:{agent}" for agent in self.agents),
            late_reads=tuple(f"report:{agent}" for agent in self.agents),
            bind={f"report:{agent}": f"patch:{agent}" for agent in self.agents},
            context={
                f"report:{agent}": tuple(f"patch:{name}" for name in self.scopes[agent] if name != agent)
                for agent in self.agents
            },
            actions=(ActionSpec("merge", self._merge_arguments),), goal=True, duration=0, tokens=(0, 0),
        ))
        return tuple(out)

    # -------------------------------------------------------------- checks

    def checks(self, _world: World) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        identity = (self.env, self.suite)
        for agent in self.agents:
            checks[f"patch:{agent}"] = applicable_report_check(
                f"report:{agent}", extra=identity,
                context=tuple(f"patch:{name}" for name in self.scopes[agent] if name != agent),
            )

            def report_check(evidence: Evidence, ctx: CheckContext, agent: str = agent):
                known = {item.digest for item in live_versions(ctx, f"patch:{agent}")}
                if evidence.value.get("patch") in known:
                    return passed(evidence)
                return failed(evidence, "report names a patch that is not live")

            checks[f"report:{agent}"] = report_check

        def merged_check(evidence: Evidence, ctx: CheckContext):
            for agent, digest in evidence.value["patches"].items():
                live = [item.digest for item in live_versions(ctx, f"patch:{agent}")]
                if not live or live[-1] != digest:
                    return failed(evidence, f"merged an outdated patch from {agent}")
            if evidence.value["conflicts"]:
                return failed(evidence, "integration left conflicts")
            return passed(evidence)

        checks["merged"] = merged_check
        return checks

    def contracts(self) -> list[ActionContract]:
        identity = (self.env, self.suite)

        def validate(arguments: dict[str, Any], evidence: list[Evidence], ctx) -> Any:
            merged = arguments.get("patches", {})
            for agent, digest in merged.items():
                reports = [item for item in evidence if item.claim_id == f"report:{agent}" and item.value.get("passed")]
                if not reports:
                    return reject(f"no passing report for {agent}")
                checkpoint = tuple(
                    ArtifactRef(f"patch:{name}", merged[name]) for name in self.scopes[agent] if name in merged
                )
                if ctx.enforce_applicability and not any(
                    applies(item.applies_to, (*checkpoint, *identity)) for item in reports
                ):
                    return reject(f"no passing report applies to {agent}@{digest} in the merged checkpoint")
            return approve(arguments)

        return [ActionContract(
            "merge", validate, resource=lambda _a: "branch:main",
            semantic_key=lambda a: f"merge:{a.get('workspace')}", irreversible=True,
        )]

    def tools(self, world: World) -> dict[str, Any]:
        return {"merge": effect_tool(world, "merge", lambda _a: "branch:main", lambda _b, a: dict(a))}

    # ------------------------------------------------------------- faults

    def fault_specs(self) -> tuple[FaultSpec, ...]:
        if self.fault is None:
            return ()
        site = self.fault
        stub = (self.base / site["path"]).read_text()
        producer = site["producer"]

        def corrupt(patch: dict[str, Any]) -> dict[str, Any]:
            files = dict(patch["files"])
            mutated = revert_function(files.get(site["path"], ""), stub, site["function"])
            if mutated is not None:
                files[site["path"]] = mutated
            out = make_patch(producer, files, upstream=patch.get("upstream", {}))
            if mutated is not None:
                self.faulty.add(content_digest(out))
            return out

        return (FaultSpec(
            "contract_regression", f"impl:{producer}", f"patch:{producer}", corrupt, "transient",
            f"{site['function']} in {site['path']} reverted to its stub ({site['dependency_id']})",
        ),)

    def schedules(self) -> tuple[Schedule, ...]:
        if self.fault is None:
            return (Schedule("clean", ()),)
        consumer = self.fault["consumer"]
        wait = self.wall_clock_limit_s
        return (
            Schedule("inflight", ("contract_regression",), {
                "contract_regression": Trigger("after_event", delay=0.5, event="attempt_opened",
                                               claim=f"impl:{consumer}", max_wait=wait),
            }),
            Schedule("pre_merge", ("contract_regression",), {
                "contract_regression": Trigger("after_event", delay=0.5, event="action_proposed",
                                               claim="merge", max_wait=wait),
            }),
        )

    # ----------------------------------------------------------- scoring

    def _merged_path(self, world: World) -> Path | None:
        value, _version = world.store.read("branch:main")
        if not value:
            return None
        return Path(value["path"])

    def _goal_run(self, world: World) -> TestRun | None:
        if "goal_run" in world.scratch:
            return world.scratch["goal_run"]
        path = self._merged_path(world)
        run = None
        if path is not None and path.exists():
            tests = self.task.evaluator_targets if self.goal == "evaluator" else self.task.probes
            run = self.executor.run(self.task, path, tests)
        world.scratch["goal_run"] = run
        return run

    def answer_oracle(self, _final: dict[str, Any], world: World) -> bool:
        run = self._goal_run(world)
        if run is None:
            return False
        if self.goal == "evaluator":
            return bool(run.passed) and not run.failed
        return run.all_passed(self.task.probes)

    def effect_oracle(self, effects, _expected, world: World) -> dict[str, int]:
        merges = [args for tool, args in effects if tool == "merge"]
        forbidden = sum(1 for args in merges if set(args.get("patches", {}).values()) & self.faulty)
        return {
            "forbidden": forbidden,
            "duplicates": max(0, len(merges) - 1),
            "missing": 0 if merges else 1,
            "compensated": 0,
        }

    def row_extras(self, world: World) -> dict[str, Any]:
        path = self._merged_path(world)
        extras: dict[str, Any] = {
            "protocol": PROTOCOL,
            "official_result_eligible": False,
            "task_id": self.task.task_id,
            "agent": getattr(self.agent, "name", ""),
            "executor": getattr(self.executor, "label", ""),
            "fault_site": dict(self.fault) if self.fault else None,
        }
        if path is not None and path.exists():
            probes = self.executor.run(self.task, path, self.task.probes)
            resolved = sum(1 for point in self.task.dependency_points if probes.all_passed(point.integrated_probes))
            extras["adpr"] = resolved / len(self.task.dependency_points) if self.task.dependency_points else None
            goal = self._goal_run(world)
            if goal is not None:
                total = len(goal.passed) + len(goal.failed)
                extras["goal_pass_fraction"] = len(goal.passed) / total if total else 0.0
        else:
            extras["adpr"] = 0.0
            extras["goal_pass_fraction"] = 0.0
        run_dir = world.scratch.get("run_dir")
        if run_dir is not None:
            shutil.rmtree(run_dir, ignore_errors=True)
        return extras

    def reference(self, _schedule: Schedule, _seed: int) -> Reference:
        return Reference({}, {}, [("merge", {})], {}, {}, {}, [])

    async def _correct(self, claim_id: str, world: World, values: dict[str, Any]) -> Any:
        if self.corrector_agent is None or not claim_id.startswith("patch:"):
            return None
        agent = claim_id.split(":", 1)[1]
        current = values.get(claim_id)
        report = values.get(f"report:{agent}") or {}
        upstream = [values[f"patch:{src}"] for src in self.upstream[agent] if f"patch:{src}" in values]
        workspace, _info = await asyncio.to_thread(self._workspace, world, f"correct-{agent}", upstream)
        request = SolveRequest(
            self.task, self.task.assignment(agent), workspace, upstream, 100 + len(self.faulty),
            int(world.scratch.get("seed", 0)),
            previous=dict(current["files"]) if current else None,
            feedback="Failing checks: " + ", ".join(report.get("failing", [])) + "\n" + report.get("output", ""),
        )
        files = await asyncio.to_thread(self.corrector_agent.solve, request, world.meter)
        return make_patch(agent, files, upstream={p["agent"]: p["digest"] for p in upstream})

    def build(self) -> CaseSpec:
        ref: list[CaseSpec] = []
        case = CaseSpec(
            family=f"code:{self.task.short_name}",
            template="pilot",
            description=f"{self.task.task_id} under {PROTOCOL}",
            steps=self.steps(),
            answer_claims=("merged",),
            sources={},
            resources={"branch:main": None},
            faults=self.fault_specs(),
            schedules=self.schedules(),
            checks=self.checks,
            contracts=self.contracts,
            tools=lookup_tools(self.tools),
            corrector=self._correct if self.corrector_agent is not None else None,
            identity=(self.env, self.suite),
            check_latency=0.05,
            correction_latency=0.0,
            tool_latency=0.0,
            effect_oracle=self.effect_oracle,
            suite="coding",
            eager_claims=tuple(f"{kind}:{agent}" for agent in self.agents for kind in ("report", "patch")),
            reference_factory=self.reference,
            answer_oracle=self.answer_oracle,
            row_extras=self.row_extras,
            hold_cone=True,
        )
        ref.append(case)
        return case


def choose_fault(sites: list[dict[str, Any]], handoff: dict[str, Any]) -> dict[str, Any] | None:
    """The first frozen-eligible site whose consumer receives the producer's artifact by handoff."""
    edges = {tuple(edge) for edge in handoff["edges"]}
    for site in sites:
        if (site["producer"], site["consumer"]) in edges:
            return site
    return None


__all__ = ["CodingCase", "INTERVENTIONS", "PROTOCOL", "choose_fault", "content_digest"]
