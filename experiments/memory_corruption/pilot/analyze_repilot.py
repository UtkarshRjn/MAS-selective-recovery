"""Offline scoring of the re-pilot traces (traces_repilot/). Drops runs that fail the manipulation check."""
import glob, json, collections
import analyze as AN, rubric as RB, gates_v2 as V
rows = []; dropped = []
for f in sorted(glob.glob('traces_repilot/*.json')):
    d = json.load(open(f))
    if not d.get('manip', {}).get('pass') or d['status'] != 'ok': dropped.append((f, d.get('manip'), d['status'])); continue
    s = AN.score(d); s['rubric'] = sorted(RB.flags(d)); s['rubric_hit'] = RB.target(d) in RB.flags(d)
    ih, g = V.run_gates(d); s['gates_v2'] = g; rows.append(s)
n_all = len(rows) + len(dropped)
print(f'manipulation check pass: {len(rows)}/{n_all}')
for c in ['C0', 'T1', 'T2', 'F1', 'U1']:
    for m in ('deepseek', 'glm'):
        rr = [r for r in rows if r['cell'] == c and m in r['model']]
        print(c, m, f"n={len(rr)} IH={sum(r['ih'] for r in rr)} SH={sum(r['sh'] for r in rr)} ask={sum(r['asked'] for r in rr)} "
              f"TS={sum(r['ts'] for r in rr)} rubric_any={sum(bool(r['rubric']) for r in rr)} mask={sum(r['mask'] for r in rr)}")
clean = [r for r in rows if r['cell'] == 'C0']
print('clean rubric firing (C0):', sum(bool(r['rubric']) for r in clean), '/', len(clean))
for m in ('deepseek', 'glm'):
    for t in 'AB':
        rr = [r for r in rows if m in r['model'] and r['task'] == t]
        if rr: print('cost', m, t, round(sum(r['cost'] for r in rr) / len(rr), 5), 'calls', round(sum(r['n_calls'] for r in rr) / len(rr), 1))
print('dropped:', dropped)
L = json.load(open('ledger.json')); print('cumulative spend token-priced', L.get('token_spend_usd'), 'server delta', L.get('server_delta_usd'))
