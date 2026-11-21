"""Offline scoring of the smoke traces. Fails the process if any cell fails the manipulation
check or a harness error appears. Smoke data is not pooled into main."""
import glob, json, os, sys, collections
import analyze as AN, rubric as RB

HERE = os.path.dirname(os.path.abspath(__file__))
TR = os.path.join(HERE, 'traces_smoke')
rows, problems = [], []
files = sorted(glob.glob(os.path.join(TR, '*.json')))
for f in files:
    d = json.load(open(f))
    name = os.path.basename(f)
    nc = bool(d.get('no_concerns'))
    cell = d['cell'] + ('/NC' if nc else '')
    rec = {'file': name, 'model': d['model'], 'task': d['task'], 'seed': d['seed'],
           'cell': d['cell'], 'no_concerns': nc, 'cell_key': cell,
           'status': d.get('status'), 'manip_pass': bool((d.get('manip') or {}).get('pass')),
           'manip': d.get('manip'), 'json_fail': bool(d.get('json_failures')),
           'transport_error': d.get('transport_error'), 'cost': d.get('cost'), 'n_calls': d.get('n_calls')}
    if d.get('status') == 'ok' and rec['manip_pass']:
        try:
            s = AN.score(d)
            rec.update({k: s[k] for k in ('ih', 'sh', 'asked', 'ts', 'mask')})
            rec['rubric'] = sorted(RB.flags(d)); rec['rubric_hit'] = RB.target(d) in RB.flags(d)
        except Exception as e:
            rec['score_error'] = str(e); problems.append(('score_error', name, str(e)))
    else:
        reasons = []
        if d.get('status') != 'ok': reasons.append('status=' + str(d.get('status')))
        if not rec['manip_pass']: reasons.append('manip_fail')
        if d.get('transport_error'): reasons.append('transport')
        problems.append(('harness_or_manip', name, ','.join(reasons) or 'unknown'))
    if rec['json_fail']:
        problems.append(('json_failures', name, str(d.get('json_failures'))))
    rows.append(rec)

expected = 76
n = len(files)
print(f'smoke traces: {n} (expected {expected})')
print(f'manipulation check pass: {sum(r["manip_pass"] for r in rows)}/{n}')
print(f'status ok: {sum(r["status"]=="ok" for r in rows)}/{n}')
by = collections.defaultdict(list)
for r in rows: by[r['cell_key']].append(r)
for k in sorted(by):
    rr = by[k]
    uns = [r for r in rr if (not r['manip_pass'] or r['status'] != 'ok')]
    print(f"  {k:8s} n={len(rr)} ok={sum(r['status']=='ok' for r in rr)} manip={sum(r['manip_pass'] for r in rr)} "
          f"IH={sum(r.get('ih') or 0 for r in rr)} SH={sum(r.get('sh') or 0 for r in rr)} "
          f"ask={sum(r.get('asked') or 0 for r in rr)} rubric_any={sum(bool(r.get('rubric')) for r in rr)}"
          + (f' FAIL {len(uns)}' if uns else ''))

c2 = [r for r in rows if r['cell'] == 'C2' and not r['no_concerns'] and r['status']=='ok']
c0 = [r for r in rows if r['cell'] == 'C0' and r['status']=='ok']  # NC C0 only in this smoke
print('C2 rubric any-flag (exploratory pre-screen only):', sum(bool(r.get('rubric')) for r in c2), '/', len(c2))

L = json.load(open(os.path.join(HERE, 'ledger.json')))
tok, srv = L.get('token_spend_usd'), L.get('server_delta_usd')
print('cumulative spend token-priced', tok, 'server delta', srv, 'conservative', max(tok or 0, srv or 0))
print('problems:', problems if problems else 'none')

out = {'n': n, 'expected': expected, 'pass': (n == expected and not problems and all(r['manip_pass'] and r['status']=='ok' for r in rows)),
       'n_manip_pass': sum(r['manip_pass'] for r in rows), 'n_ok': sum(r['status']=='ok' for r in rows),
       'problems': problems, 'rows': rows,
       'spend': {'token': tok, 'server_delta': srv, 'conservative': max(tok or 0, srv or 0)}}
json.dump(out, open(os.path.join(HERE, 'smoke_results.json'), 'w'), indent=1)
if not out['pass']:
    print('SMOKE FAIL: do not start the main run.')
    sys.exit(2)
print('SMOKE PASS')
