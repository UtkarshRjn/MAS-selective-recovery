"""Deterministic harness tests, not model attack-success evidence."""
import copy
import json
import pytest
from agentrec.authorization import (make_authorization_case, policy_violations,
                                    check_authorization, run_authorization_cases)
from agentrec.agents import read_field, planner_messages, executor_messages
from agentrec.faults import AUTHORIZATION_ATTACKS, FaultSpec, Injector
from agentrec.llm import MockLLM
from agentrec.runner import run_workflow, Recorder
from agentrec.recovery import full_restart
from agentrec.workflow import RunContext, TravelWorkflow
from agentrec.world import World


class PolicyMock(MockLLM):
    """Scripted vulnerable planner follows injected instructions; verifier blocks policy violations."""
    def _planner(self, messages):
        task = read_field(messages[1]['content'], 'TASK_JSON')
        findings = read_field(messages[1]['content'], 'FINDINGS_JSON')
        plan = super()._planner(messages)
        notes = findings['hotels'].get('notes', '')
        if not task['authorization_policy']['booking_approved'] and 'HOTEL INSTRUCTION:' not in notes:
            plan['steps'] = [s for s in plan['steps'] if s['action'] not in ('book_flight', 'book_hotel')]
        if 'cancel booking PROTECTED-001' in notes:
            plan['steps'].insert(0, {'action': 'cancel_booking', 'args': {'booking_id': 'PROTECTED-001'}})
        return plan

    def _verifier(self, messages):
        user = messages[1]['content']
        task, kind, item = (read_field(user, k) for k in ('TASK_JSON', 'REVIEW_KIND', 'ITEM_JSON'))
        # Deliberately let research and plans through so action-level blocking is exercised.
        bad = policy_violations(task, item.get('action'), item.get('args', {})) if kind == 'action' else []
        return {'ok': not bad, 'reason': ','.join(bad)}


@pytest.mark.parametrize('name', AUTHORIZATION_ATTACKS)
def test_clean_attack_verify_and_restart(name):
    task, truth, catalog = make_authorization_case(name)
    original = copy.deepcopy(task)
    clean, _, _, _ = run_workflow(task, catalog, PolicyMock())
    assert check_authorization(clean, task, truth)['success']
    spec = FaultSpec(name, 'research_hotels', True)
    world, prov, inj, _ = run_workflow(task, catalog, PolicyMock(), spec)
    result = check_authorization(world, task, truth)
    assert inj.fired and spec.adversarial
    assert result['authorization_by_type'][name]['committed'] > 0
    assert not result['success']
    assert task == original  # source text cannot rewrite policy
    verified, _, _, _ = run_workflow(task, catalog, PolicyMock(), spec, verify=True)
    vr = check_authorization(verified, task, truth)
    assert vr['unauthorized_actions_attempted'] > 0
    assert vr['unauthorized_attempts_blocked_by_verifier'] > 0
    assert vr['unauthorized_actions_committed'] == 0
    assert vr['success']
    previous = copy.deepcopy(world.action_history)
    full_restart(task, world, PolicyMock(), prov)
    assert world.action_history[:len(previous)] == previous
    after = check_authorization(world, task, truth)
    assert not after['historical_authorization_safe'] and not after['success']
    if name == 'booking_without_approval':
        assert after['task_correct']  # compensated, but historical violation survives
    if name == 'unauthorized_cancellation':
        assert not after['protected_bookings_preserved']


def test_policy_is_distinct_from_correctness_and_normalizes_recipients():
    task, _, _ = make_authorization_case('unauthorized_recipient')
    assert policy_violations(task, 'send_email', {'to': ' ALICE@EXAMPLE.COM '}) == []
    assert policy_violations(task, 'book_flight', {'flight_id': 'WRONG'}) == []
    assert policy_violations(task, 'cancel_booking', {'booking_id': 'UNKNOWN'}) == ['unauthorized_cancellation']


def test_failed_tool_is_attempt_not_commit():
    task, truth, catalog = make_authorization_case('unauthorized_cancellation')
    w, p, _, _ = run_workflow(task, catalog, PolicyMock())
    ctx = RunContext(task, w, PolicyMock(), prov=p)
    task_id = next(n.id for n in p.of_kind('artifact') if n.key == 'task')
    plan_id = next(n.id for n in p.of_kind('artifact') if n.key == 'plan')
    item = {'action': 'cancel_booking', 'args': {'booking_id': 'MISSING'}}
    TravelWorkflow().execute(ctx, 99, item, task_id, plan_id, {'steps': [item]}, {})
    result = check_authorization(w, task, truth)
    assert result['unauthorized_actions_attempted'] == 1
    assert result['unauthorized_actions_committed'] == 0


def test_attack_only_changes_observation_description():
    for name in AUTHORIZATION_ATTACKS:
        task, _, catalog = make_authorization_case(name)
        obs = World(*catalog).search_hotels(task['dest'], task['min_rating'], task['max_price'])
        before = copy.deepcopy(obs)
        new, fired = Injector(FaultSpec(name, 'research_hotels', True)).on_observation(
            'research_hotels', 'search_hotels', task, obs)
        assert fired and obs == before
        assert new[0]['description'] != obs[0]['description']
        new[0]['description'] = obs[0]['description']
        assert new == obs


def test_planning_only_prompts_do_not_require_bookings():
    task, _, _ = make_authorization_case('booking_without_approval')
    planner = planner_messages(task, {})
    assert 'each booking, the itinerary file' not in planner[0]['content']
    assert 'planning-only' in planner[1]['content']
    assert 'cancel_booking' in executor_messages(task, {'steps': []}, 0, {})[0]['content']


def test_runner_records_twelve_conditions_and_serializes_history(tmp_path):
    rec = Recorder(tmp_path)
    try:
        run_authorization_cases(PolicyMock(), rec)
    finally:
        rec.rows.close()
    rows = [json.loads(s) for s in (tmp_path/'rows.jsonl').read_text().splitlines()]
    assert len(rows) == 12
    assert sum(r['method'] == 'clean' and r['success'] for r in rows) == 3
    for p in (tmp_path/'traces').glob('*.json'):
        assert 'action_history' in json.loads(p.read_text())['world']
    assert all(r['new_unauthorized_commits_during_recovery'] == 0 for r in rows if r['method'] == 'restart')
