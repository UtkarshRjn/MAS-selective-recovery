"""Scenario specifications for the mechanism suite.

A case is a small workflow: steps that read and write claims, source records
the steps and checkers can query, typed effect contracts, fault injections at
source output boundaries, and named timing schedules.

Information is matched across policies. Every policy gets the same source
tools, the same source-linked checker, the same corrector (which re-queries
sources), the same contracts, and the same detection delay. The delay for a
fault comes from the clean reference timeline of the case, so it does not
depend on which policy runs; the orderings it was chosen to realize are then
recorded per run, since a policy can change them (verify-before never has a
consumer in flight).
"""

from __future__ import annotations

import asyncio
import copy
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..action_contracts import ActionContract, ContractRegistry, ToolGateway
from ..contract import ArtifactRef, Evidence, Plan, TaskNode, canonical, content_digest
from ..effects import ConditionalStore
from ..fault_schedule import FaultSpec
from ..verifiers import ClaimCheck, GlobalInvariant

Values = dict[str, Any]


@dataclass
class StepContext:
    world: "World"
    attempt_number: int
    evidence: dict[str, Evidence]
    identity: tuple[ArtifactRef, ...] = ()
    llm: Any = None


@dataclass(frozen=True)
class ActionSpec:
    tool: str
    arguments: Callable[[Values, StepContext], dict[str, Any] | None]
    preconditions: Callable[[Values, StepContext], dict[str, int]] | None = None


@dataclass(frozen=True)
class StepSpec:
    task_id: str
    agent: str
    writes: tuple[str, ...]
    compute: Callable[[Values, StepContext], Values | Awaitable[Values]]
    reads: tuple[str, ...] = ()
    late_reads: tuple[str, ...] = ()
    duration: float = 5.0
    tokens: tuple[int, int] = (600, 150)
    actions: tuple[ActionSpec, ...] = ()
    bind: dict[str, str] = field(default_factory=dict)
    applies: dict[str, str] = field(default_factory=dict)
    goal: bool | None = None
    checker_step: bool = False

    @property
    def all_reads(self) -> tuple[str, ...]:
        return (*self.reads, *self.late_reads)


@dataclass(frozen=True)
class Trigger:
    """When a fault's failed check is released.

    ``delay``        a fixed delay after the check starts (`delay` seconds).
    ``timeline``     a delay taken from the clean reference timeline:
                     ``inflight`` (a dependent result exists, a consumer is in a
                     model request, and an independent branch is working),
                     ``pre_commit`` (just after the first effect proposal that
                     depends on the fault), ``after_draft`` (just after the
                     first dependent output), ``late`` (after the clean run).
    ``after_event``  shortly after a monitor event, for faults defined relative
                     to another fault's recovery.
    ``at``           at an absolute loop time (`delay` seconds after the run starts).
    """

    kind: str
    delay: float = 0.0
    point: str = ""
    event: str = ""
    claim: str = ""
    max_wait: float = 60.0


