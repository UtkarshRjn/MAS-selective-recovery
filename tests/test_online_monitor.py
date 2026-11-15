"""Acceptance tests for the speculative online monitor."""

from __future__ import annotations

import asyncio

import pytest

from agentrec.action_dispatcher import TransportError, resource_key
from agentrec.contract import (
    ApprovalStatus,
    AttemptStatus,
    ContractError,
    Evidence,
    EvidenceRef,
    EvidenceStatus,
    Plan,
    ProposedAction,
    TaskNode,
    transition_evidence,
)
from agentrec.faults import FAULTS_BY_NAME
from agentrec.forward_recovery import decide_recovery, select_suspects
from agentrec.llm import MockLLM
from agentrec.monitor_runtime import MonitorRuntime, Rejected
from agentrec.online import attempt_rows, evidence_rows, initial_plan, run_case, run_comparison
from agentrec.plan_manager import PlanError, apply_revision
from agentrec.protocol import (
    ActionProposed,
    AttemptCompleted,
    OutputProposed,
    PlanRevisionProposed,
    ProtocolError,
)
from agentrec.provenance import Provenance
from agentrec.runner import run_workflow
from agentrec.scheduler import Scheduler
from agentrec.tasks import make_task
from agentrec.workflow import executor_stage, travel_stages


def _ref(claim: str, version: int = 1) -> EvidenceRef:
    return EvidenceRef(claim, version)


def test_verified_evidence_cannot_be_invalidated():
    evidence = Evidence("claim:flight_id", 1, {"flight_id": "FL1"}, "flight:attempt:1", ())
    verified = transition_evidence(evidence, EvidenceStatus.VERIFIED)
    with pytest.raises(ContractError):
        transition_evidence(verified, EvidenceStatus.INVALID)


def test_protocol_requires_attempt_and_exact_arguments():
    with pytest.raises(ProtocolError):
        OutputProposed("", "claim:flight_id", {}, (), 1)
    with pytest.raises(ProtocolError):
        ActionProposed("attempt:1", "action:1", "book_flight", ["FL1"], (), "book_flight", 1)  # type: ignore[arg-type]


def test_support_walk_stops_at_the_attempt_and_the_chain_does_not():
    prov = Provenance()
    claim = prov.add_artifact("claim:date", {"date": "bad"})
    step = prov.add_step("transfer", "transfer")
    prov.add_support(claim, step)
    output = prov.add_artifact("claim:transfer", {"date": "bad"}, producer=step)
    later = prov.add_step("planner", "planner")
    prov.add_support(output, later)
    assert output not in prov.descendants(claim, {"support"})
    closure = prov.consumption_closure([claim])
    assert {claim, step, output, later} <= closure


def test_verified_node_does_not_spread_the_chain():
    prov = Provenance()
    claim = prov.add_artifact("claim:date", {"date": "ok"}, verification_status="verified")
    step = prov.add_step("transfer", "transfer")
    prov.add_support(claim, step)
    assert prov.consumption_closure([claim]) == set()


def test_refundable_chain_reaches_itinerary_work_and_not_the_flight_booking():
    task, _truth, catalog = make_task(0)
    _world, prov, _inj, _trace = run_workflow(
        task, catalog, MockLLM(), FAULTS_BY_NAME["false_refundable@research_hotels"]
    )
    claim = next(node.id for node in prov.of_kind("artifact") if node.key == "claim:refundable")
    keys = {prov.nodes[nid].key for nid in prov.consumption_closure([claim])}
    assert "plan" in keys and "exec:2" in keys and "exec:3" in keys
    assert "exec:0" not in keys and "exec:1" not in keys


def test_travel_research_stages_have_no_edge_between_them():
    stages = {stage.task_id: stage for stage in travel_stages()}
    assert stages["research_flights"].depends_on == ()
    assert stages["research_hotels"].depends_on == ()
    assert stages["plan"].depends_on == ("research_flights", "research_hotels")
    assert executor_stage(0, "book_flight").reads == ("plan", "claim:flight_id")


