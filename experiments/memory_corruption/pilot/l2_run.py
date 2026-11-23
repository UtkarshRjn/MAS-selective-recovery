"""L2: does the loss of the true value depend on the compression ratio of a handoff?

Stage C2 measures single edges at input levels of 80, 300, 800 and 1500 words (summary budget
80 words), with the two conflicting values together (adjacent) or far apart (spread). It also
has a budget arm (40, 80, 250 word summaries of an 800-word input) and a pin arm (pin the wrong
value or the true value). Level 800 is measured but never used to fit: it is the held-out test.

--predict fits stage C2 at levels 80, 300 and 1500 only and writes l2_predictions.json:
  H1  P(V->S) = sigmoid(a + b * ln c), c = input words / 80. Claim: b > 0.
  H1t predicted P(V->S) at 800 words, checked against the observed value.
  P2  3-hop pipeline predictions from per-hop matrices chosen by each hop's level, against
      one constant matrix that ignores length (the Snowball-style baseline).
  H2  recovery from S on pure-text edges.  H3  pin truth / pin wrong / no pin.
Stage P2 runs the pipelines. --score compares them with the predictions.

Offline: python3 l2_run.py --jobs-only | --dry-run
Paid (needs approvals/LAW_L2_GO.txt and PILOT_STOP_AT): --stage C2 ; --predict ; --stage P2 ; --score
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
import l2_inputs as L2
import law_inputs as LI
import law_model as LM

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.environ.get('L2_TRACES', 'traces_law_l2'))
GO_FILE = os.path.join(HERE, 'approvals', 'LAW_L2_GO.txt')
PRED_FILE = os.path.join(HERE, 'l2_predictions.json')
SCORE_FILE = os.path.join(HERE, 'l2_scores.json')
SEEDS = tuple(range(200, 212))   # fresh: L1 used 100-111
SAMPLES = 3
FIXES = ('F0', 'F2')
PROFILES = {                     # input words at each hop of a 3-hop pipeline
    'long_then_short': (1500, 80, 80),
    'long_mid_short': (1500, 800, 80),
    'mid_mid_mid': (800, 800, 800),
    'short_long_short': (300, 1500, 300),
}
P2_SAMPLES = 2
ALPHA = 0.1
N_BOOT = 500
BOOT_SEED = 20261122


# ---------------------------------------------------------------- grid

def stage_c2_jobs(models=H.MODELS, seeds=SEEDS, samples=SAMPLES):
    js = []
    for m in models:
        for t in LI.TEMPLATES:
            for sd in seeds:
                for e in LI.ERRORS:
                    for k in range(samples):
                        base = dict(stage='C2', model=m, template=t, seed=sd, error=e, sample=k)
                        main = dict(base, arm='main', budget='b80')
                        for f in FIXES:
                            for lvl in L2.LEVELS:
                                js += [dict(main, fix=f, level=lvl, a=s, placement='adjacent') for s in LI.RELAY_STATES]
                                if lvl > 80:
                                    js.append(dict(main, fix=f, level=lvl, a='V', placement='spread'))
                        js += [dict(base, arm='budget', fix='F0', level=800, a='V', placement='adjacent', budget=b)
                               for b in ('b40', 'b250')]
                        js += [dict(base, arm=p, fix='F0', level=1500, a='V', placement='adjacent', budget='b80', pin=p)
                               for p in L2.PIN_ARMS]
    return js


def stage_p2_jobs(models=H.MODELS, seeds=SEEDS, samples=P2_SAMPLES):
    return [dict(stage='P2', model=m, template=t, seed=sd, error=e, fix=f, profile=name, levels=list(prof), sample=k)
            for m in models for t in LI.TEMPLATES for sd in seeds for e in LI.ERRORS for f in FIXES
            for name, prof in PROFILES.items() for k in range(samples)]


def job_key(j):
    if j['stage'] == 'C2':
        parts = ['C2', j['arm'], j['model'].replace('api-', ''), j['template'], str(j['seed']), j['error'],
                 j['fix'], str(j['level']), j['a'], j['placement'], j['budget'], str(j['sample'])]
    else:
        parts = ['P2', j['profile'], j['model'].replace('api-', ''), j['template'], str(j['seed']),
                 j['error'], j['fix'], str(j['sample'])]
    return '__'.join(parts)


# ---------------------------------------------------------------- running one job

def run_c2(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    text = L2.l2_input(cell, j['a'], j['level'], j['placement'], pin=j.get('pin'))
    out, meta = H.complete(llm_for(j['model']), L2.messages(j['fix'], text, j['budget']), H.seed_of(job_key(j)))
    trace = dict(j, in_words=L2.words(text), out_words=L2.words(out), output=out, cost=meta.get('cost', 0),
                 tin=meta.get('tin', 0), tout=meta.get('tout', 0))
    trace['out_state'], trace['out_raw_state'] = H.grade(cell, out)
    return trace


def run_p2(j, llm_for):
    """Hop 1 reads a padded V input. Each later hop reads the previous output, padded with
    neutral context to that hop's level (as an agent that adds its own context)."""
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    trace = dict(j, hops=[])
    prev = None
    for i, lvl in enumerate(j['levels']):
        text = L2.l2_input(cell, 'V', lvl, 'adjacent') if i == 0 else L2.pad_to(cell, prev, '', lvl)
        prev, meta = H.complete(llm_for(j['model']), L2.messages(j['fix'], text), H.seed_of(job_key(j), i))
        trace['hops'].append({'level': lvl, 'in_words': L2.words(text), 'out_words': L2.words(prev),
                              'state': H.grade(cell, prev)[0], 'cost': meta.get('cost', 0), 'output': prev})
    trace['sink_state'] = trace['hops'][-1]['state']
    return trace


