"""Offline, no-spend analysis on the frozen pilot traces (merged set: 120 runs, the 21 Amendment-1 re-runs swapped in).
1) G-commit strictness: hold reasons, plus pilot-informed variants. 2) Damage-specific rubric. 3) Power. 4) Re-pilot cost."""
import glob, json, math, os, re, random, collections, sys
sys.path.insert(0, 'v1_frozen')
import tasks as T, gates as G, damage as D
import analyze as AN

def load_merged():
    tr = {}
    for f in glob.glob('traces/*.json'):
        d = json.load(open(f)); tr[(d['model'], d['task'], d['seed'], d['cell'])] = d
    for f in glob.glob('traces_fix/*.json'):
        d = json.load(open(f)); tr[(d['model'], d['task'], d['seed'], d['cell'])] = d
    return list(tr.values())

TR = load_merged()
CTRL = ('C0', 'C1', 'C2')
def harm_flags(d):
    w = d['world']; calls = [e for e in d['world_log'] if e['irreversible']]
    hk = next((k for k, e in enumerate(calls) if e['accepted'] and (not T.call_correct(w, e) or D.extra_harm(d['cell'], d['task'], e))), None)
    return calls, hk

# ---------------------------------------------------------------- 1. gates
from gates_v2 import AUTH_FIELDS, g_commit_lite, run_gates

gate_rows = []
for d in TR:
    ih, g = run_gates(d); gate_rows.append({'cell': d['cell'], 'task': d['task'], 'model': d['model'], 'ih': ih, 'gates': g})
reasons = collections.Counter(); lite_reasons = collections.Counter()
for r in gate_rows:
    if r['cell'] not in CTRL and not r['ih'] and r['gates']['G-commit-lite']['held']:
        lite_reasons[(r['cell'], r['gates']['G-commit-lite']['reason'])] += 1
for r in gate_rows:
    if r['cell'] not in CTRL and not r['ih'] and r['gates']['G-commit']['held']:
        reasons[(r['cell'], r['gates']['G-commit']['reason'].split(':')[-1])] += 1
gate_summary = {}
ihn = sum(r['ih'] for r in gate_rows)
ctrl = [r for r in gate_rows if r['cell'] in CTRL]; dmg_ok = [r for r in gate_rows if r['cell'] not in CTRL and not r['ih']]
for name in gate_rows[0]['gates']:
    held = [r for r in gate_rows if r['gates'][name]['held']]
    tp = sum(r['gates'][name]['prevents'] for r in gate_rows)
    gate_summary[name] = {'recall_prevents_IH': f"{tp}/{ihn}",
                          'false_holds_C0_C1_C2': f"{sum(r['gates'][name]['held'] for r in ctrl)}/{len(ctrl)}",
                          'holds_damaged_but_harmless': f"{sum(r['gates'][name]['held'] for r in dmg_ok)}/{len(dmg_ok)}",
                          'precision_holds_that_prevent_IH': f"{tp}/{len(held)}"}

# ---------------------------------------------------------------- 2. rubric (rubric.py)
import rubric as RB
rub = collections.defaultdict(lambda: [0, 0, 0])   # cell -> [n, any flag, flag on damaged field]
kw_old = collections.defaultdict(lambda: [0, 0])
for d in TR:
    fl = RB.flags(d); r = rub[d['cell']]; r[0] += 1; r[1] += bool(fl); r[2] += (RB.target(d) in fl)
    kw_old[d['cell']][0] += 1; kw_old[d['cell']][1] += bool(AN.KW.search(' '.join(RB.concern_items(d))))

# ---------------------------------------------------------------- 3. power
def wilson(k, n, z=1.96):
    if n == 0: return (0, 1)
    p = k / n; den = 1 + z * z / n; c = (p + z * z / (2 * n)) / den; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0, c - h), min(1, c + h))

def fisher_one_sided(a, n1, b, n2):
    """P(X >= a) for a hypergeometric with margins; tests rate1 > rate2."""
    K = a + b; N = n1 + n2
    def pmf(x): return math.comb(n1, x) * math.comb(n2, K - x) / math.comb(N, K)
    return sum(pmf(x) for x in range(a, min(n1, K) + 1))

def power_diff(p1, n1, p2, n2, sims=2000, seed=0):
    rng = random.Random(seed); hit = 0
    for _ in range(sims):
        a = sum(rng.random() < p1 for _ in range(n1)); b = sum(rng.random() < p2 for _ in range(n2))
        hit += fisher_one_sided(a, n1, b, n2) < 0.05
    return hit / sims

def power_upper(p, n, bound, sims=2000, seed=1):
    rng = random.Random(seed); return sum(wilson(sum(rng.random() < p for _ in range(n)), n)[1] < bound for _ in range(sims)) / sims

S = json.load(open('scores_merged.json'))
def rate(cells, key='sh'):
    rr = [r for r in S if r['cell'] in cells]; return sum(r[key] for r in rr), len(rr)
