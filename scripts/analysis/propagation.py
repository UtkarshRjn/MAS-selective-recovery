"""Offline analysis of saved traces. No inference or generated-code execution."""
import ast
import json
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ['mbpp-paired-50-w16-001', 'aws-nrp-gpt-oss-five-fault-pilot-w5-001',
        'aws-nrp-gpt-oss-phase1-extended-w5-002']

def returns_none(source):
    try:
        functions = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)]
    except SyntaxError:
        return False
    return bool(functions) and any(len(f.body) == 1 and isinstance(f.body[0], ast.Return)
        and (f.body[0].value is None or isinstance(f.body[0].value, ast.Constant)
             and f.body[0].value.value is None) for f in functions)

def analyze(run):
    folder = ROOT / 'results' / run
    rows = [json.loads(line) for line in (folder / 'rows.jsonl').read_text().splitlines()]
    details = []
    for r in rows:
        if r['method'] != 'none' or r.get('dataset') != 'mbpp':
            continue
        scenario = r.get('scenario', r['fault'])
        paths = list((folder / 'tasks' / f"mbpp_{r['task_id']}" / 'traces').glob('*.json'))
        names = [f"mbpp_{r['task_id']}_{scenario}_none.json", f"mbpp_{r['task_id']}_{r['fault'].replace('@', '_')}_none.json"]
        if run == 'mbpp-paired-50-w16-001':
            names.append(f"mbpp_{r['task_id']}_{r['fault'].split('@')[1]}_none.json")
        matches = [p for p in paths if p.name in names]
        assert len(matches) == 1, (run, r['task_id'], scenario, matches)
        prov = json.loads(matches[0].read_text())['provenance']
        nodes = {n['id']: n for n in prov['nodes']}
        edges = defaultdict(set)
        for e in prov['edges']:
            if e['type'] in ('input', 'output'):
                edges[e['src']].add(e['dst'])
        origins = [n['id'] for n in nodes.values() if n['data'].get('faulty')]
        seen = set(origins)
        queue = deque(origins)
        while queue:
            for dst in edges[queue.popleft()]:
                if dst not in seen:
                    seen.add(dst)
                    queue.append(dst)
        exposed = sorted({nodes[n]['key'] for n in seen if nodes[n]['kind'] == 'step'})
        values = {n['key']: n['data']['value'] for n in nodes.values() if n['kind'] == 'artifact'}
        # Only claim measured violations on downstream stages; exclude injected origin.
        observed = set(r.get('incorrect_code_stages', [])) | set(r.get('false_approval_stages', []))
        hijacked = []
        if r['fault'].startswith('requirement_hijack'):
            hijacked = [k for k in ('coder', 'coder:2') if k in exposed and returns_none(values[k]['content'])]
            observed.update(hijacked)
        downstream_bad = sorted(observed & set(exposed))
        control = next((c for c in rows if c.get('task_id') == r['task_id']
            and c.get('scenario') == scenario and c['method'] == 'truthful_control'), None)
        new_approvals = None
        matched = None
        if control:
            control_path = matches[0].with_name(matches[0].name.replace('_none.json', '_truthful_control.json'))
            cp = json.loads(control_path.read_text())['provenance']
            cv = {n['key']: n['data']['value'] for n in cp['nodes'] if n['kind'] == 'artifact'}
            keys = ['spec', 'coder'] + (['coder:2'] if 'stale' in scenario else [])
            matched = all(values[k] == cv[k] for k in keys)
            assert matched
            new_approvals = sorted(set(r['false_approval_stages']) - set(control['false_approval_stages']))
        details.append(dict(run=run, task_id=r['task_id'], scenario=scenario,
            fired=r['fault_fired'], exposed_downstream_stages=exposed,
            observed_bad_downstream_stages=downstream_bad,
            semantic_audit_available='incorrect_code_stages' in r,
            hijacked_coders=hijacked, final_failure=not r['success'],
            matched_control_artifacts=matched, new_false_approvals_vs_control=new_approvals))
    return details

if __name__ == '__main__':
    details = [d for run in RUNS for d in analyze(run)]
    groups = defaultdict(list)
    for d in details:
        groups[d['run'], d['scenario']].append(d)
    summary = []
    for (run, scenario), all_rows in groups.items():
        fired = [d for d in all_rows if d['fired']]
        summary.append(dict(run=run, scenario=scenario, attempted=len(all_rows), fired=len(fired),
            downstream_exposure_counts=sorted(set(len(d['exposed_downstream_stages']) for d in fired)),
            observed_downstream_violation_runs=sum(bool(d['observed_bad_downstream_stages']) for d in fired)
                if all(d['semantic_audit_available'] or 'requirement_hijack' in scenario for d in fired) and fired else None,
            final_failures=sum(d['final_failure'] for d in fired),
            controlled_new_false_approval_runs=sum(bool(d['new_false_approvals_vs_control']) for d in fired)
                if any(d['matched_control_artifacts'] for d in fired) else None))
    out = ROOT / 'results' / 'propagation-analysis'
    out.mkdir(exist_ok=True)
    (out / 'details.json').write_text(json.dumps(details, indent=2) + '\n')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    lines = ['# Retrospective propagation analysis', '',
        'No new inference or generated-code execution. Only no-recovery MBPP conditions are included.', '',
        'Exposure follows recorded input/output edges from the faulty artifact, excluding its producing stage. '
        'Observed violations are failing code artifacts and false approvals from saved audits, plus structural '
        'return-None adoption for requirement hijacks. They are not a complete semantic audit of every stage.', '',
        '| Run | Scenario | Fired / attempted | Exposed downstream stages per fired run | Runs with observed downstream violations | Final failures among fired |',
        '|---|---|---:|---|---|---|']
    for s in summary:
        n = s['fired']
        obs = s['observed_downstream_violation_runs']
        lines.append(f"| {s['run']} | {s['scenario']} | {n}/{s['attempted']} | {s['downstream_exposure_counts']} | {'not audited' if obs is None else str(obs)+'/'+str(n)} | {s['final_failures']}/{n} |")
    lines += ['', '## Interpretation', '',
        '- Do not pool repeat tasks across batches as independent unique examples.',
        '- A corrupted final coder is the fault source. Its final failure is not itself downstream propagation.',
        '- A tester correctly reporting failure is not an incorrect stage.',
        '- Zero observed violations means none in the checks available, not proof every stage was unaffected.',
        '- Both controlled report scenarios have identical fixed spec/code artifacts in truthful and faulty branches; all ten pairs were checked. Each produces a new false approval in the faulty branch. The reviewer decision is deterministic Python, so this measures decision corruption, not LLM persuasion.',
        '- Stale-report control and attack both have bad final code; the attack changes approval, not code correctness.',
        '- Other conditions have independently generated executions; do not label differences causal effects. No paired checkpoint or repeated-seed analysis is available.',
        '- Travel outcomes remain in the extended pilot report; there is no comparable saved stage-semantic audit for travel.', '']
    (out / 'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps(summary, indent=2))
