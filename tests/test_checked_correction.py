"""A repair is published only after the monitor accepts it."""

from __future__ import annotations

import asyncio

from agentrec.contract import EvidenceRef
from agentrec.fault_cases import run_fault_case
from agentrec.forward_recovery import decide_recovery
from agentrec.graph_monitor import run_graph_case
from agentrec.llm import MockLLM
from agentrec.online import evidence_rows, run_case
from agentrec.qwen_runs import evaluate_qwen


def test_rejected_repair_spends_the_budget_and_does_not_book():
    report = asyncio.run(run_case(
        "online",
        correction_budget=1,
        corrections={"claim:restaurant_date": {"date": "2024-01-02"}},
    ))
    assert report.unresolved == "correction rejected"
    assert report.transport_calls == 0
    assert report.final_answer is None
    assert not any(row["producer"].startswith("correct:") for row in report.evidence)


def test_rejected_repair_with_budget_left_reruns_the_producer():
    online = asyncio.run(run_case(
        "online",
        correction_budget=2,
        corrections={"claim:restaurant_date": {"date": "2024-01-02"}},
    ))
    support = asyncio.run(run_case("support"))
    assert online.correct and online.transport_calls == 1
    assert online.model_calls == support.model_calls
    assert online.repeated_work == support.repeated_work
    assert not any(
        row["producer"].startswith("correct:")
        for row in evidence_rows(online, "claim:restaurant_date")
        if row["status"] == "verified"
    )


def test_online_without_a_correction_matches_support():
    online = asyncio.run(run_case("online", corrections={}))
    support = asyncio.run(run_case("support", corrections={}))
    assert online.correct and support.correct
    assert online.model_calls == support.model_calls
    assert online.repeated_work == support.repeated_work
    assert online.independent_preserved == support.independent_preserved


def test_screened_decision_drops_a_bad_proposal():
    from agentrec.online import initial_plan

    plan = initial_plan()
    decision = decide_recovery(
        policy="online",
        budget_remaining=1,
        roots=(EvidenceRef("claim:restaurant_date", 1),),
        corrections={"claim:restaurant_date": {"date": "2024-01-02"}},
        base_version=1,
        goals=plan.required_goals,
        tasks=plan.task_nodes,
        edges=plan.dependency_edges,
        evidence_versions=(),
        truth={"date": "2024-01-03"},
    )
    assert decision.rejected and decision.unresolved
    assert decision.publish == () and decision.revision is None


def test_refundable_sibling_survives_and_a_bad_booking_argument_does_not_dispatch():
    online = asyncio.run(run_fault_case("false_refundable", "online"))
    coarse = asyncio.run(run_fault_case("false_refundable", "coarse"))
    assert online.success and coarse.success
    hotel = [row for row in online.evidence if row["claim_id"] == "claim:hotel_id" and row["version"] == 1]
    coarse_hotel = [row for row in coarse.evidence if row["claim_id"] == "claim:hotel_id" and row["version"] == 1]
    assert hotel[0]["status"] == "verified"
    assert coarse_hotel[0]["status"] == "invalid"
    assert "notes" in online.preserved

    hotel_id = asyncio.run(run_fault_case("wrong_hotel_id", "online"))
    coarse_id = asyncio.run(run_fault_case("wrong_hotel_id", "coarse"))
    refund = [row for row in hotel_id.evidence if row["claim_id"] == "claim:refundable" and row["version"] == 1]
    coarse_refund = [row for row in coarse_id.evidence if row["claim_id"] == "claim:refundable" and row["version"] == 1]
    assert hotel_id.success and refund[0]["status"] == "verified"
    assert coarse_refund[0]["status"] == "invalid"

    booked = asyncio.run(run_fault_case("bad_argument", "online"))
    assert booked.success and booked.transport == 1
    refused = asyncio.run(run_fault_case(
        "bad_argument",
        "online",
        corrections={"claim:book_args": {"flight_id": "FL9"}},
        correction_budget=1,
    ))
    assert refused.unresolved == "correction rejected"
    assert refused.transport == 0

    dropped = asyncio.run(run_fault_case("dropped_constraint", "online"))
    assert dropped.success
    plans = [row for row in dropped.evidence if row["claim_id"] == "claim:plan" and row["status"] == "verified"]
    assert any(step["action"] == "book_hotel" for step in plans[-1]["value"]["steps"])


def test_mock_model_repairs_are_checked_on_the_graph_and_the_faults():
    rows = asyncio.run(evaluate_qwen(lambda _seed: MockLLM(), seeds=(0,)))
    assert rows and all(row.success for row in rows)
    assert all(row.llm_calls >= 0 for row in rows)
    diamond = next(row for row in rows if row.case == "diamond" and row.policy == "online")
    suffix = next(row for row in rows if row.case == "diamond" and row.policy == "suffix")
    assert "c" in diamond.preserved
    assert "c" not in suffix.preserved
    graph = asyncio.run(run_graph_case("diamond", "b", "online", llm=MockLLM()))
    assert graph.success and "c" in graph.preserved