def test_online_case_preserves_independent_work_and_rejects_the_late_plan():
    report = asyncio.run(run_case("online"))
    assert report.correct and report.overlapped
    assert attempt_rows(report, "research_flights")[0]["status"] == "completed"
    assert attempt_rows(report, "restaurant_notes")[0]["status"] == "completed"
    assert attempt_rows(report, "transfer")[0]["status"] == "obsolete"
    planners = [row for row in report.attempts if row["agent_id"] == "planner"]
    assert [row["task_id"] for row in planners] == ["planner", "planner:2"]
    assert planners[0]["status"] == "obsolete" and planners[1]["status"] == "completed"
    assert report.stale_rejected >= 1
    plans = evidence_rows(report, "claim:plan")
    assert len(plans) == 1 and plans[0]["status"] == "verified"
    assert plans[0]["producer"].startswith("planner:2")
    assert "2024-01-02" not in plans[0]["value"]["itinerary"]
    date = evidence_rows(report, "claim:restaurant_date")
    assert date[0]["status"] == "invalid"
    assert any(row["status"] == "verified" for row in date)
    assert evidence_rows(report, "claim:restaurant_address")[0]["status"] == "verified"
    assert "research_restaurant" not in report.active_tasks
    assert "planner:2" in report.active_tasks
    assert report.required_goals == ("flight_booked", "trip_planned")
    assert attempt_rows(report, "research_restaurant")[0]["status"] == "obsolete"
    assert report.transport_calls == 1


def test_recovery_revision_follows_the_suspect_cone():
    plan = initial_plan()
    date = decide_recovery(
        policy="online",
        budget_remaining=1,
        roots=(_ref("claim:restaurant_date"),),
        corrections={"claim:restaurant_date": {"date": "2024-01-03"}},
        base_version=1,
        goals=plan.required_goals,
        tasks=plan.task_nodes,
        edges=plan.dependency_edges,
        evidence_versions=(),
    )
    assert date.revision is not None
    assert set(date.revision.remove_tasks) == {"research_restaurant", "transfer", "planner", "book_flight"}
    assert [node.task_id for node in date.revision.add_tasks] == ["planner:2", "book_flight:2"]
    assert date.revision.carry_forward == ("research_flights", "restaurant_notes")
    assert date.publish == (("claim:restaurant_date", {"date": "2024-01-03"}),)
    apply_revision(plan, date.revision)

    flight = decide_recovery(
        policy="online",
        budget_remaining=1,
        roots=(_ref("claim:flight_id"),),
        corrections={"claim:flight_id": {"flight_id": "FL1"}},
        base_version=1,
        goals=plan.required_goals,
        tasks=plan.task_nodes,
        edges=plan.dependency_edges,
        evidence_versions=(),
    )
    assert flight.revision is not None
    assert "research_flights" in flight.revision.remove_tasks
    assert "research_restaurant" in flight.revision.carry_forward
    assert "planner" in flight.revision.carry_forward
    assert all(node.task_id != "research_flights:2" for node in flight.revision.add_tasks)
    assert any(node.task_id == "book_flight:2" for node in flight.revision.add_tasks)
    apply_revision(plan, flight.revision)

    tasks = (
        TaskNode("source", "s", (), (), ("claim:good", "claim:bad")),
        TaskNode("reader", "r", ("done",), ("claim:bad",), ("claim:out",)),
        TaskNode("notes", "n", (), ("claim:good",), ("claim:note",)),
    )
    edges = (("source", "reader"), ("source", "notes"))
    other = decide_recovery(
        policy="online",
        budget_remaining=1,
        roots=(_ref("claim:bad"),),
        corrections={"claim:bad": {"ok": True}},
        base_version=1,
        goals=("done",),
        tasks=tasks,
        edges=edges,
        evidence_versions=(),
    )
    assert other.revision is not None
    assert set(other.revision.remove_tasks) == {"source", "reader"}
    assert [node.task_id for node in other.revision.add_tasks] == ["reader:2"]
    assert other.revision.carry_forward == ("notes",)
    apply_revision(Plan(1, tasks, edges, ("done",)), other.revision)