@dataclass(frozen=True)
class Schedule:
    name: str
    faults: tuple[str, ...]
    triggers: dict[str, Trigger] = field(default_factory=dict)
    durations: dict[str, float] = field(default_factory=dict)
    attempt_durations: dict[tuple[str, int], float] = field(default_factory=dict)
    uncancellable: frozenset[str] = frozenset()
    trust_model: str | None = None
    lose_ack: dict[str, int] = field(default_factory=dict)
    hook: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseSpec:
    family: str
    template: str
    description: str
    steps: tuple[StepSpec, ...]
    answer_claims: tuple[str, ...]
    sources: dict[str, Any]
    resources: dict[str, Any]
    faults: tuple[FaultSpec, ...]
    schedules: tuple[Schedule, ...]
    checks: Callable[["World"], dict[str, ClaimCheck]]
    contracts: Callable[[], list[ActionContract]]
    tools: Callable[["World"], dict[str, Callable[..., Any]]]
    corrector: Callable[[str, "World", Values], Any] | None = None
    identity: tuple[ArtifactRef, ...] = ()
    invariants: tuple[GlobalInvariant, ...] = ()
    trust_model: str = "local_commit"
    concurrency: int = 8
    check_latency: float = 0.5
    correction_latency: float = 3.0
    tool_latency: float = 0.2
    budgets: tuple[float, ...] = ()
    token_budgets: tuple[int, ...] = ()
    effect_oracle: Callable[[list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]], "World"], dict[str, Any]] | None = None
    reference_sources: Callable[[Schedule], dict[str, Any]] | None = None
    hooks: dict[str, Callable[..., Awaitable[None]]] = field(default_factory=dict)
    advisory_compliance: float = 1.0
    invariant_factory: Callable[[Schedule], tuple[GlobalInvariant, ...]] | None = None
    suite: str = "mechanism"
    eager_claims: tuple[str, ...] = ()
    # False when the hazard is the schedule itself (concurrent writers), so no fault-free twin exists.
    has_clean_condition: bool = True

    def invariants_for(self, schedule: Schedule) -> tuple[GlobalInvariant, ...]:
        if self.invariant_factory is not None:
            return self.invariant_factory(schedule)
        return self.invariants

    @property
    def key(self) -> str:
        return f"{self.family}:{self.template}"

    def schedule(self, name: str) -> Schedule:
        for item in self.schedules:
            if item.name == name:
                return item
        raise KeyError(f"{self.key} has no schedule {name}")

    def step(self, task_id: str) -> StepSpec:
        for item in self.steps:
            if item.task_id == task_id:
                return item
        raise KeyError(task_id)

    def fault(self, fault_id: str) -> FaultSpec:
        for item in self.faults:
            if item.fault_id == fault_id:
                return item
        raise KeyError(fault_id)

    def plan(self) -> Plan:
        writers: dict[str, list[str]] = {}
        for step in self.steps:
            for claim_id in step.writes:
                writers.setdefault(claim_id, []).append(step.task_id)
        edges: list[tuple[str, str]] = []
        for step in self.steps:
            for claim_id in step.reads:
                for src in writers.get(claim_id, ()):
                    if src != step.task_id and (src, step.task_id) not in edges:
                        edges.append((src, step.task_id))
        # Only steps that answer the task or carry an effect are goals; an intermediate
        # step is relaunched by recovery only when a reader still needs what it writes.
        goal_steps = {
            step.task_id for step in self.steps
            if step.goal or (step.goal is None and (
                step.actions or set(step.writes) & set(self.answer_claims) or not step.writes
            ))
        }
        nodes = tuple(
            TaskNode(
                step.task_id, step.agent,
                (f"done:{step.task_id}",) if step.task_id in goal_steps else (),
                step.all_reads, step.writes,
            )
            for step in self.steps
        )
        return Plan(1, nodes, tuple(edges), tuple(f"done:{step.task_id}" for step in self.steps if step.task_id in goal_steps))

    def manifest(self) -> dict[str, Any]:
        """A JSON description used to freeze the configuration."""
        return {
            "family": self.family,
            "template": self.template,
            "description": self.description,
            "suite": self.suite,
            "has_clean_condition": self.has_clean_condition,
            "eager_claims": list(self.eager_claims),
            "steps": [
                {
                    "task_id": step.task_id,
                    "agent": step.agent,
                    "reads": list(step.reads),
                    "late_reads": list(step.late_reads),
                    "writes": list(step.writes),
                    "duration": step.duration,
                    "tokens": list(step.tokens),
                    "actions": [action.tool for action in step.actions],
                }
                for step in self.steps
            ],
            "answer_claims": list(self.answer_claims),
            "faults": [
                {"fault_id": item.fault_id, "task": item.task, "claim": item.claim_id,
                 "persistence": item.persistence, "description": item.description}
                for item in self.faults
            ],
            "schedules": [
                {
                    "name": item.name,
                    "faults": list(item.faults),
                    "triggers": {key: trig.__dict__ for key, trig in item.triggers.items()},
                    "durations": item.durations,
                    "attempt_durations": {f"{k[0]}#{k[1]}": v for k, v in item.attempt_durations.items()},
                    "uncancellable": sorted(item.uncancellable),
                    "trust_model": item.trust_model,
                    "lose_ack": item.lose_ack,
                    "hook": item.hook,
                    "params": item.params,
                }
                for item in self.schedules
            ],
            "sources_digest": content_digest(canonical(self.sources)),
            "trust_model": self.trust_model,
            "concurrency": self.concurrency,
            "check_latency": self.check_latency,
            "correction_latency": self.correction_latency,
            "budgets": list(self.budgets),
            "token_budgets": list(self.token_budgets),
            "invariants": [item.name for item in self.invariants],
        }


