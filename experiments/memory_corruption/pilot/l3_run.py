"""L3: does a handoff summarizer keep the plan's value or the true value?

Stage C3: one summary per input. Factors: role (which value the plan names), position of the
evidence line, load (binding rules or neutral notes), and summary budget. Plus a provenance-
prompt arm (F2). Stage H3: 2-hop chains, to test whether the first hop decides.

--predict fits an additive logistic model on budgets 40 and 160 only and writes
l3_predictions.json (the budget-80 cells are the held-out test). --score runs every
pre-registered test (see plan_law_l3_draft.md, section 3).

Offline: python3 l3_run.py --jobs-only | --dry-run
Paid (needs approvals/LAW_L3_GO.txt and PILOT_STOP_AT), in this order, so that the budget-80
predictions exist before any budget-80 data:
    --stage C3 --fit-only ; --predict ; --stage C3 ; --stage H3 ; --score
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict

import harness as H
import l3_inputs as L3
import law_inputs as LI

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.environ.get('L3_TRACES', 'traces_law_l3'))
GO_FILE = os.path.join(HERE, 'approvals', 'LAW_L3_GO.txt')
PRED_FILE = os.path.join(HERE, 'l3_predictions.json')
SCORE_FILE = os.path.join(HERE, 'l3_scores.json')
SEEDS = tuple(range(300, 312))       # fresh: L2 used 200-211
SAMPLES = 2
FIT_BUDGETS = (40, 160)             # budget 80 is held out
H3_POSITIONS = ('adjacent', 'end')
N_BOOT = 300
BOOT_SEED = 20261123
OUTCOMES = ('both', 'evidence_lost', 'plan_lost', 'none')


# ---------------------------------------------------------------- grid

def stage_c3_jobs(models=H.MODELS, seeds=SEEDS, samples=SAMPLES):
    js = []
    for m in models:
        for t in LI.TEMPLATES:
            for sd in seeds:
                for k in range(samples):
                    base = dict(stage='C3', model=m, template=t, seed=sd, sample=k)
                    for r in L3.ROLES:
                        for p in L3.POSITIONS:
                            for ld in L3.LOADS:
                                js += [dict(base, prompt=b, role=r, position=p, load=ld) for b in L3.BUDGETS]
                                js.append(dict(base, prompt='F2', role=r, position=p, load=ld))
    return js


def stage_h3_jobs(models=H.MODELS, seeds=SEEDS, samples=SAMPLES):
    return [dict(stage='H3', model=m, template=t, seed=sd, sample=k, prompt=80, role=r, position=p, load=ld)
            for m in models for t in LI.TEMPLATES for sd in seeds for k in range(samples)
            for r in L3.ROLES for p in H3_POSITIONS for ld in L3.LOADS]


def job_key(j):
    return '__'.join([j['stage'], j['model'].replace('api-', ''), j['template'], str(j['seed']), str(j['prompt']),
                      j['role'], j['position'], str(j['load']), str(j['sample'])])


# ---------------------------------------------------------------- running one job

def summarize(llm_for, j, text, hop=0):
    cell = LI.make_item(j['template'], j['seed'], 'E1')
    out, meta = H.complete(llm_for(j['model']), L3.messages(j['prompt'], text), H.seed_of(job_key(j), hop))
    state = H.grade(cell, out)[0]
    return {'in_words': L3.words(text), 'out_words': L3.words(out), 'state': state,
            'outcome': L3.role_outcome(state, j['role']), 'output': out,
            'cost': meta.get('cost', 0), 'tin': meta.get('tin', 0), 'tout': meta.get('tout', 0)}


def run_job(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], 'E1')
    first = summarize(llm_for, j, L3.l3_input(cell, j['role'], j['position'], j['load']))
    trace = dict(j, hops=[first], outcome=first['outcome'])
    if j['stage'] == 'H3':
        # Hop 2 reads hop 1's summary at the top, padded back to 900 words with the same padding.
        second = summarize(llm_for, j, L3.build(cell, first['output'], j['load']), hop=1)
        trace['hops'].append(second)
        trace['outcome'] = second['outcome']
    return trace


# ---------------------------------------------------------------- the additive logistic model

FEATURES = ('intercept', 'truth_plan', 'middle', 'end', 'load24', 'ln_budget')


def features(r):
    return [1.0, 1.0 if r['role'] == 'truth_plan' else 0.0, 1.0 if r['position'] == 'middle' else 0.0,
            1.0 if r['position'] == 'end' else 0.0, 1.0 if r['load'] else 0.0, math.log(r['prompt'])]


def solve(A, b):
    """Gaussian elimination with partial pivoting; A is small and square."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda i: abs(M[i][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            raise ValueError('singular')
        for i in range(c + 1, n):
            f = M[i][c] / M[c][c]
            for k in range(c, n + 1):
                M[i][k] -= f * M[c][k]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][k] * x[k] for k in range(i + 1, n))) / M[i][i]
    return x


