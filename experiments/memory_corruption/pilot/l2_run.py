"""L2 runner: the compression-ratio law.

Stage C2 (calibration): single edges at input levels 80, 300, 800 and 1500 words, plus a budget
arm (40, 80, 250 word summaries at 800 words in) and a pin arm (pin the wrong value or the true
value at 1500 words in). Level 800 is measured but is NOT used to fit: it is the held-out test
of the interpolation.

Predictions (written before stage P2, from C2 at levels 80, 300, 1500 only):
  H1  logistic P(V->S) = sigmoid(a + b * ln c) per (model, fix, error, placement). Claim: b > 0
      with an item-bootstrap interval that excludes 0, on both models.
  H1t the predicted P(V->S) at c = 800/80 against the observed value at level 800.
  P2  pipeline sink distributions from per-hop matrices chosen by each hop's c (rows
      interpolated in ln c), against a constant-matrix baseline that ignores c (the
      Snowball-style product of one per-hop matrix).
  H2  P(S -> V or T) on pure-text edges, at every level.
  H3  pin_truth versus pin_wrong versus no pin, at 1500 words.

Offline: python3 l2_run.py --jobs-only | --dry-run
Paid:    approvals/LAW_L2_GO.txt and PILOT_STOP_AT are required, as in L1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import threading
from collections import defaultdict

import l2_inputs as L2
import law_inputs as LI
import law_model as LM
import law_run as LR

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.environ.get('L2_TRACES', 'traces_law_l2'))
GO_FILE = os.path.join(HERE, 'approvals', 'LAW_L2_GO.txt')
PRED_FILE = os.path.join(HERE, 'l2_predictions.json')
MODELS = LR.DEFAULT_MODELS
SEEDS = tuple(range(200, 212))          # fresh: L1 used 100-111
SAMPLES = 3
FIXES = ('F0', 'F2')
PROFILES = {                            # input words at each hop of a 3-hop pipeline
    'long_then_short': (1500, 80, 80),
    'long_mid_short': (1500, 800, 80),
    'mid_mid_mid': (800, 800, 800),
    'short_long_short': (300, 1500, 300),
}
P2_SAMPLES = 2
ALPHA = 0.1


# ---------------------------------------------------------------- grid

def stage_c2_jobs(models=MODELS, seeds=SEEDS, samples=SAMPLES):
    js = []
    for m in models:
        for t in LI.TEMPLATES:
            for sd in seeds:
                for e in LI.ERRORS:
                    for k in range(samples):
                        base = dict(stage='C2', model=m, template=t, seed=sd, error=e, sample=k)
                        for f in FIXES:
                            for lvl in L2.LEVELS:
                                for s in LI.RELAY_STATES:
                                    js.append(dict(base, arm='main', fix=f, level=lvl, a=s,
                                                   placement='adjacent', budget='b80'))
                                if lvl > 80:
                                    js.append(dict(base, arm='main', fix=f, level=lvl, a='V',
                                                   placement='spread', budget='b80'))
                        for b in ('b40', 'b250'):
                            js.append(dict(base, arm='budget', fix='F0', level=800, a='V',
                                           placement='adjacent', budget=b))
                        for p in L2.PIN_ARMS:
                            js.append(dict(base, arm=p, fix='F0', level=1500, a='V',
                                           placement='adjacent', budget='b80', pin=p))
    return js


def stage_p2_jobs(models=MODELS, seeds=SEEDS, samples=P2_SAMPLES):
    js = []
    for m in models:
        for t in LI.TEMPLATES:
            for sd in seeds:
                for e in LI.ERRORS:
                    for f in FIXES:
                        for name, prof in PROFILES.items():
                            for k in range(samples):
                                js.append(dict(stage='P2', model=m, template=t, seed=sd, error=e,
                                               fix=f, profile=name, levels=list(prof), sample=k))
    return js


def job_key(j):
    if j['stage'] == 'C2':
        parts = ['C2', j['arm'], j['model'].replace('api-', ''), j['template'], str(j['seed']), j['error'],
                 j['fix'], str(j['level']), j['a'], j['placement'], j['budget'], str(j['sample'])]
    else:
        parts = ['P2', j['profile'], j['model'].replace('api-', ''), j['template'], str(j['seed']),
                 j['error'], j['fix'], str(j['sample'])]
    return '__'.join(parts)


# ---------------------------------------------------------------- execution

def _call(llm, msgs, seed):
    if hasattr(llm, 'complete_s0'):
        return llm.complete_s0(msgs, max_tokens=LR.MAX_TOKENS, temperature=LR.TEMPERATURE, seed=seed)
    import s0_summarize as SS
    return SS.complete_s0(llm, msgs, max_tokens=LR.MAX_TOKENS, temperature=LR.TEMPERATURE,
                          seed=seed, json_object=False)


def run_c2(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    text = L2.l2_input(cell, j['a'], j['level'], j['placement'], pin=j.get('pin'))
    msgs = L2.messages(j['fix'], text, j['budget'])
    raw, meta = _call(llm_for(j['model']), msgs, LR.seed_of(job_key(j)))
    out = (raw or '').strip()
    tr = dict(j)
    tr['in_words'] = L2.words(text)
    tr['out_words'] = L2.words(out)
    tr['out_state'], tr['out_raw_state'] = LR.grade(cell, out)
    tr['output'] = out
    tr['cost'] = meta.get('cost', 0)
    tr['tin'], tr['tout'] = meta.get('tin', 0), meta.get('tout', 0)
    return tr


def run_p2(j, llm_for):
    """Hop 1 reads a padded V input. Each later hop reads the previous output, padded with
    neutral context to that hop's level (an agent adding its own context)."""
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    tr = dict(j)
    tr['hops'] = []
    prev = None
    for i, lvl in enumerate(j['levels']):
        if i == 0:
            text = L2.l2_input(cell, 'V', lvl, 'adjacent')
        else:
            text = L2.pad_to(cell, prev, '', lvl)
        raw, meta = _call(llm_for(j['model']), L2.messages(j['fix'], text), LR.seed_of(job_key(j), i))
        prev = (raw or '').strip()
        st = LR.grade(cell, prev)[0]
        tr['hops'].append({'level': lvl, 'in_words': L2.words(text), 'out_words': L2.words(prev),
                           'state': st, 'cost': meta.get('cost', 0), 'output': prev})
    tr['sink_state'] = tr['hops'][-1]['state']
    return tr


