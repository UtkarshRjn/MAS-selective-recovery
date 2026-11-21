"""Offline manipulation check on the frozen pilot traces (v1 code)."""
import json, glob, collections, sys
sys.path.insert(0, 'v1_frozen')
import manip, heldout as H
rows = []
for d_ in ('traces', 'traces_fix'):
    for f in sorted(glob.glob(d_ + '/*.json')):
        d = json.load(open(f)); regen = H.make_any(d['task'], d['seed'], d['cell'])['mem']
        r = manip.check(d['task'], d['seed'], d['cell'], d['mem'], manip.prompts_of(d), regen)
        # did the agent also read profile / .protect (the authority source) before acting?
        log = d['world_log']; first_irr = next((i for i, e in enumerate(log) if e['irreversible']), len(log))
        r.update({'set': d_, 'model': d['model'], 'task': d['task'], 'seed': d['seed'], 'cell': d['cell'],
                  'read_profile_before_act': any(e['tool'] == 'get_user_profile' for e in log[:first_irr]),
                  'read_protect_before_act': any(e['tool'] == 'read_file' and e['args'].get('path') == '.protect' for e in log[:first_irr]),
                  'memory_404': any(e['tool'] == 'read_file' and 'MEMORY' in str(e['args'].get('path', '')).upper() for e in log)})
        rows.append(r)
json.dump(rows, open('manip_old_traces.json', 'w'), indent=1)
c = collections.Counter((r['set'], r['pass']) for r in rows); print(c)
for r in rows:
    if not r['pass']: print('FAIL', r)
T = [r for r in rows if r['set'] == 'traces' and r['cell'] in ('T1', 'T2')]
print('T runs', len(T), 'pass', sum(r['pass'] for r in T), 'profile read before act', sum(r['read_profile_before_act'] for r in T),
      'B .protect read before act', sum(r['read_protect_before_act'] for r in T if r['task'] == 'B'), '/', sum(1 for r in T if r['task'] == 'B'))
BF = [r for r in rows if r['set'] == 'traces' and r['task'] == 'B' and r['cell'].startswith('F')]
print('B F runs', len(BF), 'pass', sum(r['pass'] for r in BF), 'tried read_file MEMORY.md', sum(r['memory_404'] for r in BF))
print('all runs tried MEMORY.md read', sum(r['memory_404'] for r in rows if r['set'] == 'traces'), 'of B', sum(1 for r in rows if r['set']=='traces' and r['task']=='B'))
