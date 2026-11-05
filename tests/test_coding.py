import copy

from agentrec.coding import check_coding, make_coding_task
from agentrec.faults import FaultSpec
from agentrec.llm import MockLLM
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
FIRST_LAP = ["spec", "coder", "tester", "reviewer", "docs"]


def _keys(prov):
    return [s.key for s in sorted(prov.of_kind("step"), key=lambda n: n.seq)]


def _apply(method, task, world, prov):
    world, prov = copy.deepcopy(world), copy.deepcopy(prov)
    return method(task, world, MockLLM(), prov), world


def test_coding_loop_unrolls_two_laps():
    task, _, catalog = make_coding_task()
    world, prov, inj, _ = run_workflow(task, catalog, MockLLM())
    assert inj.fired == 0
    assert _keys(prov) == VISITS
    assert check_coding(world, task)["success"]
    assert "lo <= hi" in world.files[task["path"]]


def test_bad_patch_suffix_reruns_docs_and_coarse_keeps_them():
    task, _, catalog = make_coding_task()
    spec = FaultSpec("bad_patch", "coder", persistent=True)
    world, prov, inj, _ = run_workflow(task, catalog, MockLLM(), spec)
    assert inj.fired == 2
    assert not check_coding(world, task)["success"]

    coarse, coarse_world = _apply(coarse_lineage, task, world, prov)
    suffix, suffix_world = _apply(suffix_recover, task, world, prov)
    support, support_world = _apply(support_recover, task, world, prov)

    assert "docs" not in coarse.tainted_steps
    assert "spec" not in coarse.tainted_steps
    assert "coder:2" in coarse.tainted_steps
    assert "docs" in suffix.tainted_steps
    assert "spec" not in suffix.tainted_steps
    # The patch supports the tester only, so the review and the second spec stay.
    assert "docs" not in support.tainted_steps
    assert "reviewer" not in support.tainted_steps
    assert "coder" in support.tainted_steps and "coder:2" in support.tainted_steps

    assert check_coding(coarse_world, task)["success"]
    assert check_coding(suffix_world, task)["success"]
    assert check_coding(support_world, task)["success"]


def test_thin_spec_keeps_the_first_lap():
    task, _, catalog = make_coding_task()
    spec = FaultSpec("thin_spec", "spec:2", persistent=False)
    world, prov, inj, _ = run_workflow(task, catalog, MockLLM(), spec)
    assert inj.fired == 1
    assert not check_coding(world, task)["tests_ok"]

    coarse, coarse_world = _apply(coarse_lineage, task, world, prov)
    suffix, suffix_world = _apply(suffix_recover, task, world, prov)

    for report in (coarse, suffix):
        assert all(key not in report.tainted_steps for key in FIRST_LAP)
        assert "spec:2" in report.tainted_steps
        assert "coder:2" in report.tainted_steps
    assert "docs" not in suffix.tainted_steps
    assert check_coding(coarse_world, task)["success"]
    assert check_coding(suffix_world, task)["success"]