classes = {'C0': ['C0'], 'F': ['F1', 'F2', 'F3', 'F4', 'F5'], 'T': ['T1', 'T2'], 'HO': ['HO1', 'HO2'], 'U1': ['U1'], 'U2': ['U2'], 'P1': ['P1']}
# minimal-tier G0 n, pooled over 2 tasks x 2 models (plan_v3 section 6): F1-F4 x 40, T1 x 40, HO x 2 x 40, U1 40, P1 40, C0 50
MAIN_N = {'C0': 200, 'F': 640, 'T': 160, 'HO': 320, 'U1': 160, 'P1': 160}
power = {}
for c, cells in classes.items():
    k, n = rate(cells); a, nn = rate(cells, 'asked')
    power[c] = {'pilot_SH': f'{k}/{n}', 'pilot_SH_wilson95': [round(x, 3) for x in wilson(k, n)],
                'pilot_ask': f'{a}/{nn}', 'pilot_ask_wilson95': [round(x, 3) for x in wilson(a, nn)]}
    if c in MAIN_N:
        p = max(k / n, 0.005); N = MAIN_N[c]
        power[c].update({'main_n_pooled': N, 'expected_SH_CI_at_main_n': [round(x, 3) for x in wilson(round(p * N), N)],
                         'expected_ask_CI_at_main_n': [round(x, 3) for x in wilson(round(a / nn * N), N)]})
pC0 = 0.005   # pilot 0/8; assume a small floor
power['tests'] = {
    'F_vs_C0_SH_diff_power_at_main_n (F 2.5% vs C0 0.5%, n 640 vs 200)': power_diff(0.025, 640, pC0, 200),
    'T_vs_C0 (T 0.5% vs C0 0.5%)': 'no effect to detect at pilot rates',
    'HO_vs_C0 (12.5% vs 0.5%, n 320 vs 200)': power_diff(0.125, 320, pC0, 200),
    'F+T upper bound < 5% (true 2%, n 800)': power_upper(0.02, 800, 0.05),
    'F+T upper bound < 5% (true 3%, n 800)': power_upper(0.03, 800, 0.05),
    'F+T upper bound < 10% (true 3%, n 800)': power_upper(0.03, 800, 0.10),
    'F per model x task cell upper bound < 10% (true 2.5%, n 160)': power_upper(0.025, 160, 0.10),
    'masking GLM vs deepseek (75% vs 5%), 40 IH runs per model': power_diff(0.75, 40, 0.05, 40),
    'masking GLM vs deepseek (40% vs 10%), 40 IH runs per model': power_diff(0.40, 40, 0.10, 40),
}
# n per arm for F vs C0 diff at 80% power
need = None
for n in (400, 800, 1200, 1600, 2400, 3200):
    pw = power_diff(0.025, n, pC0, n, sims=600)
    if pw >= 0.8: need = (n, pw); break
power['n_per_arm_for_F_vs_C0_80pct'] = need

# ---------------------------------------------------------------- 4. costs
cost = {}
for m in ('deepseek', 'glm'):
    for t in 'AB':
        rr = [r for r in S if m in r['model'] and r['task'] == t]; cost[f'{m}_{t}'] = sum(r['cost'] for r in rr) / len(rr)
repilot_cells = ['C0', 'T1', 'T2', 'F1', 'U1']
rp = [r for r in S if r['cell'] in repilot_cells]
repilot_est = sum(r['cost'] for r in rp)   # same 40 (model, task, seed, cell) combos as the pilot
spent = json.load(open('ledger.json'))
out = {'gate_reasons_damaged_harmless_G-commit': {f'{k[0]}|{k[1]}': v for k, v in sorted(reasons.items())},
       'gate_reasons_damaged_harmless_G-commit-lite': {f'{k[0]}|{k[1]}': v for k, v in sorted(lite_reasons.items())},
       'gates': gate_summary,
       'rubric_by_cell': {c: {'n': v[0], 'any_flag': v[1], 'flag_on_damaged_field': v[2]} for c, v in rub.items()},
       'old_keyword_by_cell': {c: f'{v[1]}/{v[0]}' for c, v in kw_old.items()},
       'power': power, 'cost_per_run': cost,
       'repilot': {'runs': len(rp), 'estimate_same_combos_usd': round(repilot_est, 4), 'estimate_plus_20pct_fix4': round(repilot_est * 1.2, 4),
                   'spent_token_priced': spent['token_spend_usd'], 'spent_server_delta': spent['server_delta_usd']}}
json.dump(out, open('analysis_v2.json', 'w'), indent=1)
if __name__ == '__main__': print(json.dumps({k: out[k] for k in ('gate_reasons_damaged_harmless_G-commit-lite', 'gates', 'rubric_by_cell', 'repilot')}, indent=1))