def jitter(seed: int, key: str, base: float, spread: float = 0.15) -> float:
    """A reproducible duration: the same seed and key always give the same value."""
    rng = random.Random(f"{seed}:{key}")
    return round(base * (1.0 + rng.uniform(-spread, spread)), 4)


class World:
    """Source records and effect targets behind one conditional store.

    `effects` is the physical history the target applied, in order, including
    effects whose acknowledgment was lost and effects later regretted.
    """

    def __init__(self, case: CaseSpec, schedule: Schedule, sources: dict[str, Any] | None = None) -> None:
        self.case = case
        self.schedule = schedule
        base = copy.deepcopy(case.sources if sources is None else sources)
        resources = {f"src:{key}": value for key, value in base.items()}
        resources.update(copy.deepcopy(case.resources))
        self.store = ConditionalStore(resources)
        self.store.lose_ack = dict(schedule.lose_ack)
        self.effects: list[tuple[str, dict[str, Any]]] = []
        self.source_reads: list[tuple[float, str]] = []
        self.registry = ContractRegistry(case.contracts())
        self.tools = case.tools(self)
        read_only = {name for name, fn in self.tools.items() if getattr(fn, "read_only", False)}
        self.gateway = ToolGateway(self.tools, self.registry, read_only=read_only)

    def source(self, name: str) -> Any:
        value, _version = self.store.read(f"src:{name}")
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = 0.0
        self.source_reads.append((now, name))
        return value

    def source_version(self, name: str) -> int:
        return self.store.version(f"src:{name}")


def read_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    fn.read_only = True  # type: ignore[attr-defined]
    return fn


