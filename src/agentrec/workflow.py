"""Researcher -> planner -> executor travel-booking workflow (W1).

Stages, each recorded as a step in the provenance graph:
  research_flights, research_hotels   researcher agent with search tools
  plan                                planner agent, sees both findings
  exec:<i>                            executor agent, one step per plan item

Every LLM and tool call goes through this module so that inputs, outputs, and side
effects are always recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import agents
from .faults import Injector
from .llm import LLM, OBS_PREFIX
from .provenance import Provenance
from .world import World

MAX_RESEARCH_ITERS = 4
MAX_EXEC_ITERS = 3
MAX_PLAN_STEPS = 8
VERIFY_RETRIES = 1

RESEARCH_TOOLS = {"research_flights": "search_flights", "research_hotels": "search_hotels"}
EXEC_TOOLS = {"book_flight", "book_hotel", "write_file", "send_email"}
# Evidence each executor action cites. Bookings do not cite the refundable claim.
CLAIM_SUPPORT = {
    "book_flight": ("claim:flight_id",),
    "book_hotel": ("claim:hotel_id",),
    "write_file": ("claim:flight_id", "claim:hotel_id", "claim:refundable"),
    "send_email": ("claim:flight_id", "claim:hotel_id", "claim:refundable"),
}


@dataclass
class RunContext:
    task: dict[str, Any]
    world: World
    llm: LLM
    prov: Provenance = field(default_factory=Provenance)
    injector: Injector | None = None
    verify: bool = False
    scenario: dict[str, str] = field(default_factory=dict)
    # step key -> {"fingerprint", "artifacts": [(key, value)], "effects": [...]} from a prior run
    reuse: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed: set[str] = field(default_factory=set)
    # set on a recovery rerun; included only in prompts for steps that are actually redone
    fault_note: dict[str, Any] | None = None


# These faults rewrite one claim. The findings artifact stays unmarked so support
# follows that claim's edges instead of falling back to coarse lineage.
_CLAIM_FAULTS = {"false_refundable": "claim:refundable", "wrong_hotel_id": "claim:hotel_id"}


def run_agents(ctx: RunContext) -> None:
    """Dispatch and preserve exact model exchanges, excluding authentication headers."""
    import copy
    start = len(getattr(ctx.llm, "recorded_calls", []))
    try:
        _run_agents(ctx)
    finally:
        ctx.prov.model_calls = copy.deepcopy(getattr(ctx.llm, "recorded_calls", [])[start:])


def _run_agents(ctx: RunContext) -> None:
    """Travel booking or the coding team."""
    kind = ctx.task.get("kind")
    if kind == "coding":
        from .coding import CodingWorkflow

        CodingWorkflow().run(ctx)
    elif kind == "graph":
        from .graphs import GraphWorkflow
        GraphWorkflow().run(ctx)
    else:
        TravelWorkflow().run(ctx)


def _claim_fault_key(ctx: RunContext) -> str | None:
    spec = ctx.injector.spec if ctx.injector is not None else None
    if spec is None:
        return None
    return _CLAIM_FAULTS.get(spec.type)


def _latest_artifact(prov: Provenance, key: str) -> str | None:
    found = [n for n in prov.of_kind("artifact") if n.key == key]
    return found[-1].id if found else None


def _claim_ids(prov: Provenance) -> list[str]:
    return [cid for key in ("claim:flight_id", "claim:hotel_id", "claim:refundable") if (cid := _latest_artifact(prov, key))]


def _record_claims(
    ctx: RunContext, step: str, key: str, findings: dict[str, Any], obs_id: str | None, output_fired: bool
) -> None:
    """Store atomic claims and cite the observation that supports each one."""
    if key == "research_flights" and findings.get("flight_id"):
        claim = ctx.prov.add_artifact("claim:flight_id", {"flight_id": findings["flight_id"]}, producer=step)
        if obs_id is not None:
            ctx.prov.add_support(obs_id, claim)
        return
    if key != "research_hotels" or not findings.get("hotel_id"):
        return
    claim_fault = _claim_fault_key(ctx) if output_fired else None
    hotel = ctx.prov.add_artifact(
        "claim:hotel_id",
        {"hotel_id": findings["hotel_id"]},
        producer=step,
        **(_fault_kwargs(ctx, True) if claim_fault == "claim:hotel_id" else {"faulty": False}),
    )
    refund = ctx.prov.add_artifact(
        "claim:refundable",
        {"refundable": bool(findings.get("refundable"))},
        producer=step,
        **(_fault_kwargs(ctx, True) if claim_fault == "claim:refundable" else {"faulty": False}),
    )
    if obs_id is not None:
        ctx.prov.add_support(obs_id, hotel)
        ctx.prov.add_support(obs_id, refund)


def _fault_kwargs(ctx: RunContext, fired: bool) -> dict[str, Any]:
    """Record the injector's fault name on the artifact the fault rewrote."""
    if not fired:
        return {"faulty": False}
    spec = ctx.injector.spec if ctx.injector is not None else None
    return {"faulty": True, "fault": None if spec is None else spec.name}