def run_jobs(jobs, llm_for, write=True, workers=1, on_done=None, stop_types=()):
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(OUT, exist_ok=True)
    rows, todo = [], []
    for j in jobs:
        path = os.path.join(OUT, job_key(j) + '.json')
        if write and os.path.exists(path):
            with open(path) as f:
                rows.append(json.load(f))
        else:
            todo.append((j, path))
    lock = threading.Lock()
    state = {'stopped': None, 'errors': 0}

    def one(item):
        j, path = item
        if state['stopped']:
            return
        try:
            tr = run_c2(j, llm_for) if j['stage'] == 'C2' else run_p2(j, llm_for)
        except stop_types as e:
            state['stopped'] = '%s: %s' % (type(e).__name__, e)
            return
        except Exception as e:
            with lock:
                state['errors'] += 1
            print('job failed %s: %s' % (job_key(j), type(e).__name__), flush=True)
            return
        if write:
            with open(path, 'w') as f:
                json.dump(tr, f, indent=1)
        with lock:
            rows.append(tr)
            if on_done:
                on_done(len(rows), len(jobs))

    if workers <= 1:
        for it in todo:
            one(it)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
    run_jobs.last = {'stopped': state['stopped'], 'errors': state['errors'],
                     'done': len(rows), 'planned': len(jobs)}
    return rows


