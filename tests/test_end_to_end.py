import copy

import pytest

from agentrec.checker import check
from agentrec.faults import ATTACKER_EMAIL, FAULTS_BY_NAME
from agentrec.llm import MockLLM
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


@pytest.mark.parametrize("name", ["incorrect_result@research_flights", "fabrication@research_hotels"])
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


def test_verify_blocks_injected_email(setup):
    task, truth, catalog, llm = setup
    world, _, _, _ = run_workflow(
        task, catalog, llm, FAULTS_BY_NAME["malicious_instruction@research_hotels"], verify=True
    )
    assert check(world, truth)["unauthorized_emails"] == 0
