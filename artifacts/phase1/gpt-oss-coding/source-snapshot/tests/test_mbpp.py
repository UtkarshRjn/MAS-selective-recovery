import copy
import json
from pathlib import Path

import pytest

from agentrec import mbpp
from agentrec.coding import CodingWorkflow, make_coding_task
from agentrec.faults import FaultSpec, Injector
from agentrec.llm import MockLLM
from agentrec.programs import PROGRAMS, broken, run_suite
from agentrec.workflow import RunContext
from agentrec.world import World

MANIFEST = Path(__file__).resolve().parents[1] / 'evals/mbpp-pilot.json'

@pytest.fixture
def registered(monkeypatch):
    manifest = json.loads(MANIFEST.read_text())
    for c in manifest['cases']:
        monkeypatch.setitem(PROGRAMS, c['name'], {**copy.deepcopy(c), 'benchmark': 'mbpp', 'image': 'unused'})
    return manifest['cases'][0]


def test_feedback_and_final_tests_are_disjoint():
    manifest = json.loads(MANIFEST.read_text())
    assert len(manifest['cases']) == 5
    for case in manifest['cases']:
        assert len(case['feedback_tests']) == 1
        assert len(case['final_tests']) >= 2
        assert not set(case['feedback_tests']) & set(case['final_tests'])


def test_private_fields_do_not_enter_task_or_messages(registered):
    task, _, _ = make_coding_task(registered['name'])
    ctx = RunContext(task, World([], []), MockLLM())
    workflow = CodingWorkflow()
    for role in ('spec', 'coder', 'reviewer', 'docs'):
        text = json.dumps(workflow._messages(ctx, role, 1))
        assert registered['reference'] not in text
        for test in registered['final_tests']:
            assert test not in text
    assert not {'reference', 'final_tests', 'feedback_tests'} & task.keys()


def test_fault_targets_only_selected_coder_and_only_changes_count(registered):
    task, _, _ = make_coding_task(registered['name'])
    ctx = RunContext(task, World([], []), MockLLM(),
                     injector=Injector(FaultSpec('bad_patch', 'coder:2', False)))
    w = CodingWorkflow()
    original = registered['reference']
    assert w._bad_patch(ctx, 'coder', original) == (original, False)
    assert ctx.injector.fired == 0
    assert w._bad_patch(ctx, 'coder:2', broken(task['name']))[1] is False
    assert ctx.injector.fired == 0
    assert w._bad_patch(ctx, 'coder:2', original) == (broken(task['name']), True)
    assert ctx.injector.fired == 1
    assert w._bad_patch(ctx, 'coder:2', original) == (original, False)


def test_suite_routes_to_correct_split(registered, monkeypatch):
    calls = []
    def evaluate(src, program, split):
        calls.append(split)
        return {'passed': True}
    monkeypatch.setattr(mbpp, 'evaluate', evaluate)
    run_suite('source', registered['name'], split='feedback')
    run_suite('source', registered['name'])
    assert calls == ['feedback', 'final']


def test_source_tampering_rejected(tmp_path):
    source = tmp_path/'source.json'
    source.write_text('[]')
    with pytest.raises(ValueError, match='hash mismatch'):
        mbpp.prepare(source, 5)


def test_run_requires_explicit_execution():
    from types import SimpleNamespace
    with pytest.raises(ValueError, match='execute-model'):
        mbpp.cmd_run(SimpleNamespace(execute_model=False))