# ---------------------------------------------------------------- H1 fit

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def fit_logistic(xs, ys, l2=1e-3, iters=50):
    """MLE of P(y=1) = sigmoid(a + b x) by Newton, with a tiny ridge so separation stays finite."""
    a = b = 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = sigmoid(a + b * x)
            ga += y - p
            gb += (y - p) * x
            w = p * (1 - p)
            haa += w
            hab += w * x
            hbb += w * x * x
        ga -= l2 * a
        gb -= l2 * b
        haa += l2
        hbb += l2
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a += da
        b += db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def h1_rows(rows_c, model, fix, error, placement, levels):
    """(ln c, y) for V-input main-arm edges; y = 1 when the true value was lost (out S or N
    with the wrong value kept is S; we count V->S only, N is reported separately)."""
    out = []
    for r in rows_c:
        if (r['arm'] == 'main' and r['model'] == model and r['fix'] == fix and r['error'] == error
                and r['placement'] == placement and r['a'] == 'V' and r['level'] in levels
                and r['out_state'] is not None):
            out.append((math.log(L2.design_c(r['level'])), 1 if r['out_state'] == 'S' else 0,
                        '%s__%d' % (r['template'], r['seed'])))
    return out


def fit_h1(rows_c, n_boot=500, seed=20261122):
    res = {}
    keys = sorted({(r['model'], r['fix'], r['error'], r['placement'])
                   for r in rows_c if r['arm'] == 'main' and r['a'] == 'V'})
    for k in keys:
        data = h1_rows(rows_c, *k, levels=L2.CAL_LEVELS)
        if len(data) < 10:
            continue
        a, b = fit_logistic([d[0] for d in data], [d[1] for d in data])
        by = defaultdict(list)
        for d in data:
            by[d[2]].append(d)
        items = sorted(by)
        rng = random.Random(seed)
        bs, preds = [], []
        x800 = math.log(L2.design_c(800))
        for _ in range(n_boot):
            samp = []
            for _i in items:
                samp.extend(by[items[rng.randrange(len(items))]])
            ab = fit_logistic([d[0] for d in samp], [d[1] for d in samp])
            bs.append(ab[1])
            preds.append(sigmoid(ab[0] + ab[1] * x800))
        bs.sort()
        preds.sort()
        lo, hi = int(0.025 * len(bs)), int(0.975 * len(bs)) - 1
        res['|'.join(k)] = {'a': a, 'b': b, 'b_lo': bs[lo], 'b_hi': bs[hi],
                            'b_excludes_0': bs[lo] > 0 or bs[hi] < 0,
                            'pred_800': sigmoid(a + b * x800), 'pred_800_lo': preds[lo],
                            'pred_800_hi': preds[hi], 'n': len(data)}
    return res


# ---------------------------------------------------------------- matrices by level

def level_matrices(rows_c, model, fix, error, levels=L2.CAL_LEVELS):
    out = {}
    for lvl in levels:
        pairs = [(r['a'], r['out_state']) for r in rows_c
                 if r['arm'] == 'main' and r['model'] == model and r['fix'] == fix and r['error'] == error
                 and r['placement'] == 'adjacent' and r['level'] == lvl]
        out[lvl] = LM.fit_matrix(pairs, alpha=ALPHA)['P']
    return out


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
    out = []
    for r0, r1 in zip(mats[lo], mats[hi]):
        out.append(None if r0 is None or r1 is None else [(1 - w) * x + w * y for x, y in zip(r0, r1)])
    return out


def pooled_matrix(rows_c, model, fix, error):
    pairs = [(r['a'], r['out_state']) for r in rows_c
             if r['arm'] == 'main' and r['model'] == model and r['fix'] == fix and r['error'] == error
             and r['placement'] == 'adjacent' and r['level'] in L2.CAL_LEVELS]
    return LM.fit_matrix(pairs, alpha=ALPHA)['P']


