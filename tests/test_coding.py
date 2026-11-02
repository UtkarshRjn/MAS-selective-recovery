import copy

import pytest

from agentrec.coding import _CODING_SYSTEM, check_coding, make_coding_task
from agentrec.faults import FaultSpec
from agentrec.llm import MockLLM
from agentrec.programs import PROGRAMS, contract_text, implement, run_suite, strip_requirement
from agentrec.recovery import coarse_lineage, suffix_recover, support_recover
from agentrec.runner import run_workflow

VISITS = [
    "spec",
    "coder",
    "tester",
    "reviewer",
    "docs",
    "spec:2",
    "coder:2",
    "tester:2",
    "reviewer:2",
]


def _keys(prov):
    return [s.key for s in sorted(prov.of_kind("step"), key=lambda n: n.seq)]


def _apply(method, task, world, prov):
    world, prov = copy.deepcopy(world), copy.deepcopy(prov)
    return method(task, world, MockLLM(), prov), world


def test_prompts_do_not_contain_the_reference_bodies():
    blob = "\n".join(_CODING_SYSTEM.values())
    assert "min(max" not in blob
    assert "n != 0" not in blob
    assert "return \"\"" not in blob


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_suite_fails_when_the_dropped_requirement_is_missing(name):
    full = implement(name, contract_text(name))
    thin = implement(name, strip_requirement(name, contract_text(name)))
    assert run_suite(full, name)["passed"]
    assert not run_suite(thin, name)["passed"]


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_clean_program_passes_the_hidden_suite(name):
    task, _, catalog = make_coding_task(name)
    world, prov, inj, _ = run_workflow(task, catalog, MockLLM())
    assert inj.fired == 0
    assert _keys(prov) == VISITS
    result = check_coding(world, task)
    assert result["success"]
    assert result["n_passed"] == result["n"]
    assert task["name"] in world.files[task["doc_path"]]


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_dropped_requirement_support_leaves_the_submission_failing(name):
    task, _, catalog = make_coding_task(name)
    world, prov, inj, _ = run_workflow(
        task, catalog, MockLLM(), FaultSpec("drop_requirement", "spec", persistent=True)
    )
    assert inj.fired == 2
    assert [prov.nodes[nid].key for nid in prov.faulty_nodes()] == ["spec"]
    assert not check_coding(world, task)["tests_ok"]

    coarse, coarse_world = _apply(coarse_lineage, task, world, prov)
    support, support_world = _apply(support_recover, task, world, prov)
    suffix, suffix_world = _apply(suffix_recover, task, world, prov)

    assert "coder:2" in coarse.tainted_steps
    assert "coder:2" not in support.tainted_steps
    assert "coder" in support.tainted_steps
    assert check_coding(coarse_world, task)["success"]
    assert check_coding(suffix_world, task)["success"]
    assert not check_coding(support_world, task)["success"]


def test_bad_patch_suffix_reruns_docs_and_coarse_keeps_them():
    task, _, catalog = make_coding_task("clamp")
    world, prov, inj, _ = run_workflow(
        task, catalog, MockLLM(), FaultSpec("bad_patch", "coder", persistent=True)
    )
    assert inj.fired == 2
    assert not check_coding(world, task)["success"]

    coarse, coarse_world = _apply(coarse_lineage, task, world, prov)
    suffix, suffix_world = _apply(suffix_recover, task, world, prov)
    support, support_world = _apply(support_recover, task, world, prov)

    assert "docs" not in coarse.tainted_steps
    assert "docs" in suffix.tainted_steps
    assert "coder:2" in support.tainted_steps
    assert "reviewer" not in support.tainted_steps
    assert check_coding(coarse_world, task)["success"]
    assert check_coding(suffix_world, task)["success"]
    assert check_coding(support_world, task)["success"]