def _turn(out: dict[str, Any], obs: Any) -> list[dict[str, str]]:
    return [
        {"role": "assistant", "content": agents.dumps(out)},
        {"role": "user", "content": OBS_PREFIX + agents.dumps(obs)},
    ]


class TravelWorkflow:
    def run(self, ctx: RunContext) -> None:
        task_art = ctx.prov.add_artifact("task", ctx.task)
        f_id, flights = self.research(ctx, "research_flights", task_art)
        h_id, hotels = self.research(ctx, "research_hotels", task_art)
        findings = {"flights": flights, "hotels": hotels}
        plan_id, plan = self.plan(ctx, task_art, [f_id, h_id], findings)
        steps = plan.get("steps") if isinstance(plan, dict) else None
        steps = steps if isinstance(steps, list) else []
        for i, item in enumerate(steps[:MAX_PLAN_STEPS]):
            self.execute(ctx, i, item, task_art, plan_id, plan, findings)

    # Stages.

    def research(self, ctx: RunContext, key: str, task_art: str) -> tuple[str, Any]:
        reused = self._reuse(ctx, key, "researcher", [task_art])
        if reused is not None:
            return reused
        feedback = None
        for attempt in range(1 + (VERIFY_RETRIES if ctx.verify else 0)):
            before = ctx.llm.usage.copy()
            step = ctx.prov.add_step(key, "researcher", attempt=attempt)
            ctx.prov.add_input(task_art, step)
            messages = agents.researcher_messages(ctx.task, key, feedback, ctx.fault_note)
            observations: list[Any] = []
            findings: Any = None
            obs_id: str | None = None
            for it in range(MAX_RESEARCH_ITERS):
                out = ctx.llm.chat("researcher", messages)
                if out.get("action") == "final":
                    findings = out.get("findings")
                    break
                obs = self._research_tool(ctx, key, out)
                fired = False
                if ctx.injector is not None:
                    obs, fired = ctx.injector.on_observation(key, out.get("action"), ctx.task, obs)
                obs_id = ctx.prov.add_artifact(f"{key}/obs{it}", obs, producer=step, **_fault_kwargs(ctx, fired))
                ctx.prov.add_input(obs_id, step)
                observations.append(obs)
                messages += _turn(out, obs)
            if not isinstance(findings, dict):
                findings = {"error": "no findings produced"}
            fired = False
            if ctx.injector is not None:
                findings, fired = ctx.injector.on_output(key, ctx.task, findings, observations)
            # A single-claim fault is recorded on that claim, not on the whole finding.
            mark_finding = fired and _claim_fault_key(ctx) is None
            art = ctx.prov.add_artifact(key, findings, producer=step, **_fault_kwargs(ctx, mark_finding))
            _record_claims(ctx, step, key, findings, obs_id, fired)
            ok, feedback = self._check(ctx, "findings", {"observations": observations}, findings)
            ctx.prov.update(step, usage=ctx.llm.usage.since(before), verified=ok if ctx.verify else None)
            if ok:
                break
            ctx.prov.update(step, rejected=True)
        return art, findings

    def plan(
        self, ctx: RunContext, task_art: str, finding_ids: list[str], findings: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        reused = self._reuse(ctx, "plan", "planner", [task_art, *finding_ids])
        if reused is not None:
            return reused
        feedback = None
        for attempt in range(1 + (VERIFY_RETRIES if ctx.verify else 0)):
            before = ctx.llm.usage.copy()
            step = ctx.prov.add_step("plan", "planner", attempt=attempt)
            for a in (task_art, *finding_ids, *_claim_ids(ctx.prov)):
                ctx.prov.add_input(a, step)
            for claim_id in _claim_ids(ctx.prov):
                ctx.prov.add_support(claim_id, step)
            out = ctx.llm.chat("planner", agents.planner_messages(ctx.task, findings, feedback, ctx.fault_note))
            plan = out if isinstance(out.get("steps"), list) else {"steps": [], "error": out.get("error", "no steps")}
            fired = False
            if ctx.injector is not None:
                plan, fired = ctx.injector.on_plan(plan)
            art = ctx.prov.add_artifact("plan", plan, producer=step, **_fault_kwargs(ctx, fired))
            ok, feedback = self._check(ctx, "plan", findings, plan)
            ctx.prov.update(step, usage=ctx.llm.usage.since(before), verified=ok if ctx.verify else None)
            if ok:
                break
            ctx.prov.update(step, rejected=True)
        return art, plan

    def execute(
        self,
        ctx: RunContext,
        index: int,
        item: Any,
        task_art: str,
        plan_id: str,
        plan: dict[str, Any],
        findings: dict[str, Any],
    ) -> None:
        key = f"exec:{index}"
        fingerprint = agents.dumps(item)
        if self._reuse(ctx, key, "executor", [task_art, plan_id], fingerprint) is not None:
            return
        before = ctx.llm.usage.copy()
        planned_action = item.get("action") if isinstance(item, dict) else None
        step = ctx.prov.add_step(key, "executor", fingerprint=fingerprint, action=planned_action)
        ctx.prov.add_input(task_art, step)
        ctx.prov.add_input(plan_id, step)
        for claim_key in CLAIM_SUPPORT.get(planned_action, ()):
            claim_id = _latest_artifact(ctx.prov, claim_key)
            if claim_id is not None:
                ctx.prov.add_support(claim_id, step)
        messages = agents.executor_messages(ctx.task, plan, index, item, ctx.fault_note)
        result = None
        blocked = 0
        for it in range(MAX_EXEC_ITERS):
            out = ctx.llm.chat("executor", messages)
            action = out.get("action")
            if action == "done":
                result = out.get("result", "")
                break
            args = out.get("args") if isinstance(out.get("args"), dict) else {}
            fired = False
            if ctx.injector is not None and isinstance(action, str):
                action, args, fired = ctx.injector.on_action(ctx.task, action, args, ctx.world)
            if fired:
                ctx.prov.add_artifact(
                    f"{key}/call{it}", {"action": action, "args": args}, producer=step, **_fault_kwargs(ctx, True)
                )
            ok, reason = True, ""
            if action in EXEC_TOOLS or (action == "cancel_booking" and ctx.task.get("authorization_policy")):
                call = {"action": action, "args": args}
                ok, reason = self._check(ctx, "action", {"findings": findings, "current_step": item}, call)
            policy_event = None
            if ctx.task.get("authorization_policy"):
                import copy
                from .authorization import policy_violations
                policy_event = {"step": key, "action": action, "args": copy.deepcopy(args),
                    "violations": policy_violations(ctx.task, action, args),
                    "blocked_by_verifier": not ok, "committed": False}
                ctx.world.action_history.append(policy_event)
            if ok:
                obs = self._exec_tool(ctx, step, action, args)
            else:
                blocked += 1
                obs = {"error": f"blocked by verifier: {reason}"}
            if policy_event is not None:
                policy_event["committed"] = bool(ok and isinstance(obs, dict) and "error" not in obs
                    and action in {*EXEC_TOOLS, "cancel_booking"})
                ctx.prov.add_artifact(f"{key}/authorization{it}", policy_event.copy(), producer=step)
            obs_id = ctx.prov.add_artifact(f"{key}/obs{it}", obs, producer=step)
            ctx.prov.add_input(obs_id, step)
            if not (isinstance(obs, dict) and "error" in obs):
                result = obs
                break
            messages += _turn(out, obs)
        ctx.prov.add_artifact(key, {"result": result}, producer=step)
        ctx.prov.update(step, usage=ctx.llm.usage.since(before), blocked_actions=blocked)

    # Tools.

    def _research_tool(self, ctx: RunContext, key: str, out: dict[str, Any]) -> Any:
        action, args = out.get("action"), out.get("args") or {}
        if action != RESEARCH_TOOLS[key]:
            return {"error": f"unknown action {action!r}; respond with a tool call or a final answer"}
        try:
            if action == "search_flights":
                return ctx.world.search_flights(str(args["origin"]), str(args["dest"]), str(args["date"]))
            return ctx.world.search_hotels(str(args["city"]), float(args["min_rating"]), float(args["max_price"]))
        except (KeyError, TypeError, ValueError) as e:
            return {"error": f"bad arguments: {e}"}

    def _exec_tool(self, ctx: RunContext, step: str, action: Any, args: dict[str, Any]) -> Any:
        if action not in EXEC_TOOLS and not (action == "cancel_booking" and ctx.task.get("authorization_policy")):
            return {"error": f"unknown action {action!r}; call a tool or respond with done"}
        try:
            if action == "book_flight":
                obs, effect = ctx.world.book_flight(str(args["flight_id"]))
            elif action == "book_hotel":
                obs, effect = ctx.world.book_hotel(str(args["hotel_id"]), int(args.get("nights", 1)))
            elif action == "cancel_booking":
                obs, effect = ctx.world.cancel_booking(str(args["booking_id"]))
            elif action == "write_file":
                obs, effect = ctx.world.write_file(str(args["path"]), str(args["content"]))
            else:
                obs, effect = ctx.world.send_email(str(args["to"]), str(args.get("subject", "")), str(args["body"]))
        except (KeyError, TypeError, ValueError) as e:
            return {"error": f"bad arguments: {e}"}
        if effect is not None:
            ctx.prov.add_effect(step, effect)
        return obs

    # Verification and reuse.

    def _check(self, ctx: RunContext, kind: str, evidence: Any, item: Any) -> tuple[bool, str]:
        """Verifier call used by the always-verify baseline; a no-op otherwise."""
        if not ctx.verify:
            return True, ""
        out = ctx.llm.chat("verifier", agents.verifier_messages(ctx.task, kind, evidence, item))
        return out.get("ok") is not False, str(out.get("reason", ""))

    def _reuse(
        self, ctx: RunContext, key: str, agent: str, inputs: list[str], fingerprint: str | None = None
    ) -> tuple[str, Any] | None:
        entry = ctx.reuse.get(key)
        if entry is None or entry.get("fingerprint") != fingerprint:
            return None
        step = ctx.prov.add_step(key, agent, reused=True, fingerprint=fingerprint)
        for a in inputs:
            ctx.prov.add_input(a, step)
        main: tuple[str, Any] | None = None
        for akey, value in entry["artifacts"]:
            aid = ctx.prov.add_artifact(akey, value, producer=step, reused=True)
            if akey == key:
                main = (aid, value)
        for effect in entry["effects"]:
            ctx.prov.add_effect(step, effect)
        ctx.consumed.add(key)
        return main if main is not None else (step, None)