def test_coarse_discards_the_sibling_and_suffix_reruns_the_tail():
    plan = initial_plan()
    shared = dict(
        budget_remaining=1,
        roots=(_ref("claim:restaurant_date"),),
        corrections={"claim:restaurant_date": {"date": "2024-01-03"}},
        base_version=1,
        goals=plan.required_goals,
        tasks=plan.task_nodes,
        edges=plan.dependency_edges,
        evidence_versions=(),
    )
    coarse = decide_recovery(policy="coarse", **shared)
    suffix = decide_recovery(policy="suffix", **shared)
    assert coarse.revision is not None and suffix.revision is not None
    assert coarse.publish == () and suffix.publish == ()
    assert "restaurant_notes" in coarse.revision.remove_tasks
    assert coarse.revision.carry_forward == ("research_flights",)
    assert "restaurant_notes:2" not in {node.task_id for node in coarse.revision.add_tasks}
    assert set(suffix.revision.remove_tasks) == {
        "research_restaurant", "transfer", "restaurant_notes", "planner", "book_flight",
    }
    assert suffix.revision.carry_forward == ("research_flights",)
    assert "restaurant_notes:2" in {node.task_id for node in suffix.revision.add_tasks}
    apply_revision(plan, coarse.revision)
    apply_revision(plan, suffix.revision)

    online = asyncio.run(run_case("online"))
    coarse_run = asyncio.run(run_case("coarse"))
    suffix_run = asyncio.run(run_case("suffix"))
    assert online.correct and coarse_run.correct and suffix_run.correct
    assert evidence_rows(online, "claim:restaurant_address")[0]["status"] == "verified"
    assert evidence_rows(coarse_run, "claim:restaurant_address")[0]["status"] == "invalid"
    assert evidence_rows(suffix_run, "claim:restaurant_address")[0]["status"] == "verified"
    assert len(attempt_rows(online, "restaurant_notes")) == 1
    assert attempt_rows(online, "restaurant_notes")[0]["status"] == "completed"
    assert "restaurant_notes" not in coarse_run.independent_preserved
    notes = [row for row in suffix_run.attempts if row["agent_id"] == "notes"]
    assert [row["task_id"] for row in notes] == ["restaurant_notes", "restaurant_notes:2"]
    assert notes[0]["status"] == "obsolete" and notes[1]["status"] == "completed"
    assert "research_flights" in online.independent_preserved
    assert "research_flights" in coarse_run.independent_preserved
    assert "research_flights" in suffix_run.independent_preserved
    assert suffix_run.repeated_work > coarse_run.repeated_work > online.repeated_work


def test_flight_fault_keeps_the_restaurant_side():
    report = asyncio.run(run_case("online", fault="flight_id"))
    assert report.correct and report.transport_calls == 1
    for task_id in ("research_restaurant", "transfer", "restaurant_notes", "planner"):
        rows = attempt_rows(report, task_id)
        assert len(rows) == 1 and rows[0]["status"] == "completed"
    flight = attempt_rows(report, "research_flights")
    assert len(flight) == 1 and flight[0]["status"] == "obsolete"
    assert "research_flights" not in report.active_tasks
    assert "research_restaurant" in report.active_tasks
    versions = evidence_rows(report, "claim:flight_id")
    assert versions[0]["status"] == "invalid" and versions[0]["value"]["flight_id"] == "FL9"
    verified = [row for row in versions if row["status"] == "verified"]
    assert verified[-1]["value"]["flight_id"] == "FL1"
    assert verified[-1]["producer"].startswith("correct:")