def predictions(rows_c):
    preds = {'law': 'L2', 'cal_levels': list(L2.CAL_LEVELS), 'alpha': ALPHA, 'h1': fit_h1(rows_c),
             'pipelines': {}, 'h2': {}, 'h3': {}}
    combos = sorted({(r['model'], r['fix'], r['error']) for r in rows_c if r['arm'] == 'main'})
    for m, f, e in combos:
        mats = level_matrices(rows_c, m, f, e)
        pooled = pooled_matrix(rows_c, m, f, e)
        for name, prof in PROFILES.items():
            try:
                law = LM.predict_chain('V', [interp_matrix(mats, lvl) for lvl in prof])
                base = LM.predict_chain('V', [pooled] * len(prof))
            except ValueError:
                continue
            preds['pipelines']['%s|%s|%s|%s' % (name, m, f, e)] = {'law': law, 'baseline': base}
        # H2: recovery from S on pure-text edges, every level including 800
        for lvl in L2.LEVELS:
            sts = [r['out_state'] for r in rows_c if r['arm'] == 'main' and r['model'] == m and r['fix'] == f
                   and r['error'] == e and r['a'] == 'S' and r['level'] == lvl and r['out_state']]
            if sts:
                preds['h2']['%s|%s|%s|%d' % (m, f, e, lvl)] = {
                    'n': len(sts), 'recovered': sum(1 for s in sts if s in ('V', 'T'))}
    for m in sorted({r['model'] for r in rows_c}):
        for e in LI.ERRORS:
            row = {}
            for arm in ('main',) + L2.PIN_ARMS:
                sts = [r['out_state'] for r in rows_c if r['model'] == m and r['error'] == e and r['a'] == 'V'
                       and r['level'] == 1500 and r['fix'] == 'F0' and r['placement'] == 'adjacent'
                       and r['arm'] == arm and r['out_state']]
                if sts:
                    row['no_pin' if arm == 'main' else arm] = {
                        'n': len(sts), 'p_V_to_S': sum(1 for s in sts if s == 'S') / len(sts)}
            preds['h3']['%s|%s' % (m, e)] = row
    return preds


# ---------------------------------------------------------------- scoring

def score(rows_c, rows_p, preds):
    out, lines = {'h1_holdout': {}, 'pipelines': {}}, []
    for k, h in sorted(preds['h1'].items()):
        m, f, e, pl = k.split('|')
        obs = [d[1] for d in h1_rows(rows_c, m, f, e, pl, levels=(800,))]
        if not obs:
            continue
        p_obs = sum(obs) / len(obs)
        inside = h['pred_800_lo'] <= p_obs <= h['pred_800_hi']
        out['h1_holdout'][k] = {'pred': h['pred_800'], 'lo': h['pred_800_lo'], 'hi': h['pred_800_hi'],
                                'obs': p_obs, 'n': len(obs), 'inside_interval': inside}
        lines.append('H1 %-46s slope b=%.2f [%.2f, %.2f]  P(V->S) at 800: pred %.2f [%.2f, %.2f] obs %.2f %s'
                     % (k, h['b'], h['b_lo'], h['b_hi'], h['pred_800'], h['pred_800_lo'], h['pred_800_hi'],
                        p_obs, 'inside' if inside else 'OUTSIDE'))
    by = defaultdict(list)
    for r in rows_p:
        by['%s|%s|%s|%s' % (r['profile'], r['model'], r['fix'], r['error'])].append(r['sink_state'])
    wins = 0
    for k, sts in sorted(by.items()):
        p = preds['pipelines'].get(k)
        ok = [s for s in sts if s]
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
    return out, lines


# ---------------------------------------------------------------- cost

def mean_out_tokens():
    """Observed mean output tokens per model from L1 stage C traces; 600 if none."""
    path = os.path.join(HERE, 'traces_law_l1')
    acc = defaultdict(list)
    if os.path.isdir(path):
        for n in os.listdir(path):
            if n.startswith('C__') and n.endswith('.json'):
                with open(os.path.join(path, n)) as f:
                    d = json.load(f)
                for c in d.get('calls') or []:
                    if c.get('tout'):
                        acc[c['model']].append(c['tout'])
    return {m: sum(v) / len(v) for m, v in acc.items()}