def run_job(j, llm_for):
    return run_c2(j, llm_for) if j['stage'] == 'C2' else run_p2(j, llm_for)


# ---------------------------------------------------------------- H1: the logistic law

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def fit_logistic(xs, ys, l2=1e-3, iters=50):
    """MLE of P(y=1) = sigmoid(a + b x) by Newton, with a tiny ridge so separation stays finite."""
    a = b = 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = sigmoid(a + b * x)
            w = p * (1 - p)
            ga, gb = ga + (y - p), gb + (y - p) * x
            haa, hab, hbb = haa + w, hab + w * x, hbb + w * x * x
        ga, gb, haa, hbb = ga - l2 * a, gb - l2 * b, haa + l2, hbb + l2
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def h1_rows(rows_c, model, fix, error, placement, levels):
    """(ln c, lost, item) for V inputs of the main arm. lost = 1 when the output is S."""
    return [(math.log(L2.design_c(r['level'])), 1 if r['out_state'] == 'S' else 0, '%s__%d' % (r['template'], r['seed']))
            for r in rows_c
            if r['arm'] == 'main' and r['model'] == model and r['fix'] == fix and r['error'] == error
            and r['placement'] == placement and r['a'] == 'V' and r['level'] in levels and r['out_state'] is not None]


def _percentile_bounds(values):
    values = sorted(values)
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def fit_h1(rows_c, n_boot=N_BOOT, seed=BOOT_SEED):
    """Logistic fit per (model, fix, error, placement), with item-bootstrap intervals."""
    res = {}
    x800 = math.log(L2.design_c(800))
    keys = sorted({(r['model'], r['fix'], r['error'], r['placement'])
                   for r in rows_c if r['arm'] == 'main' and r['a'] == 'V'})
    for k in keys:
        data = h1_rows(rows_c, *k, levels=L2.CAL_LEVELS)
        if len(data) < 10:
            continue
        a, b = fit_logistic([d[0] for d in data], [d[1] for d in data])
        by_item = defaultdict(list)
        for d in data:
            by_item[d[2]].append(d)
        items = sorted(by_item)
        rng = random.Random(seed)
        slopes, preds = [], []
        for _ in range(n_boot):
            sample = [d for _i in items for d in by_item[items[rng.randrange(len(items))]]]
            ab = fit_logistic([d[0] for d in sample], [d[1] for d in sample])
            slopes.append(ab[1])
            preds.append(sigmoid(ab[0] + ab[1] * x800))
        b_lo, b_hi = _percentile_bounds(slopes)
        p_lo, p_hi = _percentile_bounds(preds)
        res['|'.join(k)] = {'a': a, 'b': b, 'b_lo': b_lo, 'b_hi': b_hi, 'b_excludes_0': b_lo > 0 or b_hi < 0,
                            'pred_800': sigmoid(a + b * x800), 'pred_800_lo': p_lo, 'pred_800_hi': p_hi,
                            'n': len(data)}
    return res


