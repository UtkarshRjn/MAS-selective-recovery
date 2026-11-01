import copy

import pytest

from agentrec.checker import check
from agentrec.faults import ATTACKER_EMAIL, FAULTS_BY_NAME, MISSING_EXEC_TOOL
from agentrec.llm import MockLLM
from agentrec.monitor import monitor_recover
from agentrec.recovery import coarse_lineage, full_restart
from agentrec.runner import run_workflow
from agentrec.tasks import make_task


@pytest.fixture
def setup():
    task, truth, catalog = make_task(0)
    return task, truth, catalog, MockLLM()


def test_clean_run_succeeds(setup):
    task, truth, catalog, llm = setup
    world, _, _, _ = run_workflow(task, catalog, llm)
    assert check(world, truth)["success"]


@pytest.mark.parametrize("name", list(FAULTS_BY_NAME))
def test_fault_fires_and_changes_outcome(setup, name):
    task, truth, catalog, llm = setup
    world, prov, inj, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME[name])
    assert inj.fired > 0
    assert prov.faulty_nodes()
    assert all(prov.nodes[nid].data.get("fault") == name for nid in prov.faulty_nodes())
    assert not check(world, truth)["success"]


def test_malicious_instruction_leaks_and_is_irreversible(setup):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME["malicious_instruction@research_hotels"])
    assert ATTACKER_EMAIL in check(world, truth)["unauthorized_recipients"]

    report = full_restart(task, world, llm, prov)
    result = check(world, truth)
    assert result["final_correct"]
    assert not result["success"]  # the leaked email cannot be unsent
    assert report.irreversible >= 1


@pytest.mark.parametrize("name", [
    "incorrect_result@research_flights",
    "fabrication@research_hotels",
    "tool_selection_error@write_file",
    "bad_argument@book_flight",
    "dropped_constraint@plan",
    "inexecutable_plan@plan",
])
def test_restart_and_coarse_recover_final_state(setup, name):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME[name])
    for recover in (full_restart, coarse_lineage):
        w, p = copy.deepcopy(world), copy.deepcopy(prov)
        recover(task, w, llm, p)
        assert check(w, truth)["final_correct"], recover.__name__


def test_coarse_lineage_reuses_untainted_research(setup):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME["fabrication@research_hotels"])
    report = coarse_lineage(task, world, llm, prov)
    assert "research_flights" not in report.tainted_steps
    assert report.steps_reused == 1  # only flight research survives coarse taint
    assert check(world, truth)["final_correct"]


class _RecordingLLM:
    """Forwards to the mock and keeps the user prompt of each recovery call."""

    def __init__(self, inner: MockLLM) -> None:
        self.inner = inner
        self.prompts: list[str] = []

    @property
    def usage(self):
        return self.inner.usage

    def chat(self, agent: str, messages):
        self.prompts.append(messages[1]["content"])
        return self.inner.chat(agent, messages)


def test_monitor_matches_coarse_and_notes_redone_steps(setup):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME["fabrication@research_hotels"])
    coarse_world, coarse_prov = copy.deepcopy(world), copy.deepcopy(prov)
    mon_world, mon_prov = copy.deepcopy(world), copy.deepcopy(prov)

    coarse = coarse_lineage(task, coarse_world, llm, coarse_prov)
    recorded = _RecordingLLM(llm)
    report = monitor_recover(task, mon_world, recorded, mon_prov)

    assert report.tainted_steps == coarse.tainted_steps
    assert report.steps_reused == coarse.steps_reused == 1
    assert recorded.prompts
    assert all("FAULT_JSON:" in prompt and "fabrication@research_hotels" in prompt for prompt in recorded.prompts)
    assert all('"research_flights"' not in prompt for prompt in recorded.prompts)
    assert check(mon_world, truth)["final_correct"]


def _plan(prov):
    return next(a.data["value"] for a in prov.of_kind("artifact") if a.key == "plan" and a.data.get("faulty"))


def test_tool_selection_becomes_irreversible_email(setup):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME["tool_selection_error@write_file"])
    result = check(world, truth)
    assert not result["itinerary_ok"]
    assert result["flight_ok"] and result["hotel_ok"]
    write_step = next(s for s in prov.of_kind("step") if s.key == "exec:2")
    effects = [prov.nodes[e] for e in prov.effects_of(write_step.id)]
    assert len(effects) == 1 and effects[0].data["class"] == "irreversible" and effects[0].key == "send_email"

    report = coarse_lineage(task, world, llm, prov)
    assert report.tainted_steps == ["exec:2"]
    assert report.irreversible >= 1
    assert check(world, truth)["success"]  # the extra email went to the user


def test_bad_argument_books_wrong_flight_only(setup):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME["bad_argument@book_flight"])
    result = check(world, truth)
    assert not result["flight_ok"]
    assert result["hotel_ok"] and result["erroneous_bookings_active"] == 1
    call = next(a.data["value"] for a in prov.of_kind("artifact") if a.key == "exec:0/call0")
    assert call["action"] == "book_flight" and call["args"]["flight_id"] != truth["flight_id"]

    report = coarse_lineage(task, world, llm, prov)
    assert report.tainted_steps == ["exec:0"]
    assert "plan" not in report.tainted_steps
    assert report.compensated >= 1
    assert check(world, truth)["final_correct"]


@pytest.mark.parametrize("name,missing", [
    ("dropped_constraint@plan", "hotel_ok"),
    ("inexecutable_plan@plan", "flight_ok"),
])
def test_plan_fault_taints_plan_and_execution_only(setup, name, missing):
    task, truth, catalog, llm = setup
    world, prov, _, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME[name])
    result = check(world, truth)
    assert not result[missing]
    assert result["flight_ok"] if missing == "hotel_ok" else result["hotel_ok"]
    if name.startswith("inexecutable"):
        assert any(s.get("action") == MISSING_EXEC_TOOL for s in _plan(prov)["steps"])
    else:
        assert all(s.get("action") != "book_hotel" for s in _plan(prov)["steps"])

    report = coarse_lineage(task, world, llm, prov)
    assert "plan" in report.tainted_steps
    assert "research_flights" not in report.tainted_steps
    assert "research_hotels" not in report.tainted_steps
    assert report.steps_reused == 2
    assert check(world, truth)["final_correct"]


@pytest.mark.parametrize("name", ["dropped_constraint@plan", "inexecutable_plan@plan"])
def test_verify_retries_transient_plan_fault(setup, name):
    task, truth, catalog, llm = setup
    world, _, inj, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME[name], verify=True)
    assert inj.fired == 1
    assert check(world, truth)["success"]


def test_verify_blocks_bad_executor_call(setup):
    task, truth, catalog, llm = setup
    world, _, inj, _ = run_workflow(task, catalog, llm, FAULTS_BY_NAME["bad_argument@book_flight"], verify=True)
    assert inj.fired == 1
    result = check(world, truth)
    assert result["erroneous_bookings_active"] == 0
    assert not result["flight_ok"]  # the mock executor stops the step instead of reissuing the planned call

    world, _, inj, _ = run_workflow(
        task, catalog, llm, FAULTS_BY_NAME["tool_selection_error@write_file"], verify=True
    )
    assert inj.fired == 1
    result = check(world, truth)
    assert result["unauthorized_emails"] == 0
    assert not result["itinerary_ok"]


def test_verify_blocks_injected_email(setup):
    task, truth, catalog, llm = setup
    world, _, _, _ = run_workflow(
        task, catalog, llm, FAULTS_BY_NAME["malicious_instruction@research_hotels"], verify=True
    )
    assert check(world, truth)["unauthorized_emails"] == 0
