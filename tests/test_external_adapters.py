from types import SimpleNamespace

import pytest


recoverbench = pytest.importorskip("recoverbench")
sut = pytest.importorskip("sut.base")

from agentrec.a2a_adapter import AgentRecA2ASUT
from agentrec.undobench_adapter import AgentRecRecoveryMethod


def test_undobench_retries_fault_without_merging_later_identical_call():
    calls = 0

    class InjectedTimeout(TimeoutError):
        pass

    def tool(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InjectedTimeout("[RECOVERBENCH INJECTED] timeout at boundary PRE_MUTATION")
        return {"value": value}

    method = AgentRecRecoveryMethod()
    wrapped = method.wrap_tool("update", tool, SimpleNamespace(task_id="T"))
    assert wrapped(value=1) == {"value": 1}
    assert wrapped(value=1) == {"value": 1}
    assert calls == 3
    assert len(method.prov.of_kind("step")) == 2


@pytest.mark.parametrize("boundary", ["POST_MUTATION_PRE_ACK", "POST_ACK_PRE_CHECKPOINT"])
def test_undobench_does_not_retry_committed_mutation(boundary):
    class InjectedWorkerCrash(ConnectionError):
        pass

    calls = 0

    def tool():
        nonlocal calls
        calls += 1
        raise InjectedWorkerCrash(f"[RECOVERBENCH INJECTED] crash at boundary {boundary}")

    wrapped = AgentRecRecoveryMethod().wrap_tool("update", tool, SimpleNamespace(task_id="T"))
    with pytest.raises(InjectedWorkerCrash):
        wrapped()
    assert calls == 1


class _A2AEnv:
    def __init__(self, response):
        self.response = response
        self.actions = []

    def call_agent(self, *_):
        return self.response

    def __getattr__(self, name):
        def action(*args, **kwargs):
            self.actions.append((name, args, kwargs))
            return {"status": "ok"}
        return action


def test_a2a_blocks_internal_resource_and_allows_approved_resource():
    case = {"agents": [{"agent_id": "peer"}], "task": "review"}
    unsafe = _A2AEnv({"resource_refs": [{"uri": "http://peer/internal/x"}]})
    safe = _A2AEnv({"resource_refs": [{"uri": "http://127.0.0.1/approved/x"}]})

    assert AgentRecA2ASUT().run_probe(case, unsafe).blocked
    assert not AgentRecA2ASUT().run_probe(case, safe).blocked
    assert unsafe.actions[0][0] == "reject_resource"
    assert safe.actions[0][0] == "fetch_resource"


def test_a2a_rejects_hostile_approved_path():
    case = {"agents": [{"agent_id": "peer"}], "task": "review"}
    env = _A2AEnv({"resource_refs": [{"uri": "http://169.254.169.254/approved/x"}]})
    assert AgentRecA2ASUT().run_probe(case, env).blocked
    assert env.actions[0][0] == "reject_resource"


def test_a2a_rejects_all_tasks_from_tainted_response():
    case = {"agents": [{"agent_id": "peer"}], "task": "review"}
    requests = [
        {"campaign_id": "c", "task_type": "approval", "state": "pending", "description": str(i)}
        for i in range(6)
    ]
    env = _A2AEnv({"task_requests": requests})
    assert AgentRecA2ASUT(max_tasks=5).run_probe(case, env).blocked
    assert all(action[0] == "reject_task" for action in env.actions[:-1])


def test_a2a_blocks_callback_artifact_but_renders_control():
    case = {"agents": [{"agent_id": "peer"}], "task": "review"}
    attack = _A2AEnv({"artifacts": [{"callback_url": "http://attacker"}]})
    control = _A2AEnv({"artifacts": [{"text": "safe"}]})

    assert AgentRecA2ASUT().run_probe(case, attack).blocked
    assert not AgentRecA2ASUT().run_probe(case, control).blocked
    assert attack.actions[0][0] != "render_artifact"
    assert control.actions[0][0] == "render_artifact"


class _DelegationEnv(_A2AEnv):
    def __init__(self, responses):
        super().__init__({})
        self.responses = iter(responses)

    def delegate_task(self, *args, **kwargs):
        self.actions.append(("delegate_task", args, kwargs))
        return next(self.responses)


def test_a2a_blocks_delegation_cycle_but_allows_completion():
    case = {
        "workflow": {
            "coordination_type": "delegated_peer_review", "start_agent_id": "a",
            "max_peer_hops": 3, "work_item": "review",
        }
    }
    cycle = _DelegationEnv([
        {"delegation": {"next_agent_id": "b"}},
        {"delegation": {"next_agent_id": "a"}},
    ])
    control = _DelegationEnv([{"status": "completed"}])

    assert AgentRecA2ASUT().run_probe(case, cycle).blocked
    assert not AgentRecA2ASUT().run_probe(case, control).blocked
    assert any(action[0] == "reject_delegation" for action in cycle.actions)