@pytest.mark.parametrize('target', ['coder', 'coder:2'])
def test_paired_workflow_and_restart_plumbing(registered, monkeypatch, target):
    # Fixed answers test orchestration only, not model quality.
    from agentrec.llm import Usage
    from agentrec.runner import run_workflow
    from agentrec.coding import check_coding
    from agentrec.recovery import full_restart
    calls = []
    def evaluate(source, program, split):
        calls.append(split)
        passed = source == registered['reference']
        return {'passed': passed, 'n': 1, 'n_passed': int(passed),
                'failures': [] if passed else [{'error': 'AssertionError'}]}
    monkeypatch.setattr(mbpp, 'evaluate', evaluate)
    class FixedAnswers:
        def __init__(self):
            self.usage = Usage()
        def chat(self, agent, messages):
            self.usage.calls += 1
            if agent == 'spec': return {'contract': registered['request']}
            if agent == 'coder': return {'content': registered['reference']}
            if agent == 'docs': return {'content': registered['name']}
            return {'decision': 'approve', 'note': ''}
    task, _, catalog = make_coding_task(registered['name'])
    world, prov, injector, _ = run_workflow(task, catalog, FixedAnswers(),
                                          FaultSpec('bad_patch', target, False))
    assert calls == ['feedback', 'feedback']
    assert injector.fired == 1
    assert [prov.nodes[n].key for n in prov.faulty_nodes()] == [target]
    # A transient first-draft fault can be repaired by the ordinary second lap.
    assert check_coding(world, task)['success'] == (target == 'coder')
    report = full_restart(task, world, FixedAnswers(), prov)
    assert report.steps_recomputed == 9
    assert check_coding(world, task)['success']


def test_server_model_and_credentials_are_sent_only_in_request():
    import httpx
    from agentrec.llm import ServerLLM
    llm = ServerLLM(base_url='https://example.invalid/v1', model='test-model', api_key='test-secret')
    original_headers = dict(llm.client.headers)
    llm.client.close()
    def respond(request):
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-secret'
        body = json.loads(request.content)
        assert body['model'] == 'test-model'
        assert 'test-secret' not in request.content.decode()
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok": true}'}}],
                                         'usage': {'prompt_tokens': 3, 'completion_tokens': 2}})
    llm.client = httpx.Client(base_url='https://example.invalid/v1', headers=original_headers,
                              transport=httpx.MockTransport(respond))
    try:
        assert llm.chat('test', [{'role': 'user', 'content': 'test'}]) == {'ok': True}
        assert llm.usage.calls == 1
    finally:
        llm.client.close()


def test_parallel_scheduler_overlaps_and_merges_in_manifest_order(tmp_path):
    from threading import Barrier, Lock
    barrier, lock = Barrier(2), Lock()
    active = maximum = 0
    def worker(case, out, stop):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        barrier.wait(timeout=3)
        (out/'rows.jsonl').write_text(json.dumps({'task_id': case['name']})+'\n')
        with lock:
            active -= 1
    cases = [{'name': str(i)} for i in range(4)]
    mbpp.execute_cases(cases, 2, tmp_path, worker)
    assert maximum == 2
    assert [json.loads(s)['task_id'] for s in (tmp_path/'rows.jsonl').read_text().splitlines()] == ['0','1','2','3']
    status = json.loads((tmp_path/'run_status.json').read_text())
    assert all(s['status'] == 'completed' for s in status['tasks'].values())


def test_failure_stops_new_tasks_and_preserves_partial_rows(tmp_path):
    from threading import Barrier
    barrier = Barrier(2)
    def worker(case, out, stop):
        barrier.wait(timeout=3)
        if case['name'] == 'bad':
            (out/'rows.jsonl').write_text('{"method":"clean"}\n')
            raise ValueError('secret should not appear in status')
        assert stop.wait(timeout=3)
        raise mbpp.TaskCancelled()
    cases = [{'name': name} for name in ['bad', 'running', 'queued']]
    with pytest.raises(RuntimeError, match='Batch stopped'):
        mbpp.execute_cases(cases, 2, tmp_path, worker)
    text = (tmp_path/'run_status.json').read_text()
    states = json.loads(text)['tasks']
    assert states['bad']['status'] == 'failed'
    assert states['running']['status'] == 'cancelled'
    assert states['queued']['status'] == 'not_started'
    assert 'secret' not in text
    assert json.loads((tmp_path/'rows.jsonl').read_text()) == {'method':'clean'}


def test_invalid_worker_count_rejected_before_start(tmp_path):
    with pytest.raises(ValueError, match='positive'):
        mbpp.execute_cases([], 0, tmp_path, lambda *a: pytest.fail('must not run'))