def test_baselines_repeat_more_and_block_more_than_the_online_monitor():
    reports = {item.policy: item for item in asyncio.run(run_comparison())}
    online, verify, restart, support = (reports[name] for name in ("online", "verify_before", "restart", "support"))
    assert all(item.correct for item in reports.values())
    assert online.stale_rejected >= 1 and verify.stale_rejected == 0
    assert verify.verification_waits > online.verification_waits
    assert restart.repeated_work > online.repeated_work
    assert restart.model_calls > online.model_calls
    assert "research_flights" in online.independent_preserved
    assert "research_flights" not in restart.independent_preserved
    assert support.repeated_work > online.repeated_work
    assert "research_flights" in support.independent_preserved


def test_correction_budget_does_not_dispatch():
    report = asyncio.run(run_case("online", correction_budget=0))
    assert report.unresolved == "correction budget exhausted"
    assert report.final_answer is None and report.transport_calls == 0


def test_mismatched_action_never_reaches_the_tool():
    async def mismatch() -> str:
        monitor = MonitorRuntime(initial_plan(), truth={"flight_id": "FL1", "bad_date": "2024-01-02"})
        await monitor.start()
        attempt = await monitor.open_attempt(monitor.plan.node("book_flight"))
        flight = Evidence("claim:flight_id", 1, {"flight_id": "FL1"}, attempt.attempt_id, (), EvidenceStatus.VERIFIED)
        plan = Evidence(
            "claim:plan", 1, {"flight_id": "FL1", "itinerary": "flight FL1"},
            attempt.attempt_id, (), EvidenceStatus.VERIFIED,
        )
        async with monitor.lock:
            monitor.graph.add_evidence(flight)
            monitor.graph.add_evidence(plan)
            monitor.graph.register_consumption(attempt.attempt_id, flight.ref)
            monitor.graph.register_consumption(attempt.attempt_id, plan.ref)
        calls: list[str] = []

        async def transport(action: ProposedAction) -> dict:
            calls.append(action.action_id)
            return {"ok": True}

        monitor.transport = transport
        await monitor.submit(ActionProposed(
            attempt.attempt_id, "action:bad", "book_flight", {"flight_id": "FL9"},
            (flight.ref, plan.ref), attempt.task_id, attempt.plan_version,
        ))
        for _ in range(5):
            await asyncio.sleep(0)
        status = monitor.actions["action:bad"].approval_status.value
        await monitor.stop()
        assert calls == []
        return status

    assert asyncio.run(mismatch()) == "rejected"


def test_registration_and_invalidation_follow_the_lock_order():
    async def order(invalidate_first: bool) -> str:
        node = TaskNode("transfer", "transfer", (), ("claim:date",), ("claim:transfer",))
        monitor = MonitorRuntime(Plan(1, (node,), (), ("g",)))
        await monitor.start()
        attempt = await monitor.open_attempt(node)
        evidence = Evidence("claim:date", 1, {"date": "bad"}, "seed", ())
        async with monitor.lock:
            monitor.graph.add_evidence(evidence)
        arrived_r, release_r = monitor.gate.arm(f"register:{attempt.attempt_id}:claim:date")
        arrived_i, release_i = monitor.gate.arm("invalidate")
        request = asyncio.create_task(monitor.request_evidence(attempt.attempt_id, "claim:date"))
        invalidation = asyncio.create_task(monitor.invalidate_refs([evidence.ref]))
        await asyncio.wait_for(arrived_r.wait(), 2)
        await asyncio.wait_for(arrived_i.wait(), 2)
        if invalidate_first:
            release_i.set()
            await invalidation
            async with monitor.lock:
                monitor.graph.attempts[attempt.attempt_id].status = AttemptStatus.CANCELLED
                monitor.graph.obsolete_attempts.add(attempt.attempt_id)
            release_r.set()
            with pytest.raises(Rejected):
                await asyncio.wait_for(request, 2)
            consumed = attempt.attempt_id in monitor.graph.consumers[("claim:date", 1)]
            await monitor.stop()
            return "rejected" if not consumed else "consumed"
        release_r.set()
        delivered = await asyncio.wait_for(request, 2)
        release_i.set()
        await invalidation
        consumed = attempt.attempt_id in monitor.graph.consumers[("claim:date", 1)]
        await monitor.stop()
        return "delivered" if consumed and delivered.version == 1 else "missed"

    assert asyncio.run(order(True)) == "rejected"
    assert asyncio.run(order(False)) == "delivered"


