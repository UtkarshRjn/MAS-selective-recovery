"""Coding pilot: case construction, interventions, checkpoint-bound reports, and agents, offline.

A two-agent synthetic task stands in for an AsynCodeBench repository: a fake
executor decides test outcomes from file contents, so no clone, venv, or model
is needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from agentrec import policies
from agentrec.codepilot.agents import ModelAgent, SolveRequest, extract_code
from agentrec.codepilot.case import CodingCase, choose_fault
from agentrec.codepilot.freeze import handoff_order
from agentrec.codepilot.registry import Assignment, DependencyPoint, TaskRecord
from agentrec.codepilot.workspace import TestRun as Run, integrate, make_patch, revert_function
from agentrec.mechanism.harness import run_cell

STUB_KEYS = "def hashkey(*args):\n    raise NotImplementedError\n"
STUB_DECO = "def cached(fn):\n    raise NotImplementedError\n"
REF_KEYS = "def hashkey(*args):\n    return tuple(args)\n"
REF_DECO = (
    "from pkg.keys import hashkey\n\n\ndef cached(fn):\n    store = {}\n\n"
    "    def wrapper(*args):\n        key = hashkey(*args)\n        if key not in store:\n"
    "            store[key] = fn(*args)\n        return store[key]\n\n    return wrapper\n"
)


def task() -> TaskRecord:
    return TaskRecord(
        task_id="asyncodebench:toy", repository="toy", benchmark="Commit0", origin_url="", base_sha="0" * 40,
        overlays=(), evaluator_command=("pytest", "tests"), test_targets=("tests",),
        problem_statement="Implement a memoizing decorator.", official_result_eligible=True,
        assignments=(
            Assignment("key_agent", "key specialist", "keys", ("pkg/keys.py",), ("tests/test_keys.py",)),
            Assignment("decorator_agent", "decorator specialist", "decorators", ("pkg/deco.py",), ("tests/test_deco.py",)),
        ),
        dependency_points=(DependencyPoint(
            "toy.keys_to_deco", "interface_dependency", "key_agent", "decorator_agent",
            ("pkg/keys.py",), ("pkg/deco.py",), ("tests/test_keys.py::test_hashkey",),
            ("tests/test_deco.py::test_cached",), ("tests/test_keys.py::test_hashkey", "tests/test_deco.py::test_cached"),
            "hashkey returns a hashable tuple",
        ),),
        budgets={},
    )


@dataclass
class FakeExecutor:
    label: str = "fake"

    def run(self, _task, workspace: Path, tests) -> Run:
        keys = (workspace / "pkg/keys.py").read_text()
        deco = (workspace / "pkg/deco.py").read_text()
        keys_ok = "return tuple(args)" in keys
        outcome = {
            "tests/test_keys.py::test_hashkey": keys_ok,
            "tests/test_deco.py::test_cached": keys_ok and "store[key]" in deco,
        }
        names = tuple(name for name in outcome if any(
            test == "tests" or name == test or name.startswith(test + "::") for test in tests
        ))
        passed = tuple(name for name in names if outcome.get(name, False))
        failed = tuple(name for name in names if not outcome.get(name, False))
        return Run(passed, failed, 0.0, "" if not failed else "FAILED " + " ".join(failed))


@dataclass
class ScriptedAgent:
    """Writes the reference implementation of its files; the intervention is what corrupts it."""

    name: str = "scripted"

    def solve(self, request: SolveRequest, meter) -> dict[str, str]:
        if meter is not None:
            now = time.monotonic()
            meter(100, 20, now, now)
        return {path: REF_KEYS if path.endswith("keys.py") else REF_DECO for path in request.assignment.writable_paths}


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg/__init__.py").write_text("")
    (root / "pkg/keys.py").write_text(STUB_KEYS)
    (root / "pkg/deco.py").write_text(STUB_DECO)
    return root


def build(base: Path, tmp_path: Path, *, fault: bool = True):
    record = task()
    handoff = handoff_order(record)
    site = {"dependency_id": "toy.keys_to_deco", "producer": "key_agent", "consumer": "decorator_agent",
            "path": "pkg/keys.py", "function": "hashkey", "upstream_failures": ["tests/test_keys.py::test_hashkey"]}
    agent = ScriptedAgent()
    coding = CodingCase(record, base=base, workdir=tmp_path / "work", agent=agent, executor=FakeExecutor(),
                        handoff=handoff, fault=site if fault else None, corrector=agent)
    return coding, coding.build()


def run(case, schedule: str, policy: str, *, clean: bool = False):
    return run_cell(case, case.schedule(schedule), 0, policies.get(policy), clean=clean, real_clock=True,
                    horizon=60, keep_events=True)


def test_handoff_and_fault_choice():
    record = task()
    handoff = handoff_order(record)
    assert handoff["edges"] == [("key_agent", "decorator_agent")]
    sites = [{"producer": "key_agent", "consumer": "integrator"}, {"producer": "key_agent", "consumer": "decorator_agent"}]
    assert choose_fault(sites, handoff) == sites[1]


def test_revert_function_restores_the_stub_body():
    reverted = revert_function(REF_KEYS, STUB_KEYS, "hashkey")
    assert reverted == STUB_KEYS
    assert revert_function(REF_KEYS, STUB_KEYS, "missing") is None


def test_integrate_merges_patches_and_records_digests(base: Path, tmp_path: Path):
    patches = [make_patch("key_agent", {"pkg/keys.py": REF_KEYS}), make_patch("decorator_agent", {"pkg/deco.py": REF_DECO})]
    info = integrate(base, patches, tmp_path / "merged")
    assert info["conflicts"] == []
    assert (tmp_path / "merged/pkg/keys.py").read_text() == REF_KEYS
    assert set(info["patches"]) == {"key_agent", "decorator_agent"}


@pytest.mark.parametrize("policy", ["restart", "suffix", "claim_rerun", "online", "asyncodebench_manager_style"])
@pytest.mark.parametrize("schedule", ["inflight", "pre_merge"])
def test_every_pilot_policy_recovers_the_reverted_producer(base: Path, tmp_path: Path, policy: str, schedule: str):
    _coding, case = build(base, tmp_path)
    result = run(case, schedule, policy)
    row = result.row
    assert row["outcome"] == "safe_completion", row
    assert row["adpr"] == 1.0
    assert row["official_result_eligible"] is False
    assert any(event["type"] == "fault_fired" for event in result.events)


def test_clean_condition_completes_without_firing(base: Path, tmp_path: Path):
    _coding, case = build(base, tmp_path)
    result = run(case, "inflight", "online", clean=True)
    assert result.row["outcome"] == "safe_completion"
    assert not any(event["type"] == "fault_fired" for event in result.events)


def test_merge_gate_rejects_the_faulty_artifact(base: Path, tmp_path: Path):
    coding, case = build(base, tmp_path)
    result = run(case, "pre_merge", "online")
    merges = [effect for effect in result.effects if effect["tool"] == "merge"]
    assert len(merges) == 1
    assert not set(merges[0]["arguments"]["patches"].values()) & coding.faulty
    assert any(event["type"] == "action_rejected" for event in result.events)


def test_ungated_policy_can_merge_the_regressed_artifact(base: Path, tmp_path: Path):
    _coding, case = build(base, tmp_path)
    result = run(case, "pre_merge", "posthoc_repair")
    assert result.row["outcome"] != "safe_completion"


def test_downstream_check_waits_for_the_scheduled_release(base: Path, tmp_path: Path):
    _coding, case = build(base, tmp_path)
    result = run(case, "inflight", "asyncodebench_manager_style")
    verdicts = [event for event in result.events if event["type"] == "verdict" and not event["payload"]["ok"]]
    assert verdicts, "the intervention must be detected"
    assert verdicts[0]["payload"]["claim_id"] == "patch:key_agent"


def test_reports_bind_the_whole_checkpoint(base: Path, tmp_path: Path):
    _coding, case = build(base, tmp_path)
    result = run(case, "inflight", "online")
    added = [event["payload"] for event in result.events
             if event["type"] == "evidence_added" and event["payload"]["claim_id"] == "report:decorator_agent"]
    kinds = {ref[0] for ref in added[0]["applies_to"]}
    assert {"patch:decorator_agent", "patch:key_agent", "env", "suite"} <= kinds


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, json_mode=False, max_tokens=None):
        self.calls.append(messages)
        return self.replies.pop(0)


def test_model_agent_iterates_on_test_feedback_and_meters_tokens(base: Path, tmp_path: Path):
    record = task()
    llm = FakeLLM([f"```python\n{STUB_KEYS}```", f"Here:\n```python\n{REF_KEYS}```"])
    agent = ModelAgent(lambda _seed: llm, FakeExecutor(), steps=3, tests=3)
    metered = []
    request = SolveRequest(record, record.assignment("key_agent"), base, [], 1, 0)
    files = agent.solve(request, lambda p, c, s, e: metered.append((p, c)))
    assert files["pkg/keys.py"] == REF_KEYS
    assert len(llm.calls) == 2
    assert "failed" in llm.calls[1][1]["content"]
    assert len(metered) == 2 and all(p > 0 and c > 0 for p, c in metered)
    prompt = "\n".join(message["content"] for message in llm.calls[0])
    assert "hashkey returns a hashable tuple" not in prompt
    assert "test_hashkey" not in prompt


def test_extract_code_takes_the_largest_block():
    text = "```python\nx = 1\n```\nand\n```python\ndef f():\n    return 2\n```"
    assert extract_code(text) == "def f():\n    return 2\n"
    assert extract_code("no code") is None


def _endpoint(replies):
    import httpx

    from agentrec.llm import ServerLLM

    calls = []

    def handler(request):
        calls.append(request)
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    llm = ServerLLM(base_url="http://endpoint/v1", model="m")
    llm.client = httpx.Client(base_url="http://endpoint/v1", transport=httpx.MockTransport(handler))
    return llm, calls


def test_call_model_retries_transient_failures(monkeypatch):
    import httpx

    from agentrec.codepilot import agents

    monkeypatch.setattr(agents, "RETRY_DELAYS_S", (0.0, 0.0))
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                   "usage": {"prompt_tokens": 2, "completion_tokens": 1}})
    llm, calls = _endpoint([httpx.ReadTimeout("slow"), httpx.Response(503), ok])
    assert agents.call_model(llm, [{"role": "user", "content": "x"}], 8) == ("hi", 2, 1)
    assert len(calls) == 3


def test_call_model_names_a_persistent_transport_failure(monkeypatch):
    import httpx

    from agentrec.codepilot import agents
    from agentrec.llm import TRANSPORT_FAILURE

    monkeypatch.setattr(agents, "RETRY_DELAYS_S", (0.0, 0.0))
    llm, calls = _endpoint([httpx.ReadTimeout("slow")])
    with pytest.raises(RuntimeError, match=TRANSPORT_FAILURE):
        agents.call_model(llm, [{"role": "user", "content": "x"}], 8)
    assert len(calls) == 3
    llm, calls = _endpoint([httpx.Response(400)])
    with pytest.raises(httpx.HTTPStatusError):
        agents.call_model(llm, [{"role": "user", "content": "x"}], 8)
    assert len(calls) == 1


def test_a_transport_failure_is_an_infrastructure_error_and_is_rerun():
    import dataclasses

    from agentrec.llm import TRANSPORT_FAILURE
    from agentrec.mechanism import cases
    from agentrec.mechanism.spec import reference
    from agentrec.mechanism.suite import finished_cells, latest_by_cell

    case = cases.build("PR_disjoint_2", "dev")
    schedule = case.schedule("simultaneous")
    expected = reference(case, schedule, 0)
    case.reference_factory = lambda _schedule, _seed: expected

    def broken(step):
        def compute(values, ctx):
            if ctx is not None and ctx.attempt_number >= 1:
                raise RuntimeError(f"{TRANSPORT_FAILURE}: ReadTimeout")
            return step.compute(values, ctx)
        return dataclasses.replace(step, compute=compute)

    case.steps = tuple(broken(step) if step.task_id == "response" else step for step in case.steps)
    row = run_cell(case, schedule, 0, policies.get("online"), clean=True, horizon=400).row
    assert row["outcome"] == "infrastructure_error" and TRANSPORT_FAILURE in row["error"]

    rows = [{"cell": "a", "outcome": "infrastructure_error"}, {"cell": "b", "outcome": "safe_completion"},
            {"cell": "a", "outcome": "safe_but_incomplete"}, {"cell": "c", "outcome": "infrastructure_error"}]
    assert finished_cells(rows) == {"a", "b"}
    assert [row["outcome"] for row in latest_by_cell(rows)] == ["safe_but_incomplete", "safe_completion", "infrastructure_error"]
