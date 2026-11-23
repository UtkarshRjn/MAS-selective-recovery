"""L1 runner: calibrate edge matrices (stage C), predict held-out topologies, run them (stage P), score.

Offline commands (no key, no calls, no ledger):
    python3 law_run.py --jobs-only            print the grid and the cost estimate
    python3 law_run.py --dry-run              run every stage with a scripted mock model, then score

Paid commands need a non-empty approvals/LAW_L1_GO.txt and PILOT_STOP_AT in the environment:
    python3 law_run.py --stage C --models M1,M2,...
    python3 law_run.py --predict              writes law_predictions.json from stage C only
    python3 law_run.py --stage P --models ... refuses to run without law_predictions.json
    python3 law_run.py --score
"""
from __future__ import annotations

import argparse
import hashlib
import math
import json
import os
import sys
from collections import defaultdict

import law_inputs as LI
import law_model as LM
import s0_grade as G

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.environ.get('LAW_TRACES', 'traces_law_l1'))
GO_FILE = os.path.join(HERE, 'approvals', 'LAW_L1_GO.txt')
PRED_FILE = os.path.join(HERE, 'law_predictions.json')
DEFAULT_MODELS = ('api-deepseek-4.1-flash', 'api-glm-5.3-flash')
TEMPERATURE = 0.7          # above 0 so a chain is not one model's fixed point
MAX_TOKENS = 4000
SEEDS = tuple(range(100, 112))          # fresh: earlier lines used 0-91
P_FIXES = ('F0', 'F1')                 # stage P tests the plain edge and the pinned edge
TREE_B, TREE_DEPTH = 2, 3
EST_OUT_TOKENS = 200
S0_COST_PER_CALL = 0.59070 / 592       # observed S0 mean, used as a cross-check


# ---------------------------------------------------------------- grid

def stage_c_jobs(models, seeds=SEEDS, samples=2, merge_samples=1):
    js = []
    for m in models:
        for t in LI.TEMPLATES:
            for sd in seeds:
                for e in LI.ERRORS:
                    starts = ('V', 'T') if e == 'E1' else ('V',)   # E4 truth source lists the wrong id too
                    for f in LI.LLM_FIXES:
                        for k in range(samples):
                            for st in starts:
                                js.append(dict(stage='C', edge='compress', model=m, template=t, seed=sd,
                                               error=e, fix=f, sample=k, a=st))
                            for st in LI.RELAY_STATES:
                                js.append(dict(stage='C', edge='relay', model=m, template=t, seed=sd,
                                               error=e, fix=f, sample=k, a=st))
                        for k in range(merge_samples):
                            for a, b in LI.MERGE_PAIRS:
                                js.append(dict(stage='C', edge='merge', model=m, template=t, seed=sd,
                                               error=e, fix=f, sample=k, a=a, b=b))
    return js


def stage_p_jobs(models, seeds=SEEDS, samples=1):
    """Held-out topologies. Each job is a whole topology; calls are counted in n_calls."""
    js = []
    models = list(models)
    mixed = [models[i % len(models)] for i in range(4)]
    for t in LI.TEMPLATES:
        for sd in seeds:
            for e in LI.ERRORS:
                for f in P_FIXES:
                    for k in range(samples):
                        base = dict(stage='P', template=t, seed=sd, error=e, fix=f, sample=k)
                        for m in models:
                            js.append(dict(base, topo='chain4', models=[m] * 4, n_calls=4))
                            js.append(dict(base, topo='tree', models=[m], n_calls=2 ** (TREE_DEPTH + 1) - 1))
                        if len(models) > 1:
                            js.append(dict(base, topo='chain_mixed', models=mixed, n_calls=4))
                            js.append(dict(base, topo='diamond', models=mixed[:3], n_calls=4))
    return js


def job_key(j):
    parts = [j['stage'], j.get('edge') or j.get('topo'), '-'.join(m.replace('api-', '') for m in
             (j.get('models') or [j.get('model')])), j['template'], str(j['seed']), j['error'], j['fix'],
             str(j['sample']), j.get('a', ''), j.get('b', '')]
    return '__'.join(p for p in parts if p != '')


# ---------------------------------------------------------------- one call

def seed_of(*parts):
    return int(hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:8], 16) % 1_000_000