def estimate(jobs_c, jobs_p, price):
    tout = mean_out_tokens()
    total = defaultdict(float)
    calls = 0
    plen = len(LI.load_prompt('relay', 'F2'))
    for j in jobs_c:
        tin = (plen + 6 * j['level']) / 4 + 30          # about 6 characters per word
        pi, po = price[j['model']]
        total[j['model']] += tin / 1e6 * pi + tout.get(j['model'], 600) / 1e6 * po
        calls += 1
    for j in jobs_p:
        for lvl in j['levels']:
            tin = (plen + 6 * lvl) / 4 + 30
            pi, po = price[j['model']]
            total[j['model']] += tin / 1e6 * pi + tout.get(j['model'], 600) / 1e6 * po
            calls += 1
    return dict(total), calls, tout


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
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
        price = {'api-deepseek-4.1-flash': (0.25, 0.75), 'api-glm-5.3-flash': (0.15, 0.5)}
        total, calls, tout = estimate(jc, jp, price)
        print('stage C2 jobs %d, stage P2 jobs %d, total calls %d' % (len(jc), len(jp), calls))
        print('mean output tokens from L1: %s' % {k: round(v) for k, v in tout.items()})
        print('estimated cost $%.2f %s' % (sum(total.values()), {k: round(v, 2) for k, v in total.items()}))
        print('No API calls were made. Key was not read.')
        return 0

    if args.dry_run:
        mocks = {m: LR.MockLaw(m, keep=0.6 + 0.1 * i) for i, m in enumerate(MODELS)}
        rc = run_jobs(jc, mocks.__getitem__, write=False)
        preds = predictions(rc)
        rp = run_jobs(jp, mocks.__getitem__, write=False)
        _, lines = score(rc, rp, preds)
        print('dry run: C2 %d jobs, P2 %d jobs, failed %d' % (len(rc), len(rp), run_jobs.last['errors']))
        for ln in lines[:6] + lines[-1:]:
            print('  ' + ln)
        print('No API calls were made. Ledger was not written. Key was not read.')
        return 0

    if args.predict:
        preds = predictions(load('C2'))
        blob = json.dumps(preds, indent=1, sort_keys=True)
        with open(PRED_FILE, 'w') as f:
            f.write(blob)
        print('wrote %s sha256 %s' % (PRED_FILE, hashlib.sha256(blob.encode()).hexdigest()))
        return 0

    if args.score:
        with open(PRED_FILE) as f:
            preds = json.load(f)
        out, lines = score(load('C2'), load('P2'), preds)
        print('\n'.join(lines))
        with open(os.path.join(HERE, 'l2_scores.json'), 'w') as f:
            json.dump(out, f, indent=1)
        return 0

    if args.stage:
        if not (os.path.exists(GO_FILE) and open(GO_FILE).read().strip()):
            sys.exit('Refusing a paid run: %s is missing or empty.' % GO_FILE)
        if not os.environ.get('PILOT_STOP_AT'):
            sys.exit('Refusing a paid run: set PILOT_STOP_AT.')
        if args.stage == 'P2' and not os.path.exists(PRED_FILE):
            sys.exit('Refusing stage P2: write l2_predictions.json with --predict first.')
        import agent as A
        print('hard stop (PILOT_STOP_AT) $%.2f on conservative spend' % A.STOP_AT, flush=True)
        if A._ledger.get('baseline_server_spend') is None:
            A._ledger['baseline_server_spend'] = A.server_spend()
            A.save_ledger()
        rl = A.RL(80)
        llms, llock = {}, threading.Lock()

        def llm_for(m):
            with llock:
                if m not in llms:
                    llms[m] = A.LLM(m, rl)
                return llms[m]

        def on_done(k, n):
            if k % 50 == 0 or k == n:
                A.save_ledger()
                print('%s %d/%d  conservative spend $%.4f' % (
                    args.stage, k, n, A.conservative_spend(A.token_spend(), A.server_delta())), flush=True)

        run_jobs(jc if args.stage == 'C2' else jp, llm_for, workers=args.workers,
                 on_done=on_done, stop_types=(A.Budget,))
        A.save_ledger()
        info = run_jobs.last
        print('stage %s: %d of %d jobs done, %d failed, stopped=%s, conservative spend $%.4f'
              % (args.stage, info['done'], info['planned'], info['errors'], info['stopped'],
                 A.conservative_spend(A.token_spend(), A.server_delta(force=True))))
        return 0
    ap.print_help()
    return 0


def load(stage):
    rows = []
    if os.path.isdir(OUT):
        for n in sorted(os.listdir(OUT)):
            if n.startswith(stage + '__') and n.endswith('.json'):
                with open(os.path.join(OUT, n)) as f:
                    rows.append(json.load(f))
    return rows


if __name__ == '__main__':
    sys.exit(main())