def test_revision_and_approval_follow_the_lock_order():
    async def order(revise_first: bool) -> bool:
        node = TaskNode("book_flight", "executor", ("flight_booked",), ("claim:flight_id",), ())
        monitor = MonitorRuntime(
            Plan(1, (node,), (), ("flight_booked",)),
            truth={"flight_id": "FL1", "bad_date": "nope"},
            required_action={"flight_id": "FL1"},
        )
        await monitor.start()
        attempt = await monitor.open_attempt(node)
        flight = Evidence("claim:flight_id", 1, {"flight_id": "FL1"}, attempt.attempt_id, (), EvidenceStatus.VERIFIED)
        async with monitor.lock:
            monitor.graph.add_evidence(flight)
        action = ProposedAction(
            "action:1", "book_flight", {"flight_id": "FL1"}, (flight.ref,),
            attempt.attempt_id, task_id="book_flight",
        )
        monitor.actions[action.action_id] = action
        arrived_a, release_a = monitor.gate.arm("approve")
        arrived_r, release_r = monitor.gate.arm("revise")
        approval = asyncio.create_task(monitor.authorize_and_dispatch(action.action_id, action.canonical_arguments))
        replacement = TaskNode("other", "executor", ("flight_booked",), (), ())
        revision = PlanRevisionProposed(
            monitor.recovery_id, (), 1, (replacement,), ("book_flight",), (), (), ("flight_booked",), (),
        )
        revise = asyncio.create_task(monitor.submit(revision))
        await asyncio.wait_for(arrived_a.wait(), 2)
        await asyncio.wait_for(arrived_r.wait(), 2)
        if revise_first:
            release_r.set()
            await revise
            release_a.set()
            result = await approval
        else:
            release_a.set()
            result = await approval
            release_r.set()
            await revise
        await monitor.stop()
        return result["dispatched"]

    assert asyncio.run(order(True)) is False
    assert asyncio.run(order(False)) is True


def test_resource_locks_serialize_writes_to_the_same_path():
    async def scenario() -> list[str]:
        order: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def transport(action: ProposedAction) -> dict:
            order.append("start:" + action.action_id)
            if action.action_id == "action:1":
                entered.set()
                await release.wait()
            order.append("end:" + action.action_id)
            return {"ok": True}

        key = resource_key("write_file", {"path": "itinerary.txt", "content": "a"})
        assert key == resource_key("write_file", {"path": "itinerary.txt", "content": "b"})
        monitor = MonitorRuntime(Plan(1, (TaskNode("write", "executor", ("g",), (), ()),), (), ("g",)))
        lock = monitor.resource_locks.setdefault(key, asyncio.Lock())

        async def go(action_id: str, content: str) -> None:
            action = ProposedAction(action_id, "write_file", {"path": "itinerary.txt", "content": content}, (), "seed")
            async with lock:
                await transport(action)

        one = asyncio.create_task(go("action:1", "a"))
        await entered.wait()
        two = asyncio.create_task(go("action:2", "b"))
        await asyncio.sleep(0)
        assert order == ["start:action:1"]
        release.set()
        await one
        await two
        return order

    assert asyncio.run(scenario()) == ["start:action:1", "end:action:1", "start:action:2", "end:action:2"]


