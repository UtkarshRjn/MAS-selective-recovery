"""Extended Phase 1 pilot: controlled report studies and stateful travel trials."""
from __future__ import annotations
import copy
import time

from .benchmark_faults import fingerprint
from .coding import check_coding
from .faults import FaultSpec
from .programs import PROGRAMS, run_suite
from .recovery import coarse_taint


def audit_coding(task, world, prov):
    """Post-hoc private-test audit; never fed back into agent prompts."""
    start = time.perf_counter()
    versions = {}
    code_checks = {}
    invalid_tokens = 0
    invalid_artifact_tokens = 0
    for node in prov.of_kind('artifact'):
        if node.key not in {'coder', 'coder:2'}:
            continue
        value = node.data['value']
        source = value['content']
        digest = fingerprint(source)
        if digest not in versions:
            versions[digest] = run_suite(source, task['name'], split='final')
        code_checks[node.key] = versions[digest]['passed']
        if not versions[digest]['passed']:
            producer = prov.producer_of(node.id)
            usage = prov.nodes[producer].data.get('usage', {}) if producer else {}
            tokens = usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)
            invalid_artifact_tokens += tokens
            if producer and not prov.nodes[producer].data.get('controlled_fixture'):
                invalid_tokens += tokens
    values = {n.key: n.data['value'] for n in prov.of_kind('artifact')}
    false_approvals = []
    unavailable_approvals = []
    for suffix in ('', ':2'):
        report = values.get('tester' + suffix, {})
        review = values.get('reviewer' + suffix, {})
        if review.get('decision') == 'approve' and code_checks.get('coder' + suffix) is False:
            false_approvals.append('reviewer' + suffix)
        if review.get('decision') == 'approve' and report.get('available') is False:
            unavailable_approvals.append('reviewer' + suffix)
    exposed = coarse_taint(prov)
    steps = [s for s in prov.of_kind('step') if s.id in exposed]
    return {
        'code_stage_correctness': code_checks,
        'incorrect_code_stages': [key for key, passed in code_checks.items() if not passed],
        'false_approval_stages': false_approvals,
        'approval_without_feedback_stages': unavailable_approvals,
        'exposed_stages': [s.key for s in steps],
        'exposed_stage_tokens': sum(s.data.get('usage', {}).get('prompt_tokens', 0)
                                   + s.data.get('usage', {}).get('completion_tokens', 0) for s in steps),
        'invalid_code_generation_tokens': invalid_tokens,
        'invalid_code_artifact_stage_tokens': invalid_artifact_tokens,
        'verification_llm_calls': sum(v['usage']['calls'] for s in prov.of_kind('step')
            for v in (s.data.get('verification', []) if isinstance(s.data.get('verification', []), list) else [])),
        'verification_tool_calls': sum(s.data.get('verification', {}).get('tool_calls', 0)
            for s in prov.of_kind('step') if isinstance(s.data.get('verification'), dict)),
        'provenance_write_s': getattr(prov, 'write_s', 0.0),
        'audit_wall_s': time.perf_counter() - start,
    }


def record_coding(rec, base, method, task, world, prov, stats, inj=None):
    fields = dict(base)
    if inj is not None:
        fields.update(fault_fired=inj.fired > 0, injection_events=inj.events,
                      adversarial=inj.spec.adversarial if inj.spec else False)
    # Tracking time also occurs in stats for forward runs; keep one field.
    audit = audit_coding(task, world, prov)
    label = f"{task['name']}_{base['scenario']}_{method}"
    rec.trace(label, prov, world)
    rec.row(**fields, method=method, status='completed', **check_coding(world, task),
            **{k: v for k, v in stats.items() if k not in audit}, **audit)
    print(label, 'completed', flush=True)


def qualify_report_fixture(case):
    """Validate before inference; use only feedback assertions for qualification."""
    reference_report = run_suite(case['reference'], case['name'], split='feedback')
    if not reference_report['passed']:
        raise ValueError(f"{case['name']}: reference failed feedback qualification")
    for literal in ('[]', '0'):
        mutant = f"def {case['signature']}:\n    return {literal}\n"
        mutant_report = run_suite(mutant, case['name'], split='feedback')
        if not mutant_report['passed'] and mutant_report.get('failures') and all(
                f.get('error') == 'AssertionError' for f in mutant_report['failures']):
            return mutant
    raise ValueError(f"{case['name']}: no assertion-failing controlled constant fixture")



def run_conditions(case, args, llm, rec, task, catalog, clean, stop):
    from .runner import run_workflow, RECOVER
    common = {'task_id': case['task_id'], 'seed': args.llm_seed, 'dataset': 'mbpp',
              'clean_success': clean['success']}
    from .conditions import attempt, skip_recovery
    def forward(base, method, spec=None, fixture=None, verify=False):
        def execute():
            result = run_workflow(task, catalog, llm, spec, verify=verify, scenario=fixture)
            w, p, inj, stats = result
            record_coding(rec, base, method, task, w, p, stats, inj)
            return result
        return attempt(rec, {**base, 'method': method}, llm, execute)
    forward({**common, 'fault': None, 'scenario': 'verify_clean'}, 'verify', verify=True)
    schedules = [(kind, stage, {}) for kind, stages in (
        ('semantic_mutation', ('coder', 'coder:2')),
        ('tester_timeout', ('tester', 'tester:2'))) for stage in stages]
    schedules += [(kind, stage, {}) for kind, stages in (
        ('bad_patch', ('coder', 'coder:2')),
        ('drop_requirement', ('spec', 'spec:2')),
        ('requirement_hijack', ('spec', 'spec:2'))) for stage in stages]
    program = PROGRAMS[task['name']]
    # Qualify synthetic fixtures using feedback only. Final tests never choose faults.
    reference = program['reference']
    mutant = qualify_report_fixture(case)
    schedules += [
        ('false_test_report', 'tester', {'spec': task['request'], 'coder': mutant}),
        ('stale_report', 'tester:2', {'spec': task['request'], 'coder': reference, 'coder:2': mutant}),
    ]
    for kind, stage, fixture in schedules:
        if stop.is_set():
            from .mbpp import TaskCancelled
            raise TaskCancelled()
        spec = FaultSpec(kind, stage, False)
        scenario = ('controlled_' if fixture else '') + kind + '@' + stage
        base = {**common, 'fault': spec.name, 'scenario': scenario,
                'controlled_fixture': bool(fixture), 'compound_scenario': bool(fixture)}
        if fixture:
            forward(base, 'truthful_control', fixture=fixture)
        faulty = forward(base, 'none', spec, fixture)
        forward(base, 'verify', spec, fixture, verify=True)
        methods = ('restart', 'suffix', 'coarse')
        if faulty is None:
            skip_recovery(rec, base, methods)
            continue
        w, p, inj, stats = faulty
        if inj.fired:
            for method in methods:
                def recover():
                    rw, rp = copy.deepcopy(w), copy.deepcopy(p)
                    recovery = RECOVER[method](task, rw, llm, rp)
                    total = {k: stats['usage'][k] + recovery.usage[k] for k in recovery.usage}
                    record_coding(rec, {**base, 'fault_fired': True}, method, task, rw,
                                  recovery.prov, {**recovery.row(), 'total_usage': total})
                    return recovery
                attempt(rec, {**base, 'method': method, 'fault_fired': True}, llm, recover)
    if getattr(args, 'include_authorization', False) and case['task_id'] == args.stateful_task_id:
        from .authorization import run_authorization_cases
        run_authorization_cases(llm, rec, seed=args.llm_seed)