def test_parallel_tasks_have_independent_clients_and_traces(registered, monkeypatch, tmp_path):
    from threading import Barrier
    from types import SimpleNamespace
    from agentrec import llm, coding, runner
    from agentrec.provenance import Provenance
    from agentrec.world import World
    barrier = Barrier(2)
    clients = []
    class Client:
        def close(self): self.closed = True
    class Model:
        def __init__(self, **kwargs):
            self.client = Client()
            clients.append(self)
    monkeypatch.setattr(llm, 'ServerLLM', Model)
    def run(task, catalog, model):
        barrier.wait(timeout=3)
        world = World([], [])
        world.files['owner'] = task['name']
        return world, Provenance(), None, {'usage': {'calls': 7}, 'wall_s': 0}
    monkeypatch.setattr(runner, 'run_workflow', run)
    monkeypatch.setattr(coding, 'check_coding', lambda world, task: {'success': True})
    manifest = json.loads(MANIFEST.read_text())
    args = SimpleNamespace(model='test', temperature=0, llm_seed=0, max_tokens=100,
                           request_timeout=1, phase='clean')
    mbpp.execute_cases(manifest['cases'][:2], 2, tmp_path,
        lambda case, out, stop: mbpp.run_case(case, args, {'endpoint':'unused','key':None}, out, stop))
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert all(c.client.closed for c in clients)
    for case in manifest['cases'][:2]:
        trace = tmp_path/'tasks'/case['name']/'traces'/f"{case['name']}_clean.json"
        assert json.loads(trace.read_text())['world']['files']['owner'] == case['name']


@pytest.mark.parametrize('kind,target', [
    ('drop_requirement', 'spec'), ('drop_requirement', 'spec:2'),
    ('requirement_hijack', 'spec'), ('requirement_hijack', 'spec:2'),
    ('false_test_report', 'tester'), ('false_test_report', 'tester:2'),
    ('stale_report', 'tester:2'),
])
def test_new_fault_workflow_trace_and_restart(registered, monkeypatch, kind, target):
    from agentrec.llm import Usage
    from agentrec.runner import run_workflow
    from agentrec.recovery import full_restart
    case = PROGRAMS['mbpp_4']
    def evaluate(source, program, split):
        passed = source == case['reference']
        return {'passed': passed, 'n': 1, 'n_passed': int(passed),
                'failures': [] if passed else [{'test_index': 0, 'error': 'AssertionError'}]}
    monkeypatch.setattr(mbpp, 'evaluate', evaluate)
    class Answers:
        def __init__(self, faulty_draft=False):
            self.usage = Usage()
            self.coders = 0
            self.faulty_draft = faulty_draft
        def chat(self, agent, messages):
            self.usage.calls += 1
            if agent == 'spec': return {'contract': case['request']}
            if agent == 'coder':
                self.coders += 1
                bad = self.faulty_draft and (self.coders == 1 or kind == 'false_test_report')
                return {'content': broken('mbpp_4') if bad else case['reference']}
            if agent == 'docs': return {'content': 'mbpp_4'}
            return {'decision': 'approve', 'note': ''}
    task, _, catalog = make_coding_task('mbpp_4')
    world, prov, injector, _ = run_workflow(task, catalog,
        Answers(kind in {'stale_report', 'false_test_report'}), FaultSpec(kind, target, False))
    assert injector.fired == 1
    nodes = [prov.nodes[n] for n in prov.faulty_nodes()]
    assert len(nodes) == 1 and nodes[0].key == target
    assert nodes[0].data['injection']['before'] != nodes[0].data['injection']['after']
    if kind == 'false_test_report':
        reviews = [n for n in prov.of_kind('artifact') if n.key == target.replace('tester', 'reviewer')]
        assert reviews[0].data['value']['decision'] == 'approve'
    if kind == 'stale_report':
        first = next(n for n in prov.of_kind('artifact') if n.key == 'tester')
        producer = prov.producer_of(nodes[0].id)
        assert (first.id, producer, 'input') in prov.edges
    recovered = full_restart(task, world, Answers(), prov)
    assert recovered.steps_recomputed == 9
    assert not recovered.prov.faulty_nodes()