def call(llm_for, model, edge, fix, text, seed, trace):
    msgs = LI.messages_for(edge, fix, text)
    llm = llm_for(model)
    if hasattr(llm, 'complete_s0'):   # mock: no client import, no key, no ledger
        raw, meta = llm.complete_s0(msgs, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, seed=seed)
    else:
        import s0_summarize as SS
        raw, meta = SS.complete_s0(llm, msgs, max_tokens=MAX_TOKENS,
                                   temperature=TEMPERATURE, seed=seed, json_object=False)
    out = (raw or '').strip()
    trace.setdefault('calls', []).append({'model': model, 'edge': edge, 'fix': fix, 'seed': seed,
                                          'input': text, 'output': out, 'cost': meta.get('cost', 0),
                                          'tin': meta.get('tin', 0), 'tout': meta.get('tout', 0),
                                          'finish': meta.get('finish'), 'mocked': meta.get('mocked')})
    return out


def grade(cell, text):
    g = G.code_text(text, cell['gap_true'], cell['gap_neighbor'], G.known_ids_of(cell), cell=cell)
    return LM.state_of_grade(g), g.get('state')


def _with_pin(fix, out, pin):
    return LI.attach_pin(out, pin) if fix == 'F1' else out


def run_stage_c_job(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    tr = dict(j)
    sd = seed_of(job_key(j))
    if j['edge'] == 'compress':
        text = LI.compress_input(cell, j['a'], j['fix'])
        pin = LI.extract_pin(text)
    elif j['edge'] == 'relay':
        text = LI.relay_input(cell, j['a'], j['fix'])
        pin = LI.extract_pin(text)
    else:
        text, pin = LI.merge_input(cell, j['a'], j['b'], j['fix'])
    out = _with_pin(j['fix'], call(llm_for, j['model'], j['edge'], j['fix'], text, sd, tr), pin)
    tr['out_state'], tr['out_raw_state'] = grade(cell, out)
    tr['final_text'] = out
    return tr


def run_stage_p_job(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    tr = dict(j)
    fix = j['fix']
    key = job_key(j)
    src = LI.compress_input(cell, 'V', fix)
    pin = LI.extract_pin(src)
    ms = j['models']
    if j['topo'] in ('chain4', 'chain_mixed'):
        text, states = src, []
        for i, m in enumerate(ms):
            edge = 'compress' if i == 0 else 'relay'
            text = _with_pin(fix, call(llm_for, m, edge, fix, text, seed_of(key, i), tr), pin)
            states.append(grade(cell, text)[0])
        tr['hop_states'] = states
        tr['sink_state'] = states[-1]
    elif j['topo'] == 'diamond':
        root = _with_pin(fix, call(llm_for, ms[0], 'compress', fix, src, seed_of(key, 0), tr), pin)
        t1 = _with_pin(fix, call(llm_for, ms[1], 'relay', fix, root, seed_of(key, 1), tr), pin)
        t2 = _with_pin(fix, call(llm_for, ms[2], 'relay', fix, root, seed_of(key, 2), tr), pin)
        body = ('Teammate 1:\n' + LI.strip_pin(t1).strip() + '\n\nTeammate 2:\n' + LI.strip_pin(t2).strip() + '\n')
        if fix == 'F1' and pin:
            body += '\n' + pin
        sink = _with_pin(fix, call(llm_for, ms[0], 'merge', fix, body, seed_of(key, 3), tr), pin)
        tr['hop_states'] = [grade(cell, x)[0] for x in (root, t1, t2)]
        tr['sink_state'] = grade(cell, sink)[0]
    elif j['topo'] == 'tree':
        m = ms[0]
        level = [_with_pin(fix, call(llm_for, m, 'compress', fix, src, seed_of(key, 'r'), tr), pin)]
        levels = [[grade(cell, level[0])[0]]]
        for d in range(1, TREE_DEPTH + 1):
            nxt = []
            for pi, parent in enumerate(level):
                for c in range(TREE_B):
                    nxt.append(_with_pin(fix, call(llm_for, m, 'relay', fix, parent,
                                                   seed_of(key, d, pi, c), tr), pin))
            level = nxt
            levels.append([grade(cell, x)[0] for x in level])
        tr['level_states'] = levels
    return tr


# ---------------------------------------------------------------- calibration fit

def load(stage):
    rows = []
    if not os.path.isdir(OUT):
        return rows
    for n in sorted(os.listdir(OUT)):
        if n.endswith('.json') and n.startswith(stage + '__'):
            with open(os.path.join(OUT, n)) as f:
                rows.append(json.load(f))
    return rows


def fit_all(rows_c, alpha=0.1):  # frozen prior: add-0.1 (Bijin, 21 Nov 2026)
    """{(model, fix, error): {'compress': fit, 'relay': fit, 'merge': table}}"""
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows_c:
        groups[(r['model'], r['fix'], r['error'])][r['edge']].append(r)
    out = {}
    for k, g in groups.items():
        comp = LM.fit_matrix([(r['a'], r['out_state']) for r in g['compress']], alpha=alpha)
        rel = LM.fit_matrix([(r['a'], r['out_state']) for r in g['relay']], alpha=alpha)
        mt = defaultdict(list)
        for r in g['merge']:
            mt[tuple(sorted((r['a'], r['b']), key=LM.IDX.get))].append(r['out_state'])
        table = {}
        for pair, sts in mt.items():
            ok = [s for s in sts if s]
            if ok:
                cnt = [alpha + sum(1 for s in ok if s == x) for x in LM.STATES]
                tot = sum(cnt)
                table[pair] = [c / tot for c in cnt]
        # 2-state baseline: one survival number s for "the wrong value is present"
        def frac(rows):
            ok = [r for r in rows if r['out_state']]
            return (sum(1 for r in ok if r['out_state'] in LM.ERROR_STATES) / len(ok)) if ok else None
        base = {'p0': frac([r for r in g['compress'] if r['a'] == 'V']),
                's': frac([r for r in g['relay'] if r['a'] in LM.ERROR_STATES])}
        out[k] = {'compress': comp, 'relay': rel, 'merge': table, 'baseline': base}
    return out


def predict_topology(fits, topo, models, fix, error):
    get = lambda m: fits[(m, fix, error)]
    if topo in ('chain4', 'chain_mixed'):
        start = LM.vecmat(LM.onehot('V'), get(models[0])['compress']['P'])
        return LM.predict_chain(start, [get(m)['relay']['P'] for m in models[1:]]), 0.0
    if topo == 'diamond':
        root = LM.vecmat(LM.onehot('V'), get(models[0])['compress']['P'])
        out, fb = [0.0] * 4, 0.0
        for i, s in enumerate(LM.STATES):     # branches are independent given the root state
            if root[i] == 0:
                continue
            d1 = LM.vecmat(LM.onehot(s), get(models[1])['relay']['P'])
            d2 = LM.vecmat(LM.onehot(s), get(models[2])['relay']['P'])
            m, f = LM.predict_merge(d1, d2, get(models[0])['merge'])
            out = [o + root[i] * x for o, x in zip(out, m)]
            fb += root[i] * f
        return out, fb
    raise ValueError(topo)


def predictions(rows_c):
    fits = fit_all(rows_c)
    models = sorted({k[0] for k in fits})
    preds = {'law_version': 'L1', 'temperature': TEMPERATURE, 'tree_b': TREE_B, 'tree_depth': TREE_DEPTH,
             'edges': {}, 'topologies': {}}
    for (m, f, e), ft in sorted(fits.items()):
        P = ft['relay']['P']
        ok = all(row is not None for row in P)
        rec = {'relay_P': P, 'compress_row_V': ft['compress']['P'][LM.IDX['V']],
               'baseline': ft['baseline'],
               'n_relay': ft['relay']['n'], 'merge_pairs': {'%s%s' % k: v for k, v in ft['merge'].items()}}
        if ok:
            rec['R_b2'] = LM.reproduction_number(P, TREE_B)
            rec['critical_b'] = LM.critical_branching(P)
            rec['R_b2_reground'] = LM.reproduction_number(LM.reground_matrix(P), TREE_B)
            start = ft['compress']['P'][LM.IDX['V']]
            rec['tree_expected'] = LM.predict_tree(start, P, TREE_B, TREE_DEPTH)
            # R says whether errors persist; harm says whether they hurt. Reground can raise R
            # (S becomes V) and still cut harm, because executors ask on visible conflicts.
            rec['tree_expected_harm'] = [sum(d[s] * LM.DEFAULT_ACT[s] for s in LM.STATES)
                                         for d in rec['tree_expected']]
            rg = LM.predict_tree(start, LM.reground_matrix(P), TREE_B, TREE_DEPTH)
            rec['tree_expected_harm_reground'] = [sum(d[s] * LM.DEFAULT_ACT[s] for s in LM.STATES)
                                                  for d in rg]
        preds['edges']['%s|%s|%s' % (m, f, e)] = rec
    mixed = [models[i % len(models)] for i in range(4)] if models else []
    for f in P_FIXES:
        for e in LI.ERRORS:
            for m in models:
                try:
                    preds['topologies']['chain4|%s|%s|%s' % (m, f, e)] = predict_topology(fits, 'chain4', [m] * 4, f, e)[0]
                except (KeyError, ValueError):
                    pass
            if len(models) > 1:
                for topo, ms in (('chain_mixed', mixed), ('diamond', mixed[:3])):
                    try:
                        p, fb = predict_topology(fits, topo, ms, f, e)
                        preds['topologies']['%s|%s|%s' % (topo, f, e)] = p
                        preds['topologies']['%s|%s|%s|merge_fallback_mass' % (topo, f, e)] = fb
                    except (KeyError, ValueError):
                        pass
    return preds


# ---------------------------------------------------------------- scoring

def score(rows_p, preds, fits=None):
    """Observed vs predicted sink distribution, with the two-state baseline."""
    lines = []
    by = defaultdict(list)
    chain_models = {}
    for r in rows_p:
        if r['topo'] == 'tree':
            continue
        name = r['topo'] if r['topo'] != 'chain4' else 'chain4|%s' % r['models'][0]
        key = '%s|%s|%s' % (name, r['fix'], r['error']) if r['topo'] == 'chain4' else '%s|%s|%s' % (r['topo'], r['fix'], r['error'])
        by[key].append(r.get('sink_state'))
        chain_models[key] = r['models']
    out = {}
    for key, sts in sorted(by.items()):
        pred = preds['topologies'].get(key)
        obs = LM.observed_dist(sts)
        if pred is None or obs is None:
            continue
        mae = sum(abs(a - b) for a, b in zip(pred, obs)) / 4
        ll = LM.log_loss(pred, [s for s in sts if s])
        p_wrong_pred = pred[LM.IDX['V']] + pred[LM.IDX['S']]
        p_wrong_obs = obs[LM.IDX['V']] + obs[LM.IDX['S']]
        out[key] = {'n': len([s for s in sts if s]), 'pred': LM.as_dict(pred), 'obs': LM.as_dict(obs),
                    'mae': mae, 'log_loss': ll, 'p_wrong_pred': p_wrong_pred, 'p_wrong_obs': p_wrong_obs,
                    'p_harm_pred': LM.p_harm(pred), 'p_harm_obs': LM.p_harm(obs)}
        # Binary "wrong value present" log loss: the 4-state law vs the 2-state s^k baseline.
        ms = chain_models.get(key) or []
        fix, err = key.split('|')[-2:]
        bases = [(preds['edges'].get('%s|%s|%s' % (m, fix, err)) or {}).get('baseline') or {} for m in ms]
        if key.startswith('chain') and bases and all(b.get('p0') is not None and b.get('s') is not None for b in bases):
            p_base = bases[0]['p0']
            for b in bases[1:]:
                p_base *= b['s']
            ys = [s in LM.ERROR_STATES for s in sts if s]

            def bll(p, eps=1e-6):
                p = min(1 - eps, max(eps, p))
                return -sum(math.log(p) if y else math.log(1 - p) for y in ys) / len(ys)
            out[key]['binary_logloss_law'] = bll(p_wrong_pred)
            out[key]['binary_logloss_baseline'] = bll(p_base)
            out[key]['p_wrong_baseline'] = p_base
            out[key]['law_beats_baseline'] = out[key]['binary_logloss_law'] < out[key]['binary_logloss_baseline']
        lines.append('%-48s n=%3d  MAE=%.3f  logloss=%.3f  P(wrong) pred=%.2f obs=%.2f  P(harm) pred=%.2f obs=%.2f'
                     % (key, out[key]['n'], mae, ll or 0, p_wrong_pred, p_wrong_obs,
                        out[key]['p_harm_pred'], out[key]['p_harm_obs']))
    trees = defaultdict(list)
    for r in rows_p:
        if r['topo'] == 'tree':
            trees['%s|%s|%s' % (r['models'][0], r['fix'], r['error'])].append(r['level_states'])
    for key, lv in sorted(trees.items()):
        edge = preds['edges'].get(key) or {}
        exp = edge.get('tree_expected')
        obs_err = [sum(sum(1 for s in levels[d] if s in LM.ERROR_STATES) for levels in lv) / len(lv)
                   for d in range(TREE_DEPTH + 1)]
        pred_err = [(e['V'] + e['S']) for e in exp] if exp else None
        out['tree|' + key] = {'obs_error_nodes_by_depth': obs_err, 'pred_error_nodes_by_depth': pred_err,
                              'R_b2': edge.get('R_b2'), 'n_trees': len(lv)}
        lines.append('tree %-38s R=%.2f  error nodes by depth obs=%s pred=%s'
                     % (key, edge.get('R_b2') or float('nan'), ['%.2f' % x for x in obs_err],
                        ['%.2f' % x for x in pred_err] if pred_err else None))
    return out, lines


# ---------------------------------------------------------------- cost

def estimate_cost(jobs, price):
    """Token estimate from the real input texts (4 chars per token), plus a fixed output size."""
    by_model = defaultdict(float)
    calls = 0
    for j in jobs:
        cell = LI.make_item(j['template'], j['seed'], j['error'])
        if j['stage'] == 'C':
            if j['edge'] == 'compress':
                text = LI.compress_input(cell, j['a'], j['fix'])
            elif j['edge'] == 'relay':
                text = LI.relay_input(cell, j['a'], j['fix'])
            else:
                text = LI.merge_input(cell, j['a'], j['b'], j['fix'])[0]
            pairs = [(j['model'], 'compress' if j['edge'] == 'compress' else j['edge'], text)]
        else:
            src = LI.compress_input(cell, 'V', j['fix'])
            short = LI.relay_input(cell, 'V', j['fix'])
            pairs = [(j['models'][0], 'compress', src)] + [(j['models'][i % len(j['models'])], 'relay', short)
                                                           for i in range(1, j['n_calls'])]
        for m, edge, text in pairs:
            tin = (len(LI.load_prompt(edge if edge != 'merge' else 'merge', j['fix'])) + len(text)) / 4 + 30
            pin, pout = price.get(m, (0.25, 0.75))
            by_model[m] += tin / 1e6 * pin + EST_OUT_TOKENS / 1e6 * pout
            calls += 1
    return dict(by_model), calls


# ---------------------------------------------------------------- mock model for the dry run

class MockLaw:
    """Deterministic stand-in: keeps or drops each sentence by a hash of (seed, sentence)."""

    def __init__(self, model, keep=0.75):
        self.model, self.keep = model, keep

    def complete_s0(self, messages, max_tokens=MAX_TOKENS, temperature=0.0, seed=0, json_object=False):
        text = messages[-1]['content'].split('\n\n', 1)[-1]
        text = LI.strip_pin(text)
        sents = [s.strip() for s in text.replace('\n', ' ').split('. ') if s.strip()]
        kept = [s for s in sents
                if int(hashlib.md5(('%s|%s|%s' % (self.model, seed, s)).encode()).hexdigest()[:4], 16) / 65535 < self.keep]
        out = '. '.join(kept[:8]) or 'Book the trip as requested.'
        out = out.replace('{', '(').replace('}', ')')
        return out, {'tin': 0, 'tout': 0, 'cost': 0.0, 'finish': 'stop', 'mocked': True}


# ---------------------------------------------------------------- main

def require_go():
    if not (os.path.exists(GO_FILE) and open(GO_FILE).read().strip()):
        sys.exit('Refusing a paid run: %s is missing or empty.' % GO_FILE)
    if not os.environ.get('PILOT_STOP_AT'):
        sys.exit('Refusing a paid run: set PILOT_STOP_AT to the cap Bijin gave in his own words.')


def run_jobs(jobs, llm_for, write=True, workers=1, on_done=None, stop_types=()):
    """Run jobs, resuming from traces on disk. A stop_types exception (for example a budget
    stop) halts new work; finished traces are kept and the rest are reported as not run."""
    import threading
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
            tr = run_stage_c_job(j, llm_for) if j['stage'] == 'C' else run_stage_p_job(j, llm_for)
        except stop_types as e:
            state['stopped'] = '%s: %s' % (type(e).__name__, e)
            return
        except Exception as e:  # one failed job must not end the grid; it is counted and rerun later
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--stage', choices=('C', 'P'))
    ap.add_argument('--predict', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--models', default=','.join(DEFAULT_MODELS))
    ap.add_argument('--seeds', type=int, default=len(SEEDS), help='seeds per template')
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args(argv)
    models = [m for m in args.models.split(',') if m]
    seeds = SEEDS[:args.seeds]
    jc, jp = stage_c_jobs(models, seeds), stage_p_jobs(models, seeds)

    if args.jobs_only:
        price = {'api-deepseek-4.1-flash': (0.25, 0.75), 'api-glm-5.3-flash': (0.15, 0.5)}
        cc, nc = estimate_cost(jc, price)
        cp, np_ = estimate_cost(jp, price)
        print('stage C jobs %d calls %d est $%.2f %s' % (len(jc), nc, sum(cc.values()),
                                                        {k: round(v, 2) for k, v in cc.items()}))
        print('stage P jobs %d calls %d est $%.2f %s' % (len(jp), np_, sum(cp.values()),
                                                        {k: round(v, 2) for k, v in cp.items()}))
        print('cross-check at the S0 mean $%.5f per call: C $%.2f, P $%.2f'
              % (S0_COST_PER_CALL, nc * S0_COST_PER_CALL, np_ * S0_COST_PER_CALL))
        print('No API calls were made. Key was not read.')
        return 0

    if args.dry_run:
        mocks = {m: MockLaw(m, keep=0.6 + 0.1 * i) for i, m in enumerate(models)}
        rc = run_jobs(jc, mocks.__getitem__, write=False)
        bad = [r for r in rc if r['stage'] == 'C' and r['edge'] != 'merge'
               and LI.input_state(LI.make_item(r['template'], r['seed'], r['error']),
                                  LI.compress_input(LI.make_item(r['template'], r['seed'], r['error']), r['a'], r['fix'])
                                  if r['edge'] == 'compress' else
                                  LI.relay_input(LI.make_item(r['template'], r['seed'], r['error']), r['a'], r['fix'])) != r['a']]
        preds = predictions(rc)
        rp = run_jobs(jp, mocks.__getitem__, write=False)
        _, lines = score(rp, preds)
        print('dry run: stage C %d jobs, stage P %d jobs, input-state mismatches %d' % (len(rc), len(rp), len(bad)))
        for k, e in sorted(preds['edges'].items()):
            if 'R_b2' in e:
                print('  edge %-40s R(b=2)=%.2f  critical b=%.2f  R with reground=%.2f'
                      % (k, e['R_b2'], e['critical_b'], e['R_b2_reground']))
        for ln in lines[:12]:
            print('  ' + ln)
        print('No API calls were made. Ledger was not written. Key was not read.')
        return 1 if bad else 0

    if args.predict:
        preds = predictions(load('C'))
        blob = json.dumps(preds, indent=1, sort_keys=True)
        with open(PRED_FILE, 'w') as f:
            f.write(blob)
        print('wrote %s sha256 %s' % (PRED_FILE, hashlib.sha256(blob.encode()).hexdigest()))
        return 0

    if args.score:
        with open(PRED_FILE) as f:
            preds = json.load(f)
        out, lines = score(load('P'), preds)
        print('\n'.join(lines))
        with open(os.path.join(HERE, 'law_l1_scores.json'), 'w') as f:
            json.dump(out, f, indent=1)
        return 0

    if args.stage:
        require_go()
        if args.stage == 'P' and not os.path.exists(PRED_FILE):
            sys.exit('Refusing stage P: write law_predictions.json with --predict first.')
        import threading
        import agent as A
        unknown = [m for m in models if m not in A.PRICE]
        if unknown:
            sys.exit('Refusing a paid run: no price for %s in agent.PRICE.' % unknown)
        print('hard stop (PILOT_STOP_AT) $%.2f on conservative spend' % A.STOP_AT, flush=True)
        if A._ledger.get('baseline_server_spend') is None:
            A._ledger['baseline_server_spend'] = A.server_spend()
            A.save_ledger()
        rl = A.RL(80)  # same limit as the S0 runner
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

        rows = run_jobs(jc if args.stage == 'C' else jp, llm_for, workers=args.workers,
                        on_done=on_done, stop_types=(A.Budget,))
        A.save_ledger()
        info = run_jobs.last
        spend = A.conservative_spend(A.token_spend(), A.server_delta(force=True))
        print('stage %s: %d of %d jobs done, %d failed, stopped=%s, conservative spend $%.4f, traces in %s'
              % (args.stage, info['done'], info['planned'], info['errors'], info['stopped'], spend, OUT))
        return 0
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