def effect_tool(
    world: World,
    tool: str,
    resource: Callable[[dict[str, Any]], str],
    update: Callable[[Any, dict[str, Any]], Any],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """A mutating tool: one conditional write on the target, recorded in the physical history."""

    async def call(arguments: dict[str, Any], *, preconditions: dict[str, int], idempotency_key: str) -> dict[str, Any]:
        await asyncio.sleep(world.case.tool_latency)
        target = resource(arguments)
        expected = preconditions.get(target)
        recorded: list[bool] = []

        def apply(before: Any) -> Any:
            recorded.append(True)
            return update(before, arguments)

        try:
            result = world.store.write(
                target, apply, operation=tool, expected_version=expected, idempotency_key=idempotency_key,
            )
        except Exception:
            if recorded:
                world.effects.append((tool, dict(arguments)))
            raise
        if not result.get("duplicate_suppressed"):
            world.effects.append((tool, dict(arguments)))
        return result

    return call


# --------------------------------------------------------------- reference


@dataclass
class Reference:
    values: Values
    evidence: dict[str, Evidence]
    effects: list[tuple[str, dict[str, Any]]]
    start: dict[str, float]
    end: dict[str, float]
    published: dict[str, float]
    proposals: list[tuple[float, str, tuple[str, ...]]]


def _support(case: CaseSpec, claim_id: str) -> set[str]:
    """Claims a claim transitively depends on in the clean graph."""
    out: set[str] = set()
    stack = [claim_id]
    while stack:
        current = stack.pop()
        for step in case.steps:
            if current in step.writes:
                for read in step.all_reads:
                    if read not in out:
                        out.add(read)
                        stack.append(read)
    return out


def reference(case: CaseSpec, schedule: Schedule, seed: int, sources: dict[str, Any] | None = None) -> Reference:
    """Evaluate the clean workflow: values, effects, and an unconstrained timeline.

    The timeline assumes enough workers for every ready step and instant
    verification; it fixes detection delays, it is not a performance model.
    """
    world = World(case, schedule, sources)
    values: Values = {}
    evidence: dict[str, Evidence] = {}
    start: dict[str, float] = {}
    end: dict[str, float] = {}
    published: dict[str, float] = {}
    effects: list[tuple[str, dict[str, Any]]] = []
    proposals: list[tuple[float, str, tuple[str, ...]]] = []
    remaining = list(case.steps)
    while remaining:
        progressed = False
        for step in list(remaining):
            if not all(claim in published for claim in step.all_reads):
                continue
            ready = max((published[claim] for claim in step.reads), default=0.0)
            late = max((published[claim] for claim in step.late_reads), default=0.0)
            duration = schedule.durations.get(step.task_id, step.duration)
            duration = schedule.attempt_durations.get((step.task_id, 1), duration)
            began = ready
            finished = max(began, late) + jitter(seed, f"{step.task_id}#1", duration)
            start[step.task_id] = began
            end[step.task_id] = finished
            snapshot = {claim: copy.deepcopy(values[claim]) for claim in step.all_reads}
            ctx = StepContext(world, 1, {claim: evidence[claim] for claim in step.all_reads}, case.identity)
            produced = step.compute(snapshot, ctx)
            if asyncio.iscoroutine(produced):
                produced.close()
                raise TypeError("reference evaluation needs synchronous compute functions")
            for claim_id in step.writes:
                values[claim_id] = produced[claim_id]
                applies_to: tuple[ArtifactRef, ...] = ()
                if claim_id in step.applies:
                    applies_to = (evidence[step.applies[claim_id]].artifact, *case.identity)
                evidence[claim_id] = Evidence(claim_id, 1, copy.deepcopy(produced[claim_id]), step.task_id, (), applies_to=applies_to)
                published[claim_id] = finished
            for action in step.actions:
                arguments = action.arguments(snapshot | produced, ctx)
                if arguments is not None:
                    effects.append((action.tool, arguments))
                    proposals.append((finished, step.task_id, step.all_reads))
            remaining.remove(step)
            progressed = True
        if not progressed:
            raise ValueError(f"{case.key}: steps cannot all be scheduled")
    return Reference(values, evidence, effects, start, end, published, proposals)


def timeline_delay(case: CaseSpec, ref: Reference, fault: FaultSpec, point: str) -> float:
    """Delay after the faulty output's publication at which `point` first holds in the clean timeline."""
    out = ref.published[fault.claim_id]
    dependents = [step for step in case.steps if fault.claim_id in _support_of_step(case, step)]
    dependent_ids = {step.task_id for step in dependents}
    independents = [step for step in case.steps if step.task_id not in dependent_ids and fault.claim_id not in step.writes]
    eps = 0.1
    if point == "after_draft":
        times = [ref.end[step.task_id] for step in dependents if step.writes]
        return (min(times) - out + eps) if times else case.check_latency
    if point == "pre_commit":
        times = [t for t, task_id, _reads in ref.proposals if task_id in dependent_ids]
        return (min(times) - out + eps) if times else max(ref.end.values()) - out + eps
    if point == "late":
        return max(ref.end.values()) - out + 1.0
    if point == "inflight":
        candidates = sorted({out, *ref.start.values(), *ref.end.values()})
        for t in candidates:
            moment = t + eps
            dependent_done = any(ref.end[s.task_id] <= moment and s.writes for s in dependents)
            consumer_busy = any(ref.start[s.task_id] <= moment < ref.end[s.task_id] for s in dependents)
            independent_busy = any(ref.start[s.task_id] <= moment < ref.end[s.task_id] for s in independents)
            if moment > out and dependent_done and consumer_busy and independent_busy:
                return moment - out
        busy = [ref.start[s.task_id] + eps for s in dependents if ref.start[s.task_id] >= out]
        return (min(busy) - out) if busy else case.check_latency
    raise ValueError(f"unknown timeline point {point}")


def _support_of_step(case: CaseSpec, step: StepSpec) -> set[str]:
    out = set(step.all_reads)
    for claim_id in step.all_reads:
        out |= _support(case, claim_id)
    return out


def fault_cone(case: CaseSpec, claim_ids: set[str]) -> set[str]:
    """Steps whose outputs depend on any of the claims, by the experimental dependency contract."""
    return {step.task_id for step in case.steps if _support_of_step(case, step) & claim_ids}