def fit_logistic(X, y, ridge=1e-2, iters=50):
    """Newton (IRLS) for P(y=1) = sigmoid(X w), with a small ridge so separation stays finite."""
    k = len(X[0])
    w = [0.0] * k
    for _ in range(iters):
        g = [-ridge * wi for wi in w]
        Hm = [[ridge if i == j else 0.0 for j in range(k)] for i in range(k)]
        for xi, yi in zip(X, y):
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, sum(a * b for a, b in zip(w, xi))))))
            for i in range(k):
                g[i] += (yi - p) * xi[i]
                for j in range(k):
                    Hm[i][j] += p * (1 - p) * xi[i] * xi[j]
        step = solve(Hm, g)
        w = [a + b for a, b in zip(w, step)]
        if sum(abs(s) for s in step) < 1e-8:
            break
    return w


def predict(w, x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, sum(a * b for a, b in zip(w, x))))))


def _main_rows(rows, model):
    return [r for r in rows if r['stage'] == 'C3' and r['model'] == model and r['prompt'] != 'F2'
            and r['outcome'] is not None]


def predictions(rows, n_boot=N_BOOT, seed=BOOT_SEED):
    """Fit on budgets 40 and 160; predict P(evidence lost) for every budget-80 cell, per model."""
    preds = {'law': 'L3', 'fit_budgets': list(FIT_BUDGETS), 'features': list(FEATURES), 'models': {}}
    for m in sorted({r['model'] for r in rows}):
        fit_rows = [r for r in _main_rows(rows, m) if r['prompt'] in FIT_BUDGETS]
        if len(fit_rows) < 20:
            continue
        y = [1 if r['outcome'] == 'evidence_lost' else 0 for r in fit_rows]
        w = fit_logistic([features(r) for r in fit_rows], y)
        by_item = defaultdict(list)
        for r, yi in zip(fit_rows, y):
            by_item['%s__%d' % (r['template'], r['seed'])].append((features(r), yi))
        items = sorted(by_item)
        rng = random.Random(seed)
        boots = []
        for _ in range(n_boot):
            sample = [d for _i in items for d in by_item[items[rng.randrange(len(items))]]]
            boots.append(fit_logistic([d[0] for d in sample], [d[1] for d in sample]))
        cells = {}
        for role in L3.ROLES:
            for pos in L3.POSITIONS:
                for load in L3.LOADS:
                    x = features({'role': role, 'position': pos, 'load': load, 'prompt': 80})
                    draws = sorted(predict(b, x) for b in boots)
                    cells['%s|%s|%d' % (role, pos, load)] = {
                        'pred': predict(w, x), 'lo': draws[int(0.025 * len(draws))],
                        'hi': draws[int(0.975 * len(draws)) - 1]}
        preds['models'][m] = {'weights': dict(zip(FEATURES, w)), 'budget80': cells}
    return preds


# ---------------------------------------------------------------- pre-registered tests

def rate(rows, outcome, **match):
    sel = [r for r in rows if all(r[k] == v for k, v in match.items())]
    return (sum(1 for r in sel if r['outcome'] == outcome) / len(sel) if sel else None), len(sel)


def _ordered(values, increasing):
    pairs = list(zip(values, values[1:]))
    return all((a < b) if increasing else (a > b) for a, b in pairs)


def score(rows, preds):
    out, lines = {'models': {}}, []
    for m in sorted({r['model'] for r in rows}):
        main = _main_rows(rows, m)
        res = {}
        plan = {r: rate(main, 'plan_lost', role=r)[0] for r in L3.ROLES}
        evid = {r: rate(main, 'evidence_lost', role=r)[0] for r in L3.ROLES}
        res['H1a_plan_lost_le_2pct'] = all(v is not None and v <= 0.02 for v in plan.values())
        res['H1b_evidence_loss_equal_within_10pp'] = abs(evid['wrong_plan'] - evid['truth_plan']) <= 0.10
        pos = [rate(main, 'evidence_lost', position=p)[0] for p in L3.POSITIONS]
        bud = [rate(main, 'evidence_lost', prompt=b)[0] for b in L3.BUDGETS]
        load = [rate(main, 'evidence_lost', load=ld)[0] for ld in L3.LOADS]
        res['H2_adjacent_lt_middle_lt_end'] = _ordered(pos, increasing=True)
        res['H3_40_gt_80_gt_160'] = _ordered(bud, increasing=False)
        res['H4_load24_gt_load0'] = load[1] > load[0]
        h5 = []
        for r_ in L3.ROLES:
            for ld in L3.LOADS:
                single = rate(main, 'evidence_lost', role=r_, position='adjacent', load=ld, prompt=80)[0]
                chains = [r for r in rows if r['stage'] == 'H3' and r['model'] == m and r['role'] == r_
                          and r['load'] == ld and r['hops'][0]['outcome'] == 'both' and r['outcome'] is not None]
                if chains and single is not None:
                    hop2 = sum(1 for r in chains if r['outcome'] == 'evidence_lost') / len(chains)
                    h5.append(hop2 <= single + 0.05)
        res['H5_second_hop_le_adjacent_plus_5pp'] = bool(h5) and all(h5)
        cells = (preds['models'].get(m) or {}).get('budget80') or {}
        inside = []
        for key, c in cells.items():
            role, p, ld = key.split('|')
            obs = rate(main, 'evidence_lost', role=role, position=p, load=int(ld), prompt=80)[0]
            if obs is not None:
                inside.append(c['lo'] <= obs <= c['hi'])
        res['holdout_budget80_inside_ge_75pct'] = bool(inside) and sum(inside) / len(inside) >= 0.75
        f2 = [r for r in rows if r['stage'] == 'C3' and r['model'] == m and r['prompt'] == 'F2' and r['outcome']]
        res['descriptive'] = {'plan_lost': plan, 'evidence_lost_by_role': evid, 'by_position': dict(zip(L3.POSITIONS, pos)),
                              'by_budget': dict(zip(map(str, L3.BUDGETS), bud)), 'by_load': dict(zip(map(str, L3.LOADS), load)),
                              'holdout_inside': '%d/%d' % (sum(inside), len(inside)),
                              'F2_evidence_lost': rate(f2, 'evidence_lost')[0]}
        out['models'][m] = res
        lines.append(m)
        for k, v in res.items():
            if k != 'descriptive':
                lines.append('  %-42s %s' % (k, 'PASS' if v else 'FAIL'))
        for k, v in res['descriptive'].items():
            lines.append('  %-42s %s' % (k, v))
    out['kill_rule_fired'] = not any(v['H1b_evidence_loss_equal_within_10pp'] for v in out['models'].values())
    lines.append('kill rule fired (H1b fails on every model): %s' % out['kill_rule_fired'])
    return out, lines


