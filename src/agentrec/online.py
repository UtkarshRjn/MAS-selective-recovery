"""Fixed-graph online monitor cases and the baselines they are measured against.

`restaurant_date` spoils the date and leaves flight research and address notes
independent. `flight_id` spoils the flight id and leaves the restaurant side
independent. Corrections are supplied. The revised graph is derived from the
suspect cone, not from a hand-written task list.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .contract import Plan, TaskNode
from .monitor_runtime import MonitorRuntime
from .scheduler import Holds, OverlapClock, Scheduler, run_mock

POLICIES = ("online", "verify_before", "restart", "support")

TRUTH: dict[str, Any] = {
    "date": "2024-01-03",
    "bad_date": "2024-01-02",
    "flight_id": "FL1",
    "claim:flight_id": {"flight_id": "FL1"},
    "claim:restaurant_address": {"address": "1 Market St"},
    "claim:restaurant_notes": {"address": "1 Market St"},
}

CORRECTION = {"claim:restaurant_date": {"date": "2024-01-03"}}
FLIGHT_CORRECTION = {"claim:flight_id": {"flight_id": "FL1"}}
FAULTS = ("restaurant_date", "flight_id")


def initial_plan() -> Plan:
    nodes = (
        TaskNode("research_flights", "flight", (), (), ("claim:flight_id",)),
        TaskNode("research_restaurant", "restaurant", (), (), ("claim:restaurant_address", "claim:restaurant_date")),
        TaskNode("transfer", "transfer", (), ("claim:restaurant_date",), ("claim:transfer",)),
        TaskNode("restaurant_notes", "notes", (), ("claim:restaurant_address",), ("claim:restaurant_notes",)),
        TaskNode("planner", "planner", ("trip_planned",), ("claim:restaurant_address", "claim:restaurant_date"), ("claim:plan",)),
        TaskNode("book_flight", "executor", ("flight_booked",), ("claim:flight_id", "claim:plan"), ()),
    )
    edges = (
        ("research_restaurant", "transfer"),
        ("research_restaurant", "restaurant_notes"),
        ("research_restaurant", "planner"),
        ("planner", "book_flight"),
    )
    return Plan(1, nodes, edges, ("flight_booked", "trip_planned"))


def _produce_flight(monitor: MonitorRuntime, _snapshot: dict[str, Any], _attempt: object) -> dict[str, Any]:
    bad = monitor.faults_enabled and monitor.fault_name == "flight_id"
    return {"claim:flight_id": {"flight_id": "FL9" if bad else "FL1"}}


def _produce_restaurant(monitor: MonitorRuntime, _snapshot: dict[str, Any], _attempt: object) -> dict[str, Any]:
    bad = monitor.faults_enabled and monitor.fault_name == "restaurant_date"
    date = "2024-01-02" if bad else "2024-01-03"
    return {
        "claim:restaurant_address": {"address": "1 Market St"},
        "claim:restaurant_date": {"date": date},
    }


def _produce_transfer(_monitor: MonitorRuntime, snapshot: dict[str, Any], _attempt: object) -> dict[str, Any]:
    return {"claim:transfer": {"date": snapshot["claim:restaurant_date"]["date"]}}


def _produce_notes(_monitor: MonitorRuntime, snapshot: dict[str, Any], _attempt: object) -> dict[str, Any]:
    return {"claim:restaurant_notes": {"address": snapshot["claim:restaurant_address"]["address"]}}


def _produce_planner(_monitor: MonitorRuntime, snapshot: dict[str, Any], _attempt: object) -> dict[str, Any]:
    flight = snapshot.get("claim:flight_id", {}).get("flight_id", "FL1")
    date = snapshot["claim:restaurant_date"]["date"] if "claim:restaurant_date" in snapshot else None
    if date and date != TRUTH["date"]:
        text = f"restaurant {date}"
    else:
        text = f"flight {flight}"
    return {"claim:plan": {"itinerary": text, "flight_id": flight}}


PRODUCERS = {
    "flight": _produce_flight,
    "restaurant": _produce_restaurant,
    "transfer": _produce_transfer,
    "notes": _produce_notes,
    "planner": _produce_planner,
    "executor": _produce_planner,
}


def _book_args(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"flight_id": snapshot["claim:flight_id"]["flight_id"]}


@dataclass(frozen=True)
class OnlineReport:
    policy: str
    correct: bool
    logical_ticks: int
    model_calls: int
    repeated_work: int
    verification_cost: int
    blocked_waits: int
    verification_waits: int
    stale_rejected: int
    overlapped: bool
    independent_preserved: tuple[str, ...]
    final_answer: dict[str, Any] | None
    unresolved: str | None
    attempts: tuple[dict[str, str], ...]
    active_tasks: tuple[str, ...]
    required_goals: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    transport_calls: int


def _status(monitor: MonitorRuntime, task_id: str) -> str | None:
    attempt_id = monitor.latest_attempt.get(task_id)
    if attempt_id is None:
        return None
    attempt = monitor.graph.attempts.get(attempt_id)
    return None if attempt is None else attempt.status.value


def _verified(monitor: MonitorRuntime, claim_id: str) -> bool:
    return any(
        item.claim_id == claim_id and item.verification_status.value == "verified"
        for item in monitor.graph.evidence.values()
    )


async def _drive_flight(monitor: MonitorRuntime, holds: Holds, policy: str) -> None:
    """Let the restaurant side finish, then release the bad flight id."""
    await asyncio.wait_for(holds.entered["research_flights"].wait(), 5)
    assert monitor.verification_gate is not None
    monitor.verification_gate.set()
    await monitor.wait_until(
        lambda item: _status(item, "restaurant_notes") == "completed" and _status(item, "planner") == "completed"
    )
    holds.release["research_flights"].set()
    await monitor.wait_until(lambda item: item.recovery_applied or item.unresolved is not None)
    if monitor.unresolved is not None:
        return
    if policy == "restart":
        await monitor.wait_until(lambda item: _verified(item, "claim:plan") and _verified(item, "claim:flight_id"))
        return
    await monitor.wait_until(lambda item: item.final_answer is not None)


async def _drive(monitor: MonitorRuntime, holds: Holds, policy: str) -> None:
    await asyncio.wait_for(holds.entered["research_flights"].wait(), 5)
    await asyncio.wait_for(monitor.verification_blocked.wait(), 5)
    if policy in {"online", "support", "restart"}:
        await asyncio.wait_for(holds.entered["transfer"].wait(), 5)
        await asyncio.wait_for(holds.entered["planner"].wait(), 5)
        await monitor.wait_until(lambda item: _status(item, "restaurant_notes") == "completed")
    assert monitor.verification_gate is not None
    monitor.verification_gate.set()
    await monitor.wait_until(lambda item: item.recovery_applied or item.unresolved is not None)
    flight_id = holds.attempt_ids["research_flights"]
    if policy != "restart" and monitor.is_cancelled(flight_id):
        raise RuntimeError("flight research was cancelled while it was still required")
    holds.release["research_flights"].set()
    if monitor.unresolved is None and policy == "online":
        await monitor.wait_until(lambda item: _status(item, "planner:2") == "completed")
    elif monitor.unresolved is None and policy == "support":
        await monitor.wait_until(lambda item: _verified(item, "claim:plan"))
    elif monitor.unresolved is None and policy == "restart":
        await monitor.wait_until(lambda item: _verified(item, "claim:plan") and _verified(item, "claim:flight_id"))
    for name in ("transfer", "planner"):
        if name in holds.release:
            holds.release[name].set()


def _report(policy: str, monitor: MonitorRuntime, clock: OverlapClock) -> OnlineReport:
    attempts = tuple(
        {
            "attempt_id": attempt.attempt_id,
            "task_id": attempt.task_id,
            "agent_id": attempt.agent_id,
            "status": attempt.status.value,
            "plan_version": str(attempt.plan_version),
        }
        for attempt in monitor.graph.attempts.values()
        if attempt.agent_id not in {"verifier", "recovery"}
    )
    counts = Counter(item["agent_id"] for item in attempts if item["agent_id"] != "corrector")
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    preserved = []
    for task_id in ("research_flights", "restaurant_notes"):
        rows = [item for item in attempts if item["task_id"] == task_id]
        if len(rows) == 1 and rows[0]["status"] == "completed":
            preserved.append(task_id)
    answer = monitor.final_answer
    correct = (
        monitor.unresolved is None
        and answer is not None
        and answer.get("flight_id") == "FL1"
        and answer.get("itinerary") == "flight FL1"
        and "2024-01-02" not in str(answer)
    )
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
    return OnlineReport(
        policy=policy,
        correct=correct,
        logical_ticks=clock.ticks,
        model_calls=clock.calls,
        repeated_work=repeated,
        verification_cost=monitor.verification_cost,
        blocked_waits=monitor.verification_waits + monitor.dependency_waits + monitor.action_blocks,
        verification_waits=monitor.verification_waits,
        stale_rejected=monitor.stale_rejected,
        overlapped=clock.overlapped,
        independent_preserved=tuple(preserved),
        final_answer=answer,
        unresolved=monitor.unresolved,
        attempts=attempts,
        active_tasks=tuple(node.task_id for node in monitor.plan.task_nodes),
        required_goals=monitor.plan.required_goals,
        evidence=evidence,
        transport_calls=len(monitor.transport_calls),
    )


async def run_case(
    policy: str = "online",
    *,
    concurrency: int = 4,
    correction_budget: int = 2,
    corrections: dict[str, Any] | None = None,
    fault: str = "restaurant_date",
) -> OnlineReport:
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy}")
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault}")
    supplied = CORRECTION if fault == "restaurant_date" else FLIGHT_CORRECTION
    monitor = MonitorRuntime(
        initial_plan(),
        worker_count=2,
        policy=policy,
        correction_budget=correction_budget,
        truth=TRUTH,
        corrections=supplied if corrections is None else corrections,
        allow_provisional=policy != "verify_before",
        required_action={"flight_id": "FL1"},
    )
    monitor.fault_name = fault
    held = {"research_flights"}
    if fault == "restaurant_date" and policy in {"online", "support", "restart"}:
        held.update({"transfer", "planner"})
    holds = Holds(held)
    clock = OverlapClock()

    async def runner(runtime: MonitorRuntime, attempt: object, node: TaskNode) -> None:
        await run_mock(
            runtime,
            attempt,  # type: ignore[arg-type]
            node,
            producers=PRODUCERS,
            actions={"book_flight": _book_args},
            clock=clock,
            holds=holds,
        )

    scheduler = Scheduler(monitor, runner, concurrency)
    await monitor.start()
    schedule = asyncio.create_task(scheduler.run())
    try:
        drive = _drive_flight if fault == "flight_id" else _drive
        await asyncio.wait_for(drive(monitor, holds, policy), 5)
        await asyncio.wait_for(schedule, 5)
    finally:
        if monitor.verification_gate is not None:
            monitor.verification_gate.set()
        for event in holds.release.values():
            event.set()
        if not schedule.done():
            await asyncio.wait_for(schedule, 5)
        await monitor.stop()
    return _report(policy, monitor, clock)


async def run_comparison(*, concurrency: int = 4, budget: int = 2) -> list[OnlineReport]:
    return [await run_case(policy, concurrency=concurrency, correction_budget=budget) for policy in POLICIES]


def format_comparison(reports: list[OnlineReport]) -> str:
    header = (
        f"{'policy':<16}{'correct':>9}{'calls':>7}{'repeated':>10}"
        f"{'verify':>8}{'blocked':>9}{'stale':>7}  preserved"
    )
    lines = [header]
    for report in reports:
        preserved = ",".join(report.independent_preserved) or "-"
        lines.append(
            f"{report.policy:<16}{str(report.correct):>9}{report.model_calls:>7}{report.repeated_work:>10}"
            f"{report.verification_cost:>8}{report.blocked_waits:>9}{report.stale_rejected:>7}  {preserved}"
        )
    return "\n".join(lines)


def attempt_rows(report: OnlineReport, task_id: str) -> tuple[dict[str, str], ...]:
    return tuple(row for row in report.attempts if row["task_id"] == task_id)


def evidence_rows(report: OnlineReport, claim_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in report.evidence if row["claim_id"] == claim_id)