# ---------------------------------------------------------------- pipeline law

def _main_pairs(rows_c, model, fix, error, levels):
    return [(r['a'], r['out_state']) for r in rows_c
            if r['arm'] == 'main' and r['model'] == model and r['fix'] == fix and r['error'] == error
            and r['placement'] == 'adjacent' and r['level'] in levels]


def level_matrices(rows_c, model, fix, error, levels=L2.CAL_LEVELS):
    return {lvl: LM.fit_matrix(_main_pairs(rows_c, model, fix, error, (lvl,)), alpha=ALPHA)['P'] for lvl in levels}


def pooled_matrix(rows_c, model, fix, error):
    """The baseline: one matrix for every hop, whatever its length."""
    return LM.fit_matrix(_main_pairs(rows_c, model, fix, error, L2.CAL_LEVELS), alpha=ALPHA)['P']


def interp_matrix(mats, level):
    """Rows interpolated linearly in ln(level) between the nearest calibrated levels."""
    lv = sorted(mats)
    if level <= lv[0]:
        return mats[lv[0]]
    if level >= lv[-1]:
        return mats[lv[-1]]
    lo = max(x for x in lv if x <= level)
    hi = min(x for x in lv if x >= level)
    if lo == hi:
        return mats[lo]
    w = (math.log(level) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return [None if r0 is None or r1 is None else [(1 - w) * x + w * y for x, y in zip(r0, r1)]
            for r0, r1 in zip(mats[lo], mats[hi])]


def _pipeline_predictions(rows_c, m, f, e):
    mats, pooled, out = level_matrices(rows_c, m, f, e), pooled_matrix(rows_c, m, f, e), {}
    for name, prof in PROFILES.items():
        try:
            out['%s|%s|%s|%s' % (name, m, f, e)] = {
                'law': LM.predict_chain('V', [interp_matrix(mats, lvl) for lvl in prof]),
                'baseline': LM.predict_chain('V', [pooled] * len(prof))}
        except ValueError:
            continue
    return out


def _recovery(rows_c, m, f, e):
    """H2: how often an S input came back as V or T, per level."""
    out = {}
    for lvl in L2.LEVELS:
        states = [r['out_state'] for r in rows_c if r['arm'] == 'main' and r['model'] == m and r['fix'] == f
                  and r['error'] == e and r['a'] == 'S' and r['level'] == lvl and r['out_state']]
        if states:
            out['%s|%s|%s|%d' % (m, f, e, lvl)] = {'n': len(states),
                                                  'recovered': sum(1 for s in states if s in ('V', 'T'))}
    return out


def _pin_effects(rows_c, m, e):
    """H3: P(V->S) at 1500 words for no pin, pin wrong and pin truth."""
    row = {}
    for arm in ('main',) + L2.PIN_ARMS:
        states = [r['out_state'] for r in rows_c if r['model'] == m and r['error'] == e and r['a'] == 'V'
                  and r['level'] == 1500 and r['fix'] == 'F0' and r['placement'] == 'adjacent'
                  and r['arm'] == arm and r['out_state']]
        if states:
            row['no_pin' if arm == 'main' else arm] = {'n': len(states),
                                                       'p_V_to_S': sum(1 for s in states if s == 'S') / len(states)}
    return row


def predictions(rows_c):
    preds = {'law': 'L2', 'cal_levels': list(L2.CAL_LEVELS), 'alpha': ALPHA, 'h1': fit_h1(rows_c),
             'pipelines': {}, 'h2': {}, 'h3': {}}
    for m, f, e in sorted({(r['model'], r['fix'], r['error']) for r in rows_c if r['arm'] == 'main'}):
        preds['pipelines'].update(_pipeline_predictions(rows_c, m, f, e))
        preds['h2'].update(_recovery(rows_c, m, f, e))
    for m in sorted({r['model'] for r in rows_c}):
        for e in LI.ERRORS:
            preds['h3']['%s|%s' % (m, e)] = _pin_effects(rows_c, m, e)
    return preds


# ---------------------------------------------------------------- scoring

def _score_h1(rows_c, preds, out, lines):
    for k, h in sorted(preds['h1'].items()):
        obs = [d[1] for d in h1_rows(rows_c, *k.split('|'), levels=(800,))]
        if not obs:
            continue
        p_obs = sum(obs) / len(obs)
        inside = h['pred_800_lo'] <= p_obs <= h['pred_800_hi']
        out['h1_holdout'][k] = {'pred': h['pred_800'], 'lo': h['pred_800_lo'], 'hi': h['pred_800_hi'],
                                'obs': p_obs, 'n': len(obs), 'inside_interval': inside}
        lines.append('H1 %-46s slope b=%.2f [%.2f, %.2f]  P(V->S) at 800: pred %.2f [%.2f, %.2f] obs %.2f %s'
                     % (k, h['b'], h['b_lo'], h['b_hi'], h['pred_800'], h['pred_800_lo'], h['pred_800_hi'],
                        p_obs, 'inside' if inside else 'OUTSIDE'))


def _score_pipelines(rows_p, preds, out, lines):
    by = defaultdict(list)
    for r in rows_p:
        by['%s|%s|%s|%s' % (r['profile'], r['model'], r['fix'], r['error'])].append(r['sink_state'])
    wins = 0
    for k, states in sorted(by.items()):
        p, ok = preds['pipelines'].get(k), [s for s in states if s]
        if not p or not ok:
            continue
        ll_law, ll_base = LM.log_loss(p['law'], ok), LM.log_loss(p['baseline'], ok)
        wins += ll_law < ll_base
        obs = LM.observed_dist(ok)
        out['pipelines'][k] = {'n': len(ok), 'obs': LM.as_dict(obs), 'law': LM.as_dict(p['law']),
                               'baseline': LM.as_dict(p['baseline']), 'logloss_law': ll_law,
                               'logloss_baseline': ll_base, 'law_wins': ll_law < ll_base}
        lines.append('P2 %-50s n=%3d  P(S) obs %.2f law %.2f base %.2f  logloss law %.3f base %.3f'
                     % (k, len(ok), obs[1], p['law'][1], p['baseline'][1], ll_law, ll_base))
    out['pipeline_law_wins'] = wins
    out['pipeline_cells'] = len(out['pipelines'])
    lines.append('law beats the constant-matrix baseline in %d of %d pipeline cells' % (wins, len(out['pipelines'])))


def score(rows_c, rows_p, preds):
    out, lines = {'h1_holdout': {}, 'pipelines': {}}, []
    _score_h1(rows_c, preds, out, lines)
    _score_pipelines(rows_p, preds, out, lines)
    return out, lines


# ---------------------------------------------------------------- cost

def mean_out_tokens():
    """Mean output tokens per model, observed in L1 stage C traces."""
    acc = defaultdict(list)
    for d in H.load_traces(os.path.join(HERE, 'traces_law_l1'), 'C'):
        for c in d.get('calls') or []:
            if c.get('tout'):
                acc[c['model']].append(c['tout'])
    return {m: sum(v) / len(v) for m, v in acc.items()}


def estimate(jobs_c, jobs_p, price):
    tout = mean_out_tokens()
    prompt_chars = len(LI.load_prompt('relay', 'F2'))
    total, calls = defaultdict(float), 0
    levels = [(j['model'], j['level']) for j in jobs_c] + [(j['model'], lvl) for j in jobs_p for lvl in j['levels']]
    for m, lvl in levels:
        tin = (prompt_chars + 6 * lvl) / 4 + 30   # about 6 characters per word
        p_in, p_out = price[m]
        total[m] += tin / 1e6 * p_in + tout.get(m, 600) / 1e6 * p_out
        calls += 1
    return dict(total), calls, tout


# ---------------------------------------------------------------- commands

def cmd_jobs_only(jc, jp):
    price = {'api-deepseek-4.1-flash': (0.25, 0.75), 'api-glm-5.3-flash': (0.15, 0.5)}
    total, calls, tout = estimate(jc, jp, price)
    print('stage C2 jobs %d, stage P2 jobs %d, total calls %d' % (len(jc), len(jp), calls))
    print('mean output tokens from L1: %s' % {k: round(v) for k, v in tout.items()})
    print('estimated cost $%.2f %s' % (sum(total.values()), {k: round(v, 2) for k, v in total.items()}))
    print('No API calls were made. Key was not read.')
    return 0


def cmd_dry_run(jc, jp):
    llms = H.mocks(H.MODELS)
    rc, rep_c = H.run_jobs(jc, lambda j: run_job(j, llms.__getitem__), job_key, OUT, write=False)
    preds = predictions(rc)
    rp, rep_p = H.run_jobs(jp, lambda j: run_job(j, llms.__getitem__), job_key, OUT, write=False)
    _, lines = score(rc, rp, preds)
    print('dry run: C2 %d jobs, P2 %d jobs, failed %d' % (len(rc), len(rp), rep_c.failed + rep_p.failed))
    for ln in lines[:6] + lines[-1:]:
        print('  ' + ln)
    print('No API calls were made. Ledger was not written. Key was not read.')
    return 0


def cmd_predict():
    sha = H.write_predictions(PRED_FILE, predictions(H.load_traces(OUT, 'C2')))
    print('wrote %s sha256 %s' % (PRED_FILE, sha))
    return 0


def cmd_score():
    with open(PRED_FILE) as f:
        preds = json.load(f)
    out, lines = score(H.load_traces(OUT, 'C2'), H.load_traces(OUT, 'P2'), preds)
    print('\n'.join(lines))
    with open(SCORE_FILE, 'w') as f:
        json.dump(out, f, indent=1)
    return 0


def cmd_stage(stage, jobs, workers):
    if stage == 'P2' and not os.path.exists(PRED_FILE):
        sys.exit('Refusing stage P2: write l2_predictions.json with --predict first.')
    session = H.PaidSession(GO_FILE, H.MODELS)
    _, report = H.run_jobs(jobs, lambda j: run_job(j, session.llm_for), job_key, OUT,
                           workers=workers, on_done=session.progress(stage), stop_on=session.stop_on)
    session.finish(stage, report, OUT)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--jobs-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--stage', choices=('C2', 'P2'))
    ap.add_argument('--predict', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--seeds', type=int, default=len(SEEDS))
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args(argv)
    seeds = SEEDS[:args.seeds]
    jc, jp = stage_c2_jobs(seeds=seeds), stage_p2_jobs(seeds=seeds)
    if args.jobs_only:
        return cmd_jobs_only(jc, jp)
    if args.dry_run:
        return cmd_dry_run(jc, jp)
    if args.predict:
        return cmd_predict()
    if args.score:
        return cmd_score()
    if args.stage:
        return cmd_stage(args.stage, jc if args.stage == 'C2' else jp, args.workers)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
