"""A WorkBench task as a monitored workflow of concurrent lookups, a decision, and gated effects.

Protocol extension `agentrec-workbench-decomposed-v1` (not WorkBench's
protocol; rows carry `official_result_eligible = false`):

* Each lookup of the template is a step (one agent) answered from WorkBench
  read tools on the pristine sandbox; a lookup that needs another's answer
  reads it.
* `decide` reads every lookup and writes the action plan; `act` proposes each
  planned write through the gateway. `email.send_email` is irreversible;
  `calendar.create_event` and `project_management.create_task` are reversible
  through a registered revert.
* Every policy gets the same checks: a lookup is re-derived from WorkBench
  records (using the latest live versions of its inputs); the plan is
  re-derived by the frozen decision rule from the lookup versions it cites and
  compared with WorkBench's own state comparison. Failure reasons never carry
  the true value; the corrector re-asks the same agent.
* The fault corrupts the branch-deciding lookup's first output. Its failed
  check is released after `decide` starts (`inflight`) or after `act` starts
  (`pre_effect`).

The outcome is scored twice: WorkBench's `is_correct` / `has_side_effects` on
the final action list (applied effects minus reverted ones), and the full
effect history against the ground-truth multiset (forbidden, duplicate,
missing, compensated).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from datetime import datetime
from typing import Any

from ..action_contracts import ActionContract, approve, cited, reject
from ..contract import ArtifactRef, Evidence, canonical
from ..fault_schedule import FaultSpec
from ..mechanism.cases import append_to, lookup_tools
from ..mechanism.spec import ActionSpec, CaseSpec, Reference, Schedule, StepContext, StepSpec, Trigger, World, effect_tool
from ..verifiers import CheckContext, failed, live_versions, passed, pending
from .agents import DecideRequest, LookupRequest, WorkBenchAgent
from .source import Task, WorkBench, action_string, parse_action
from .templates import Template

PROTOCOL = "agentrec-workbench-decomposed-v1"
INTERVENTIONS = ("inflight", "pre_effect")
REVERT = "revert:"
RESOURCES = {
    "email.send_email": lambda a: f"mailbox:{a.get('recipient')}",
    "calendar.create_event": lambda a: "calendar:events",
    "project_management.create_task": lambda a: f"board:{a.get('board')}",
}


def _norm(key: str, value: Any) -> Any:
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if key == "start" or "date" in key:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[:19], fmt)
                return str(parsed) if key == "start" else str(parsed.date())
            except ValueError:
                continue
    return text.lower()


def agrees(value: Any, expected: Any, keys: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    return all(_norm(key, value.get(key)) == _norm(key, expected.get(key)) for key in keys)


def plan_effects(plan: Any) -> list[tuple[str, dict[str, str]]]:
    actions = plan.get("actions", []) if isinstance(plan, dict) else []
    return [(str(item["tool"]), dict(item["arguments"])) for item in actions]


class WorkBenchCase:
    def __init__(
        self,
        wb: WorkBench,
        task: Task,
        template: Template,
        agent: WorkBenchAgent,
        *,
        corrector: WorkBenchAgent | None = None,
        max_wait_s: float = 120.0,
    ) -> None:
        self.wb = wb
        self.task = task
        self.template = template
        self.agent = agent
        self.corrector_agent = corrector
        self.max_wait_s = max_wait_s
        self.params = template.parse(task.task)
        self.truth = [parse_action(action) for action in task.outcome]
        self.identity = (ArtifactRef("workbench", "sha256:" + hashlib.sha256(wb.commit.encode()).hexdigest()[:20]),)
        self.corrections: Counter = Counter()

    # -------------------------------------------------------------- steps

    def _lookup(self, claim: str):
        lookup = self.template.lookup(claim)

        async def compute(values: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
            request = LookupRequest(self.task.task, self.template, lookup, self.params,
                                    {name: values[name] for name in lookup.inputs}, ctx.attempt_number,
                                    int(ctx.world.scratch.get("seed", 0)))
            return {claim: await asyncio.to_thread(self.agent.lookup, request, ctx.meter)}

        return compute

    async def _decide(self, values: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
        found = {lookup.claim: values[lookup.claim] for lookup in self.template.lookups}
        request = DecideRequest(self.task.task, self.template, self.params, found, ctx.attempt_number,
                                int(ctx.world.scratch.get("seed", 0)))
        effects = await asyncio.to_thread(self.agent.decide, request, ctx.meter)
        return {"plan": self._plan(effects)}

    @staticmethod
    def _plan(effects: list[tuple[str, dict[str, str]]]) -> dict[str, Any]:
        return {"actions": [{"tool": tool, "arguments": dict(arguments)} for tool, arguments in effects]}

    def _planned(self, tool: str):
        def arguments(values: dict[str, Any], _ctx: StepContext) -> dict[str, Any] | None:
            for planned, args in plan_effects(values["plan"]):
                if planned == tool:
                    return dict(args)
            return None

        return arguments

    def steps(self) -> tuple[StepSpec, ...]:
        out = [
            StepSpec(f"lookup:{lookup.claim}", f"{lookup.claim}_agent", (lookup.claim,), self._lookup(lookup.claim),
                     reads=lookup.inputs, duration=0, tokens=(0, 0))
            for lookup in self.template.lookups
        ]
        out.append(StepSpec("decide", "decision_agent", ("plan",), self._decide,
                            reads=tuple(lookup.claim for lookup in self.template.lookups), duration=0, tokens=(0, 0)))
        out.append(StepSpec(
            "act", "actor", ("acted",),
            lambda values, _ctx: {"acted": {"planned": len(plan_effects(values["plan"]))}},
            reads=("plan",), duration=0, tokens=(0, 0),
            actions=tuple(ActionSpec(tool, self._planned(tool)) for tool in self.template.effect_tools),
        ))
        return tuple(out)

    # -------------------------------------------------------------- checks

    def _lookup_check(self, claim: str):
        lookup = self.template.lookup(claim)

        def check(evidence: Evidence, ctx: CheckContext):
            inputs: dict[str, Any] = {}
            for name in lookup.inputs:
                live = live_versions(ctx, name)
                if not live:
                    return pending(name)
                inputs[name] = max(live, key=lambda item: item.version).value
            try:
                expected = lookup.derive(self.wb, self.params, inputs)
            except Exception:  # noqa: BLE001 - an input the records cannot answer
                return failed(evidence, f"{claim} cannot be re-derived from its inputs")
            if agrees(evidence.value, expected, lookup.key_fields):
                return passed(evidence)
            return failed(evidence, f"{claim} disagrees with the WorkBench records")

        return check

    def _plan_check(self, evidence: Evidence, ctx: CheckContext):
        found: dict[str, Any] = {}
        cited_versions = {ref.claim_id: ref.version for ref in evidence.supporting_evidence}
        for lookup in self.template.lookups:
            versions = ctx.versions(lookup.claim)
            match = [item for item in versions if item.version == cited_versions.get(lookup.claim)]
            if not match:
                live = live_versions(ctx, lookup.claim)
                if not live:
                    return pending(lookup.claim)
                match = [max(live, key=lambda item: item.version)]
            found[lookup.claim] = match[0].value
        try:
            expected = self.template.decide(self.params, found)
        except Exception:  # noqa: BLE001 - malformed lookup answers cannot support any plan
            return failed(evidence, "the plan does not follow from the lookups it cites")
        got = [action_string(tool, args) for tool, args in plan_effects(evidence.value)]
        want = [action_string(tool, args) for tool, args in expected]
        if Counter(got) == Counter(want) or self.wb.score(got, want)["workbench_correct"]:
            return passed(evidence)
        return failed(evidence, "the plan does not follow from the lookups it cites")

    def checks(self, _world: World) -> dict[str, Any]:
        checks: dict[str, Any] = {lookup.claim: self._lookup_check(lookup.claim) for lookup in self.template.lookups}
        checks["plan"] = self._plan_check
        checks["acted"] = lambda evidence, _ctx: passed(evidence)
        return checks

    def contracts(self) -> list[ActionContract]:
        def planned(tool: str):
            def validate(arguments: dict[str, Any], evidence: list[Evidence], _ctx) -> Any:
                plan = cited(evidence, "plan")
                if plan is None:
                    return reject("no plan is cited")
                if (tool, dict(arguments)) not in [(t, dict(a)) for t, a in plan_effects(plan.value)]:
                    return reject(f"{tool} is not in the cited plan")
                return approve(arguments)

            return validate

        out = []
        for tool in self.template.effect_tools:
            irreversible = tool == "email.send_email"
            out.append(ActionContract(
                tool, planned(tool), resource=RESOURCES[tool],
                semantic_key=lambda a, tool=tool: f"{tool}:{canonical(a)}",
                irreversible=irreversible,
                compensate=None if irreversible else (lambda a, tool=tool: (REVERT + tool, dict(a))),
            ))
            if not irreversible:
                out.append(ActionContract(
                    REVERT + tool, lambda a, _e, _c: approve(a), resource=RESOURCES[tool],
                    semantic_key=lambda a, tool=tool: f"{REVERT}{tool}:{canonical(a)}",
                ))
        return out

    def tools(self, world: World) -> dict[str, Any]:
        out = {}
        for tool in self.template.effect_tools:
            out[tool] = effect_tool(world, tool, RESOURCES[tool], append_to(tool))
            if tool != "email.send_email":
                out[REVERT + tool] = effect_tool(world, REVERT + tool, RESOURCES[tool], append_to(REVERT + tool))
        return out

    # ------------------------------------------------------------- faults

    def fault_specs(self) -> tuple[FaultSpec, ...]:
        claim = self.template.fault_claim
        return (FaultSpec("lookup_fault", f"lookup:{claim}", claim, self.template.corrupt, "transient",
                          f"{claim} lookup returns a value that flips the {self.template.key} decision"),)

    def schedules(self) -> tuple[Schedule, ...]:
        wait = self.max_wait_s
        return (
            Schedule("clean", ()),
            Schedule("inflight", ("lookup_fault",), {
                "lookup_fault": Trigger("after_event", delay=0.5, event="attempt_opened", claim="decide", max_wait=wait),
            }),
            Schedule("pre_effect", ("lookup_fault",), {
                "lookup_fault": Trigger("after_event", delay=0.0, event="attempt_opened", claim="act", max_wait=wait),
            }),
        )

    # ----------------------------------------------------------- scoring

    def final_actions(self, world: World) -> list[str]:
        """Applied effects minus those a later revert undid, as WorkBench action strings."""
        reverted = Counter(
            (tool[len(REVERT):], canonical(args)) for tool, args in world.effects if tool.startswith(REVERT)
        )
        out = []
        for tool, args in world.effects:
            if tool.startswith(REVERT):
                continue
            key = (tool, canonical(args))
            if reverted[key] > 0:
                reverted[key] -= 1
                continue
            out.append(action_string(tool, args))
        return out

    def _score(self, world: World) -> dict[str, bool]:
        if "score" not in world.scratch:
            world.scratch["score"] = self.wb.score(self.final_actions(world), list(self.task.outcome))
        return world.scratch["score"]

    def answer_oracle(self, _final: dict[str, Any], world: World) -> bool:
        return self._score(world)["workbench_correct"]

    def row_extras(self, world: World) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "official_result_eligible": False,
            "workbench_task_index": self.task.index,
            "workbench_template": self.template.key,
            "agent": getattr(self.agent, "name", ""),
            "final_actions": self.final_actions(world),
            **self._score(world),
        }

    def reference(self, _schedule: Schedule, _seed: int) -> Reference:
        return Reference({}, {}, [(tool, dict(args)) for tool, args in self.truth], {}, {}, {}, [])

    async def _correct(self, claim_id: str, world: World, values: dict[str, Any]) -> Any:
        if self.corrector_agent is None:
            return None
        self.corrections[claim_id] += 1
        attempt = 100 + self.corrections[claim_id]
        seed = int(world.scratch.get("seed", 0))
        if claim_id == "plan":
            found = {lookup.claim: values.get(lookup.claim) for lookup in self.template.lookups}
            request = DecideRequest(self.task.task, self.template, self.params, found, attempt, seed,
                                    feedback="A check found that your previous plan does not follow from these "
                                             "findings and the task. Decide again carefully.")
            return self._plan(await asyncio.to_thread(self.corrector_agent.decide, request, world.meter))
        lookup = next((item for item in self.template.lookups if item.claim == claim_id), None)
        if lookup is None:
            return None
        request = LookupRequest(self.task.task, self.template, lookup, self.params,
                                {name: values.get(name) for name in lookup.inputs}, attempt, seed,
                                feedback="A check found that a previous answer to this question disagrees with "
                                         "the WorkBench records. Look it up again carefully.")
        return await asyncio.to_thread(self.corrector_agent.lookup, request, world.meter)

    def build(self) -> CaseSpec:
        return CaseSpec(
            family=f"workbench:{self.template.key}",
            template=f"row{self.task.index}",
            description=f"WorkBench row {self.task.index} ({self.template.key}) under {PROTOCOL}",
            steps=self.steps(),
            answer_claims=("plan", "acted"),
            sources={},
            resources={},
            faults=self.fault_specs(),
            schedules=self.schedules(),
            checks=self.checks,
            contracts=self.contracts,
            tools=lookup_tools(self.tools),
            corrector=self._correct if self.corrector_agent is not None else None,
            identity=self.identity,
            check_latency=0.05,
            correction_latency=0.0,
            tool_latency=0.05,
            suite="workbench",
            eager_claims=tuple(lookup.claim for lookup in self.template.lookups),
            reference_factory=self.reference,
            answer_oracle=self.answer_oracle,
            row_extras=self.row_extras,
            hold_cone=True,
        )


__all__ = ["INTERVENTIONS", "PROTOCOL", "REVERT", "WorkBenchCase", "agrees", "plan_effects"]
