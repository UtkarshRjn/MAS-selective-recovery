"""Travel faults on the online monitor.

`false_refundable` and `wrong_hotel_id` spoil one claim and leave a sibling.
`bad_argument` proposes a flight id the verified evidence does not support.
`dropped_constraint` removes the hotel step from the plan. A repair is published
only after the monitor accepts it.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .agents import dumps
from .contract import EvidenceStatus, Plan, TaskNode
from .graph_monitor import GRAPH_POLICIES
from .monitor_runtime import MonitorRuntime
from .scheduler import Holds, OverlapClock, Scheduler, run_mock

FULL_STEPS = ("book_flight", "book_hotel", "write_file", "send_email")
FAULTS = ("false_refundable", "wrong_hotel_id", "bad_argument", "dropped_constraint")


@dataclass(frozen=True)
class FaultReport:
    fault: str
    policy: str
    success: bool
    calls: int
    repeated: int
    blocked: int
    stale: int
    preserved: tuple[str, ...]
    unresolved: str | None
    transport: int
    rejected: int
    proposals: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]


def _plan_for(fault: str) -> Plan:
    if fault == "false_refundable":
        writes = ("claim:hotel_id", "claim:refundable")
    else:
        writes = ("claim:refundable", "claim:hotel_id")
    if fault in {"false_refundable", "wrong_hotel_id"}:
        nodes = (
            TaskNode("research_hotels", "hotels", (), (), writes),
            TaskNode("hotel_notes", "notes", (), ("claim:hotel_id",), ("claim:hotel_notes",)),
            TaskNode(
                "planner",
                "planner",
                ("trip_planned",),
                ("claim:hotel_id", "claim:refundable"),
                ("claim:itinerary",),
            ),
        )
        edges = (("research_hotels", "hotel_notes"), ("research_hotels", "planner"))
        return Plan(1, nodes, edges, ("trip_planned",))
    if fault == "bad_argument":
        nodes = (
            TaskNode("research_flights", "flight", (), (), ("claim:flight_id",)),
            TaskNode("planner", "planner", (), ("claim:flight_id",), ("claim:plan",)),
            TaskNode("quote", "quote", (), ("claim:flight_id",), ("claim:book_args",)),
            TaskNode(
                "book_flight",
                "executor",
                ("flight_booked",),
                ("claim:book_args", "claim:flight_id", "claim:plan"),
                (),
            ),
        )
        edges = (
            ("research_flights", "planner"),
            ("research_flights", "quote"),
            ("quote", "book_flight"),
            ("planner", "book_flight"),
        )
        return Plan(1, nodes, edges, ("flight_booked",))
    nodes = (
        TaskNode("research", "research", (), (), ("claim:flight_id", "claim:hotel_id")),
        TaskNode("planner", "planner", ("trip_planned",), ("claim:flight_id", "claim:hotel_id"), ("claim:plan",)),
    )
    edges = (("research", "planner"),)
    return Plan(1, nodes, edges, ("trip_planned",))


def _truth(fault: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "flight_id": "FL1",
        "refundable": False,
        "bad_date": "2024-01-02",
        "claim:flight_id": {"flight_id": "FL1"},
        "claim:hotel_id": {"hotel_id": "H1"},
        "claim:refundable": {"refundable": False},
        "claim:hotel_notes": {"hotel_id": "H1"},
        "claim:itinerary": {"hotel_id": "H1", "refundable": False},
        "claim:book_args": {"flight_id": "FL1"},
    }
    if fault == "dropped_constraint":
        base["plan_actions"] = list(FULL_STEPS)
    return base


def _correction(fault: str) -> dict[str, Any]:
    if fault == "false_refundable":
        return {"claim:refundable": {"refundable": False}}
    if fault == "wrong_hotel_id":
        return {"claim:hotel_id": {"hotel_id": "H1"}}
    if fault == "bad_argument":
        return {"claim:book_args": {"flight_id": "FL1"}}
    return {"claim:plan": {"steps": [{"action": name} for name in FULL_STEPS]}}


def _produce(monitor: MonitorRuntime, snapshot: dict, attempt) -> dict[str, Any]:
    fault = monitor.graph_fault
    bad = monitor.faults_enabled
    agent = attempt.agent_id
    if agent == "hotels":
        hotel = "H9" if bad and fault == "wrong_hotel_id" else "H1"
        refundable = True if bad and fault == "false_refundable" else False
        return {
            "claim:refundable": {"refundable": refundable},
            "claim:hotel_id": {"hotel_id": hotel},
        }
    if agent == "notes":
        return {"claim:hotel_notes": {"hotel_id": snapshot["claim:hotel_id"]["hotel_id"]}}
    if agent == "planner" and fault == "bad_argument":
        return {"claim:plan": {"flight_id": "FL1", "itinerary": "flight FL1"}}
    if agent == "planner" and fault in {"false_refundable", "wrong_hotel_id"}:
        return {
            "claim:itinerary": {
                "hotel_id": snapshot["claim:hotel_id"]["hotel_id"],
                "refundable": snapshot["claim:refundable"]["refundable"],
            }
        }
    if agent == "flight":
        return {"claim:flight_id": {"flight_id": "FL1"}}
    if agent == "quote":
        flight_id = "FL9" if bad and fault == "bad_argument" else snapshot["claim:flight_id"]["flight_id"]
        return {"claim:book_args": {"flight_id": flight_id}}
    if agent == "research":
        return {"claim:flight_id": {"flight_id": "FL1"}, "claim:hotel_id": {"hotel_id": "H1"}}
    steps = ["book_flight", "write_file", "send_email"] if bad and fault == "dropped_constraint" else list(FULL_STEPS)
    return {"claim:plan": {"steps": [{"action": name} for name in steps]}}


def _book_args(snapshot: dict) -> dict[str, Any]:
    return {"flight_id": snapshot["claim:book_args"]["flight_id"]}


_REPAIR_SYSTEM = (
    'Reply with one JSON object {"value": <claim>} and no other text. '
    "Match SHAPE_JSON. Fill value from the other JSON fields. Do not write a sentence."
)


def _propose(llm, fault: str, claim_id: str):
    """Ask for the claim value. The shape example is a different id, so copying it fails the check."""
    if fault == "false_refundable":
        lines = [
            f"KIND_JSON: {dumps('refundable')}",
            f"CLAIM_JSON: {dumps(claim_id)}",
            f"HOTEL_JSON: {dumps({'id': 'H1', 'refundable': False})}",
            'SHAPE_JSON: {"value": {"refundable": true}}',
            "Set value.refundable to the boolean in HOTEL_JSON.",
        ]
    elif fault == "wrong_hotel_id":
        hotels = [{"id": "H1", "nightly_price": 180}, {"id": "H9", "nightly_price": 240}]
        lines = [
            f"KIND_JSON: {dumps('hotel_id')}",
            f"HOTELS_JSON: {dumps(hotels)}",
            'SHAPE_JSON: {"value": {"hotel_id": "H0"}}',
            "Set value.hotel_id to the id of the cheapest hotel.",
        ]
    elif fault == "bad_argument":
        lines = [
            f"KIND_JSON: {dumps('book_args')}",
            f"FLIGHT_JSON: {dumps('FL1')}",
            'SHAPE_JSON: {"value": {"flight_id": "FL0"}}',
            "Set value.flight_id to the string in FLIGHT_JSON.",
        ]
    else:
        lines = [
            f"KIND_JSON: {dumps('plan')}",
            f"STEPS_JSON: {dumps(list(FULL_STEPS))}",
            'SHAPE_JSON: {"value": {"steps": [{"action": "book_flight"}]}}',
            'Set value.steps to every name in STEPS_JSON, in order, each as {"action": <name>}.',
        ]
    messages = [
        {"role": "system", "content": _REPAIR_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]
    return _as_claim(fault, llm.chat("corrector", messages)["value"])


def _as_claim(fault: str, value):
    """Keep a bare refundable boolean in the claim object the rest of the run reads."""
    if fault != "false_refundable" or isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"refundable": value.strip().lower() == "true"}
    return {"refundable": bool(value)}


def _ok(monitor: MonitorRuntime, fault: str) -> bool:
    if monitor.unresolved is not None:
        return False

    def verified(claim_id: str) -> list:
        return [
            item for item in monitor.graph.versions(claim_id)
            if item.verification_status == EvidenceStatus.VERIFIED
        ]

    if fault == "false_refundable":
        return any(item.value == {"refundable": False} for item in verified("claim:refundable")) and any(
            item.value == {"hotel_id": "H1"} for item in verified("claim:hotel_id")
        )
    if fault == "wrong_hotel_id":
        return any(item.value == {"hotel_id": "H1"} for item in verified("claim:hotel_id"))
    if fault == "bad_argument":
        booked = any(item.value == {"flight_id": "FL1"} for item in verified("claim:book_args"))
        return booked and len(monitor.transport_calls) == 1
    latest = max(verified("claim:plan"), key=lambda item: item.version, default=None)
    if latest is None:
        return False
    got = [step.get("action") for step in latest.value.get("steps", [])]
    return got == list(FULL_STEPS)


def _settled(monitor: MonitorRuntime, fault: str) -> bool:
    if monitor.unresolved is not None:
        return True
    if not all(monitor.task_output_ready(node.task_id) for node in monitor.plan.task_nodes):
        return False
    return _ok(monitor, fault)


async def run_fault_case(
    fault: str,
    policy: str = "online",
    *,
    llm=None,
    corrections: dict[str, Any] | None = None,
    correction_budget: int = 2,
) -> FaultReport:
    if fault not in FAULTS:
        raise KeyError(fault)
    if policy not in GRAPH_POLICIES:
        raise ValueError(f"unknown policy {policy}")
    plan = _plan_for(fault)
    supplied = _correction(fault) if llm is None and corrections is None else (corrections or {})
    monitor = MonitorRuntime(
        plan,
        worker_count=2,
        policy=policy,
        correction_budget=correction_budget,
        truth=_truth(fault),
        corrections=supplied,
        allow_provisional=True,
        required_action={"flight_id": "FL1"},
    )
    monitor.graph_fault = fault
    monitor.defer_input_verification = True
    if llm is not None:
        monitor.proposer = lambda claim_id, llm=llm, fault=fault: _propose(llm, fault, claim_id)
    producers = {node.agent_id: _produce for node in plan.task_nodes}
    actions = {"book_flight": _book_args} if fault == "bad_argument" else {}
    clock = OverlapClock()
    holds = Holds(set())

    async def runner(runtime, attempt, node):
        await run_mock(
            runtime,
            attempt,
            node,
            producers=producers,
            actions=actions,
            clock=clock,
            holds=holds,
        )

    scheduler = Scheduler(monitor, runner, concurrency=4)
    await monitor.start()
    schedule = asyncio.create_task(scheduler.run())
    limit = 180 if llm is not None else 5
    try:
        await monitor.wait_until(lambda item: _settled(item, fault), timeout=limit)
        async with monitor.lock:
            monitor.settled = True
        await monitor._notify(["progress"])
        await asyncio.wait_for(schedule, limit)
    finally:
        if not schedule.done():
            async with monitor.lock:
                monitor.settled = True
            await monitor._notify(["progress"])
            await asyncio.wait_for(schedule, limit)
        await monitor.stop()
    return _report(fault, policy, monitor, clock)


def _report(fault: str, policy: str, monitor: MonitorRuntime, clock: OverlapClock) -> FaultReport:
    attempts = [
        attempt for attempt in monitor.graph.attempts.values()
        if attempt.agent_id not in {"verifier", "recovery", "corrector"}
    ]
    counts = Counter(attempt.agent_id for attempt in attempts)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    preserved = []
    for agent_id in counts:
        rows = [attempt for attempt in attempts if attempt.agent_id == agent_id]
        original = [attempt for attempt in rows if attempt.plan_version == 1]
        if len(original) == 1 and original[0].status.value == "completed":
            preserved.append(agent_id)
    evidence = tuple(
        {
            "claim_id": item.claim_id,
            "version": item.version,
            "status": item.verification_status.value,
            "producer": item.producer_attempt,
            "value": item.value,
        }
        for item in sorted(monitor.graph.evidence.values(), key=lambda item: (item.claim_id, item.version))
    )
    return FaultReport(
        fault=fault,
        policy=policy,
        success=_ok(monitor, fault),
        calls=clock.calls,
        repeated=repeated,
        blocked=monitor.verification_waits + monitor.dependency_waits + monitor.action_blocks,
        stale=monitor.stale_rejected,
        preserved=tuple(sorted(preserved)),
        unresolved=monitor.unresolved,
        transport=len(monitor.transport_calls),
        rejected=monitor.proposals_rejected,
        proposals=dict(monitor.proposal_values),
        evidence=evidence,
    )