# ---------------------------------------------------------------- commands

def cmd_jobs_only(jc, jh):
    per_call = {'api-deepseek-4.1-flash': 0.0014, 'api-glm-5.3-flash': 0.00036}   # observed in L2
    calls = defaultdict(int)
    for j in jc:
        calls[j['model']] += 1
    for j in jh:
        calls[j['model']] += 2
    cost = {m: n * per_call.get(m, 0.0014) for m, n in calls.items()}
    print('stage C3 jobs %d, stage H3 jobs %d, calls %d' % (len(jc), len(jh), sum(calls.values())))
    print('estimated cost $%.2f %s (at L2 observed cost per call)' % (sum(cost.values()), {k: round(v, 2) for k, v in cost.items()}))
    print('No API calls were made. Key was not read.')
    return 0


def cmd_dry_run(jc, jh):
    llms = H.mocks(H.MODELS)
    rows, rep = H.run_jobs(jc + jh, lambda j: run_job(j, llms.__getitem__), job_key, OUT, write=False)
    preds = predictions(rows, n_boot=30)
    _, lines = score(rows, preds)
    print('dry run: %d jobs, failed %d' % (len(rows), rep.failed))
    for ln in lines:
        print('  ' + ln)
    print('No API calls were made. Ledger was not written. Key was not read.')
    return 0


def load_rows():
    return H.load_traces(OUT, 'C3') + H.load_traces(OUT, 'H3')


def cmd_predict():
    sha = H.write_predictions(PRED_FILE, predictions(load_rows()))
    print('wrote %s sha256 %s' % (PRED_FILE, sha))
    return 0


def cmd_score():
    with open(PRED_FILE) as f:
        preds = json.load(f)
    out, lines = score(load_rows(), preds)
    print('\n'.join(lines))
    with open(SCORE_FILE, 'w') as f:
        json.dump(out, f, indent=1)
    return 0


def cmd_stage(stage, jobs, workers):
    if (stage == 'H3' or any(j['prompt'] == 80 for j in jobs)) and not os.path.exists(PRED_FILE):
        sys.exit('Refusing: write l3_predictions.json with --predict before any budget-80 or H3 data.')
    session = H.PaidSession(GO_FILE, H.MODELS)
    _, report = H.run_jobs(jobs, lambda j: run_job(j, session.llm_for), job_key, OUT,
                           workers=workers, on_done=session.progress(stage), stop_on=session.stop_on)
    session.finish(stage, report, OUT)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--jobs-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--stage', choices=('C3', 'H3'))
    ap.add_argument('--predict', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--fit-only', action='store_true',
                    help='stage C3: only the jobs the predictions are fitted on (not budget 80)')
    ap.add_argument('--seeds', type=int, default=len(SEEDS))
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args(argv)
    seeds = SEEDS[:args.seeds]
    jc, jh = stage_c3_jobs(seeds=seeds), stage_h3_jobs(seeds=seeds)
    if args.fit_only:
        jc = [j for j in jc if j['prompt'] != 80]
    if args.jobs_only:
        return cmd_jobs_only(jc, jh)
    if args.dry_run:
        return cmd_dry_run(jc, jh)
    if args.predict:
        return cmd_predict()
    if args.score:
        return cmd_score()
    if args.stage:
        return cmd_stage(args.stage, jc if args.stage == 'C3' else jh, args.workers)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
