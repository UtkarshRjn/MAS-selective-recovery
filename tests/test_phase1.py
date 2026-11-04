import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from threading import Event

import pytest
from agentrec.benchmark_faults import mutate_comparison, perturb
from agentrec.faults import FaultSpec, Injector
from agentrec.llm import Usage, MockLLM
from agentrec.programs import PROGRAMS, broken
from agentrec.coding import make_coding_task
from agentrec.runner import run_workflow, Recorder
from agentrec.phase1 import audit_coding, extended_case, run_travel
from agentrec import mbpp


@pytest.fixture
def fixture_case(monkeypatch):
    case = next(c for c in json.loads(Path('evals/mbpp-pilot.json').read_text())['cases'] if c['task_id'] == 7)
    monkeypatch.setitem(PROGRAMS, case['name'], {**case, 'benchmark': 'mbpp', 'image': 'unused'})
    def evaluate(source, program, split):
        passed = source == case['reference']
        return {'passed': passed, 'n': 1, 'n_passed': int(passed),
                'failures': [] if passed else [{'test_index': 0, 'error': 'AssertionError'}]}
    monkeypatch.setattr(mbpp, 'evaluate', evaluate)
    return case


class Answers:
    def __init__(self, case):
        self.case = case
        self.usage = Usage()
        self.messages = []
    def chat(self, agent, messages):
        self.usage.calls += 1
        self.usage.prompt_tokens += 10
        self.usage.completion_tokens += 5
        self.messages.append((agent, copy.deepcopy(messages)))
        if agent == 'coding_verifier':
            payload = json.loads(messages[-1]['content'])
            artifact = payload['artifact']
            if payload['role'] == 'spec':
                replacement = {'text': self.case['request']}
            elif payload['role'] == 'coder':
                replacement = {'content': self.case['reference']}
            else:
                replacement = artifact
            return {'accept': replacement == artifact, 'reason': 'test verifier', 'replacement': replacement}
        if agent == 'spec': return {'contract': self.case['request']}
        if agent == 'coder': return {'content': self.case['reference']}
        if agent == 'docs': return {'content': self.case['name']}
        return {'decision': 'approve', 'note': ''}


def values(prov):
    return {n.key:n.data['value'] for n in prov.of_kind('artifact')}


def test_mutation_changes_boundary_without_test_or_reference_access():
    original = 'def f(x):\n    return x >= 4\n'
    mutated, info = mutate_comparison(original)
    assert info == {'line': 2, 'from': 'GtE', 'to': 'Gt'}
    assert isinstance(ast.parse(mutated).body[0].body[0].value.ops[0], ast.Gt)
    assert mutate_comparison('def f(x):\n return x')[1] is None
    assert mutate_comparison('not valid python!')[1] is None


def test_timeout_is_missing_evidence_not_fake_assertion():
    ctx = SimpleNamespace(injector=Injector(FaultSpec('tester_timeout', 'tester', False)))
    actual = {'passed': True, 'n': 1, 'n_passed': 1, 'failures': [], 'source_sha256': 'abc'}
    altered, fired, event = perturb(ctx, 'tester', actual)
    assert fired and not altered['available'] and not altered['passed']
    assert altered['failures'][0]['error'] == 'simulated_tester_timeout'
    assert actual['passed'] and event['before'] == actual


def test_controlled_reports_are_matched_and_verification_corrects(fixture_case):
    case = fixture_case
    task, _, catalog = make_coding_task(case['name'])
    scenario = {'spec': case['request'], 'coder': case['reference'], 'coder:2': broken(case['name'])}
    w0,p0,_,_ = run_workflow(task,catalog,Answers(case),scenario=scenario)
    w1,p1,inj,_ = run_workflow(task,catalog,Answers(case),FaultSpec('stale_report','tester:2',False),scenario=scenario)
    assert inj.fired == 1
    assert values(p0)['coder:2'] == values(p1)['coder:2']
    assert not values(p0)['tester:2']['passed'] and values(p1)['tester:2']['passed']
    assert values(p1)['reviewer:2']['decision'] == 'approve'
    audit = audit_coding(task,w1,p1)
    assert audit['incorrect_code_stages'] == ['coder:2']
    assert audit['false_approval_stages'] == ['reviewer:2']
    assert audit['invalid_code_generation_tokens'] == 0
    assert audit['invalid_code_artifact_stage_tokens'] == 15
    model = Answers(case)
    wv,pv,_,_ = run_workflow(task,catalog,model,FaultSpec('stale_report','tester:2',False),verify=True,scenario=scenario)
    assert values(pv)['coder:2']['content'] == case['reference']
    assert model.usage.calls == 12
    assert audit_coding(task,wv,pv)['verification_tool_calls'] == 2
    # Private final assertions are absent even from verifier prompts.
    prompts = json.dumps(model.messages)
    for assertion in case['final_tests']:
        assert assertion not in prompts