def test_all_fault_cli_schedule_records_skips_and_unique_traces(registered, monkeypatch, tmp_path):
    from threading import Event
    from types import SimpleNamespace
    from agentrec import llm
    from agentrec.benchmark_faults import FAULT_TARGETS
    case = PROGRAMS['mbpp_4']
    class Model:
        def __init__(self, **kwargs):
            self.usage = llm.Usage()
            self.client = SimpleNamespace(close=lambda: None)
        def chat(self, agent, messages):
            self.usage.calls += 1
            if agent == 'spec': return {'contract': case['request']}
            if agent == 'coder': return {'content': case['reference']}
            if agent == 'docs': return {'content': case['name']}
            return {'decision': 'approve', 'note': ''}
    monkeypatch.setattr(llm, 'ServerLLM', Model)
    def evaluate(source, program, split):
        passed = source == case['reference']
        return {'passed': passed, 'n': 1, 'n_passed': int(passed),
                'failures': [] if passed else [{'error': 'AssertionError'}]}
    monkeypatch.setattr(mbpp, 'evaluate', evaluate)
    args = SimpleNamespace(model='fake', temperature=0, llm_seed=0, max_tokens=100,
        request_timeout=1, phase='paired', fault_types=list(FAULT_TARGETS),
        fault_stages=['coder', 'coder:2'], methods=[])
    mbpp.run_case(case, args, {'endpoint': 'unused', 'key': None}, tmp_path, Event())
    rows = [json.loads(line) for line in (tmp_path/'rows.jsonl').read_text().splitlines()]
    expected = 1 + sum(len(v) for v in FAULT_TARGETS.values())
    assert len(rows) == expected
    assert len(list((tmp_path/'traces').glob('*.json'))) == expected
    assert len({r['fault'] for r in rows}) == expected
    for row in rows:
        if row['fault'] and row['fault'].startswith(('false_test_report', 'stale_report')):
            assert not row['fault_fired'] and not row['fault_eligible']
            assert row['injection_events'][0]['reason']


@pytest.mark.parametrize('provider,variable,url', [
    ('nrp', 'NRP_API_KEY', 'https://ellm.nrp-nautilus.io/v1'),
    ('triton', 'TRITON_API_KEY', 'https://tritonai-api.ucsd.edu/v1'),
])
def test_provider_key_selection(provider, variable, url, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv(variable, 'selected-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-use')
    result = mbpp.resolve_connection(SimpleNamespace(provider=provider))
    assert result == {'endpoint': url, 'key': 'selected-key', 'key_env': variable}
    monkeypatch.delenv(variable)
    with pytest.raises(ValueError, match=variable):
        mbpp.resolve_connection(SimpleNamespace(provider=provider))


def test_custom_endpoint_and_file_credentials(tmp_path, monkeypatch):
    from types import SimpleNamespace
    key_file = tmp_path/'key'
    key_file.write_text('dedicated-key\n')
    args = SimpleNamespace(provider='custom', api_base_url='https://example.invalid/gateway/v1/',
                           api_key_file=str(key_file))
    assert mbpp.resolve_connection(args)['key'] == 'dedicated-key'
    assert mbpp.resolve_connection(args)['endpoint'].endswith('/gateway/v1')
    for url in ['https://secret@example.invalid/v1', 'https://example.invalid?key=secret', 'file:///tmp/key']:
        args.api_base_url = url
        with pytest.raises(ValueError, match='base URL'):
            mbpp.resolve_connection(args)
    with pytest.raises(ValueError, match='requires'):
        mbpp.resolve_connection(SimpleNamespace(provider='custom'))


def test_optional_provider_fields_and_completion_token_parameter():
    import httpx
    from agentrec.llm import ServerLLM
    model = ServerLLM(base_url='https://example.invalid/gateway/v1', model='route',
                     send_seed=False, send_temperature=False, send_json_mode=False,
                     token_limit_field='max_completion_tokens')
    model.client.close()
    def respond(request):
        assert request.url.path == '/gateway/v1/chat/completions'
        body = json.loads(request.content)
        assert body == {'messages': [{'role': 'user', 'content': 'JSON please'}],
                        'model': 'route', 'max_completion_tokens': 768}
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    model.client = httpx.Client(base_url='https://example.invalid/gateway/v1',
                               transport=httpx.MockTransport(respond))
    try:
        assert model.chat('spec', [{'role': 'user', 'content': 'JSON please'}]) == {'ok': True}
    finally:
        model.client.close()
