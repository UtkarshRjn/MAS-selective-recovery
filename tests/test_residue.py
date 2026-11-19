import copy

import pytest

from agentrec.faults import EMAIL_FIRST_FAULTS, FAULTS_BY_NAME, FaultSpec
from agentrec.llm import MockLLM
from agentrec.residue import make_residue_task, run_case, side_effect_residue, summarize, wilson
from agentrec.runner import RECOVER, run_workflow

METHODS = ["restart", "suffix", "coarse", "support", "monitor", "support_monitor"]
WRONG_FLIGHT = FAULTS_BY_NAME["incorrect_result@research_flights"]
FILE_FAULT = EMAIL_FIRST_FAULTS[1]


def _effect_order(prov):
    steps = [n for n in prov.of_kind("step") if n.key.startswith("exec:")]
    return [n.data["action"] for n in steps if n.data.get("action") in {"send_email", "write_file"}]


def test_email_first_plan_sends_email_before_file():
    task, truth, catalog = make_residue_task("email_first", 0)
    world, prov, _, _ = run_workflow(task, catalog, MockLLM())
    assert _effect_order(prov) == ["send_email", "write_file"]
    assert side_effect_residue(world, truth)["residue"] is False


def test_default_booking_plan_is_unchanged():
    task, truth, catalog = make_residue_task("booking", 0)
    _, prov, _, _ = run_workflow(task, catalog, MockLLM())
    assert _effect_order(prov) == ["write_file", "send_email"]


def test_pre_recovery_residue_is_the_same_for_every_method():
    task, truth, catalog = make_residue_task("booking", 0)
    llm = MockLLM()
    world, prov, inj, _ = run_workflow(task, catalog, llm, WRONG_FLIGHT)
    assert inj.fired
    pre = side_effect_residue(world, truth)
    assert pre["wrong_emails"] == 1 and pre["wrong_bookings_ever"] == 1
    for method in METHODS:
        world_r, prov_r = copy.deepcopy(world), copy.deepcopy(prov)
        RECOVER[method](task, world_r, llm, prov_r)
        post = side_effect_residue(world_r, truth, before=world)
        assert post["final_correct"], method
        # the wrong email cannot be unsent and the wrong booking stays on record
        assert post["wrong_emails"] == 1 and post["wrong_bookings_ever"] == 1, method


@pytest.mark.parametrize("gate", ["G1", "G2"])
def test_gates_hold_the_wrong_flight_before_it_is_booked_or_emailed(gate):
    task, truth, catalog = make_residue_task("booking", 0)
    world, prov, inj, _ = run_workflow(task, catalog, MockLLM(), WRONG_FLIGHT, gate=gate)
    pre = side_effect_residue(world, truth)
    if gate == "G1":
        assert any(n.data.get("gate_hold") for n in prov.of_kind("step"))
        assert pre["wrong_emails"] == 0 and pre["wrong_bookings_ever"] == 0
    else:
        # the wrong flight is a real search result on the right date, so the consistency gate lets it through
        assert pre["wrong_bookings_ever"] == 1


def test_g1_then_coarse_recovers_without_residue():
    rows = run_case("booking", 0, WRONG_FLIGHT, MockLLM(), ["coarse"], ["G1"])
    g1 = next(r for r in rows if r["gate"] == "G1" and r["method"] == "coarse")
    assert g1["post"]["final_correct"] and not g1["post"]["residue"]


def test_g2_holds_an_email_to_another_recipient():
    spec = FaultSpec("malicious_instruction", "research_hotels", persistent=True)
    task, truth, catalog = make_residue_task("booking", 1)
    ungated, _, inj, _ = run_workflow(task, catalog, MockLLM(), spec)
    gated, _, _, _ = run_workflow(task, catalog, MockLLM(), spec, gate="G2")
    if side_effect_residue(ungated, truth)["unauthorized_emails"] == 0:
        pytest.skip("mock agent did not act on the injection for this seed")
    assert side_effect_residue(gated, truth)["unauthorized_emails"] == 0


def test_fault_after_the_email_leaves_no_wrong_email_but_restart_duplicates_it():
    rows = run_case("email_first", 0, FILE_FAULT, MockLLM(), ["restart", "coarse"], [])
    none = next(r for r in rows if r["method"] == "none")
    assert none["fired"] and none["pre"]["wrong_emails"] == 0 and none["pre"]["user_emails"] == 1
    restart = next(r for r in rows if r["method"] == "restart")
    coarse = next(r for r in rows if r["method"] == "coarse")
    assert restart["post"]["final_correct"] and restart["post"]["recovery_duplicate_emails"] == 1
    assert coarse["post"]["final_correct"] and coarse["post"]["recovery_duplicate_emails"] == 0


def test_fault_before_the_email_leaves_a_wrong_email_after_recovery():
    rows = run_case("email_first", 0, EMAIL_FIRST_FAULTS[0], MockLLM(), ["restart", "coarse"], [])
    for r in rows:
        if r["method"] != "none":
            assert r["post"]["final_correct"] and r["post"]["wrong_emails"] == 1


def test_false_confirmation_counts_an_email_with_no_booking():
    task, truth, catalog = make_residue_task("booking", 0)
    world, _, _, _ = run_workflow(task, catalog, MockLLM())
    world.bookings.clear()
    assert side_effect_residue(world, truth)["false_confirmations"] == 1


def test_wilson_and_summary():
    p, lo, hi = wilson(0, 16)
    assert p == 0 and lo == 0 and 0.19 < hi < 0.21
    rows = run_case("email_first", 0, FILE_FAULT, MockLLM(), ["coarse"], [])
    (line,) = summarize(rows)
    assert line["n"] == 1 and line["recovered"].startswith("1/1")