def test_transport_failure_stays_separate_from_approval():
    async def scenario() -> str:
        node = TaskNode("book_flight", "executor", ("flight_booked",), (), ())
        monitor = MonitorRuntime(Plan(1, (node,), (), ("flight_booked",)), required_action={"flight_id": "FL1"})
        await monitor.start()
        attempt = await monitor.open_attempt(node)
        flight = Evidence("claim:flight_id", 1, {"flight_id": "FL1"}, attempt.attempt_id, (), EvidenceStatus.VERIFIED)
        plan = Evidence(
            "claim:plan", 1, {"flight_id": "FL1", "itinerary": "flight FL1"},
            attempt.attempt_id, (), EvidenceStatus.VERIFIED,
        )
        async with monitor.lock:
            monitor.graph.add_evidence(flight)
            monitor.graph.add_evidence(plan)

        async def transport(_action: ProposedAction) -> dict:
            raise TransportError("connection reset")

        monitor.transport = transport
        action = ProposedAction(
            "action:1", "book_flight", {"flight_id": "FL1"}, (flight.ref, plan.ref),
            attempt.attempt_id, task_id="book_flight",
        )
        monitor.actions[action.action_id] = action
        result = await monitor.authorize_and_dispatch(action.action_id, action.canonical_arguments)
        await monitor.stop()
        assert result["transport"] == "failed"
        assert monitor.final_answer is None
        assert monitor.actions["action:1"].approval_status == ApprovalStatus.TRANSPORT_FAILED
        return monitor.actions["action:1"].transport_error

    assert "connection reset" in asyncio.run(scenario())


def test_plan_revision_rejects_a_cycle_and_a_dropped_goal():
    current = Plan(
        1,
        (TaskNode("a", "a", ("goal",), (), ()), TaskNode("b", "b", (), (), ())),
        (("a", "b"),),
        ("goal",),
    )
    with pytest.raises(PlanError):
        apply_revision(current, PlanRevisionProposed(
            "monitor:recovery", (), 1, (), (), (("b", "a"),), (), ("goal",), ("a",),
        ))
    with pytest.raises(PlanError):
        apply_revision(current, PlanRevisionProposed(
            "monitor:recovery", (), 1, (), ("a",), (), (), ("goal",), (),
        ))


def test_ambiguous_attribution_unions_unverified_inputs():
    consumed = (_ref("claim:address"), _ref("claim:date"))
    produced = (_ref("claim:plan"),)
    suspects = select_suspects(
        supplied=(),
        ambiguous=True,
        consumed=consumed,
        produced=produced,
        status_of=lambda ref: EvidenceStatus.VERIFIED if ref.claim_id == "claim:address" else EvidenceStatus.PROVISIONAL,
    )
    assert suspects == (_ref("claim:date"), _ref("claim:plan"))


def test_concurrency_limit_holds_the_third_task():
    async def limited() -> list[str]:
        entered: list[str] = []
        first = asyncio.Event()
        release = asyncio.Event()
        nodes = (
            TaskNode("a", "a", ("g",), (), ()),
            TaskNode("b", "b", ("g",), (), ()),
            TaskNode("c", "c", ("g",), (), ()),
        )

        async def runner(monitor: MonitorRuntime, attempt, node: TaskNode) -> None:
            entered.append(node.task_id)
            if node.task_id == "a":
                first.set()
                await release.wait()
            await monitor.submit(AttemptCompleted(attempt.attempt_id, (), attempt.plan_version))

        monitor = MonitorRuntime(Plan(1, nodes, (), ("g",)))
        scheduler = Scheduler(monitor, runner, concurrency=1)
        await monitor.start()
        task = asyncio.create_task(scheduler.run())
        await asyncio.wait_for(first.wait(), 2)
        await asyncio.sleep(0)
        assert entered == ["a"]
        release.set()
        await monitor.wait_until(lambda item: len(entered) == 3)
        async with monitor.lock:
            monitor.settled = True
        monitor.progress_event.set()
        await asyncio.wait_for(task, 2)
        await monitor.stop()
        return entered

    assert asyncio.run(limited())[0] == "a"