def test_verifier_rechecks_timeout_and_accounts_extra_calls(fixture_case):
    task, _, catalog = make_coding_task(fixture_case['name'])
    model=Answers(fixture_case)
    w,p,inj,stats=run_workflow(task,catalog,model,FaultSpec('tester_timeout','tester',False),verify=True)
    assert inj.fired == 1 and values(p)['tester']['passed']
    assert values(p)['reviewer']['decision'] == 'approve'
    assert stats['usage']['calls'] == 12
    assert stats['provenance_write_s'] >= 0
    assert audit_coding(task,w,p)['verification_llm_calls'] == 5


def test_complete_extended_schedule_and_fixture_controls(fixture_case, tmp_path):
    case=fixture_case
    task,_,catalog=make_coding_task(case['name'])
    rec=Recorder(tmp_path)
    try:
        extended_case(case,SimpleNamespace(llm_seed=0,include_travel=False),Answers(case),rec,
                      task,catalog,{'success':True},Event())
    finally:
        rec.rows.close()
    rows=[json.loads(s) for s in (tmp_path/'rows.jsonl').read_text().splitlines()]
    controlled=[r for r in rows if r.get('controlled_fixture')]
    assert len(controlled)==8  # two scenarios, each control/none/verify/restart
    for scenario in {r['scenario'] for r in controlled}:
        group=[r for r in controlled if r['scenario']==scenario]
        faulty=next(r for r in group if r['method']=='none')
        assert faulty['fault_fired'] and faulty['compound_scenario']
        control=next(r for r in group if r['method']=='truthful_control')
        assert not control['fault_fired']
    assert all('invalid_code_generation_tokens' in r for r in rows)


def test_stateful_unauthorized_effect_survives_restart(tmp_path):
    rec=Recorder(tmp_path)
    try:
        run_travel(MockLLM(),rec,seed=0)
    finally:
        rec.rows.close()
    rows=[json.loads(s) for s in (tmp_path/'rows.jsonl').read_text().splitlines()]
    malicious=[r for r in rows if r['scenario'].startswith('malicious_instruction')]
    faulty=next(r for r in malicious if r['method']=='none')
    restarted=next(r for r in malicious if r['method']=='restart')
    assert faulty['unauthorized_emails']>0
    assert restarted['unauthorized_emails']>0 and restarted['irreversible']>0
    assert not restarted['strict_success']


def test_report_fixture_numeric_fallback_uses_only_feedback(fixture_case, monkeypatch):
    from agentrec import phase1
    calls = []
    def evaluate(source, name, split):
        calls.append((source, split))
        if source == fixture_case['reference']:
            return {'passed': True, 'failures': []}
        return {'passed': False, 'failures': [{'error': 'TypeError' if 'return []' in source else 'AssertionError'}]}
    monkeypatch.setattr(phase1, 'run_suite', evaluate)
    assert 'return 0' in phase1.qualify_report_fixture(fixture_case)
    assert len(calls) == 3 and all(split == 'feedback' for _, split in calls)


def test_travel_does_not_require_unrequested_refund_text(tmp_path, monkeypatch):
    from agentrec import checker
    original = checker.check
    def without_refund_text(world, truth):
        result = original(world, truth)
        result['refund_ok'] = False
        return result
    monkeypatch.setattr(checker, 'check', without_refund_text)
    rec = Recorder(tmp_path)
    try:
        run_travel(MockLLM(), rec, seed=0)
    finally:
        rec.rows.close()
    clean = json.loads((tmp_path/'rows.jsonl').read_text().splitlines()[0])
    assert clean['success'] and clean['strict_success'] and not clean['refund_ok']


def test_full_schedule_covers_all_fault_families_and_recovery_methods(fixture_case, tmp_path):
    case = fixture_case
    task, _, catalog = make_coding_task(case['name'])
    rec = Recorder(tmp_path)
    try:
        extended_case(case, SimpleNamespace(phase='full', llm_seed=0, include_travel=False,
                      include_authorization=False), Answers(case), rec, task, catalog,
                      {'success': True}, Event())
    finally:
        rec.rows.close()
    rows = [json.loads(s) for s in (tmp_path/'rows.jsonl').read_text().splitlines()]
    assert {r['fault'].split('@')[0] for r in rows if r.get('fault')} == {
        'bad_patch', 'drop_requirement', 'requirement_hijack', 'semantic_mutation',
        'tester_timeout', 'false_test_report', 'stale_report'}
    for row in rows:
        if row['method'] == 'none' and row['fault_fired']:
            assert {r['method'] for r in rows if r['scenario'] == row['scenario']} >= {
                'none', 'verify', 'restart', 'suffix', 'coarse'}
    assert all('code_stage_correctness' in r for r in rows)
