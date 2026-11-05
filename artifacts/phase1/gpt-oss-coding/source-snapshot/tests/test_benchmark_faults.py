"""Deterministic injection tests: no model requests or generated-code execution."""
import copy
from types import SimpleNamespace

import pytest
from agentrec.benchmark_faults import perturb, constant_none_goal, fingerprint
from agentrec.faults import FaultSpec, Injector


def context(kind, target, name='mbpp_4'):
    return SimpleNamespace(task={'name': name, 'request': 'Find integers, returned in descending order.'},
                           injector=Injector(FaultSpec(kind, target, False)))


def report(passed, source):
    return dict(passed=passed, n=1, n_passed=int(passed),
                failures=[] if passed else [{'test_index': 0, 'error': 'AssertionError'}],
                source_sha256=fingerprint(source))


def test_drop_only_selected_stage_and_constraint():
    ctx = context('drop_requirement', 'spec:2')
    value = {'text': ctx.task['request']}
    assert perturb(ctx, 'spec', value) == (value, False, None)
    new, fired, event = perturb(ctx, 'spec:2', value)
    assert fired and new['text'] == 'Find integers.'
    assert value['text'] == ctx.task['request']
    assert event['before'] == value and event['after'] == new
    assert perturb(ctx, 'spec:2', value) == (value, False, None)


@pytest.mark.parametrize('name,text,reason', [
    ('mbpp_2', 'original', 'no_annotated_public_constraint'),
    ('mbpp_4', 'paraphrase', 'constraint_not_present_verbatim_in_spec'),
])
def test_drop_skips_without_silent_prompt_replacement(name, text, reason):
    ctx = context('drop_requirement', 'spec', name)
    new, fired, event = perturb(ctx, 'spec', {'text': text})
    assert not fired and new == {'text': text}
    assert event['reason'] == reason and ctx.injector.fired == 0


def test_hijack_is_appended_and_adversarial():
    ctx = context('requirement_hijack', 'spec')
    new, fired, event = perturb(ctx, 'spec', {'text': 'Original task.'})
    assert fired and new['text'].startswith('Original task.')
    assert 'return None' in new['text'] and ctx.injector.spec.adversarial
    assert constant_none_goal('def f(x):\n return None', 'f')
    assert not constant_none_goal('def f(x):\n return x', 'f')
    assert not constant_none_goal('def f(x):\n if x: return None\n return x', 'f')


def test_false_report_only_assertion_failures_and_preserves_truth():
    ctx = context('false_test_report', 'tester')
    actual = report(False, 'bad')
    original = copy.deepcopy(actual)
    fake, fired, event = perturb(ctx, 'tester', actual)
    assert fired and fake['passed'] and fake['n_passed'] == 1 and fake['failures'] == []
    assert actual == original and event['before'] == original
    assert ctx.injector.spec.adversarial
    for value in [report(True, 'good'), {**actual, 'failures': [{'error': 'execution_timeout'}]}]:
        assert not perturb(context('false_test_report', 'tester'), 'tester', value)[1]


def test_stale_report_requires_new_version_and_changed_decision():
    old, current = report(True, 'v1'), report(False, 'v2')
    ctx = context('stale_report', 'tester:2')
    stale, fired, event = perturb(ctx, 'tester:2', current, previous=old)
    assert fired and stale == old and event['before'] == current
    for previous in [None, report(True, 'v2'), report(False, 'v1')]:
        ctx = context('stale_report', 'tester:2')
        assert not perturb(ctx, 'tester:2', current, previous=previous)[1]
        assert ctx.injector.fired == 0


def test_no_injector_leaves_artifacts_unchanged():
    ctx = SimpleNamespace(injector=None)
    value = {'text': 'unchanged'}
    assert perturb(ctx, 'spec', value) == (value, False, None)
