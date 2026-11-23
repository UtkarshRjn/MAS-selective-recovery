"""L1: a 4-state Markov law for how one wrong value moves through agent handoffs.

Stage C measures single edges (compress, relay, merge). --predict fits edge matrices from
stage C only and writes law_predictions.json. Stage P runs held-out topologies (chains, a
diamond, binary trees). --score compares them with the predictions.

Offline (no key, no calls):
    python3 law_run.py --jobs-only     the grid and a cost estimate
    python3 law_run.py --dry-run       every stage with a mock model
Paid (needs approvals/LAW_L1_GO.txt and PILOT_STOP_AT):
    python3 law_run.py --stage C ; --predict ; --stage P ; --score
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

import harness as H
import law_inputs as LI
import law_model as LM

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.environ.get('LAW_TRACES', 'traces_law_l1'))
GO_FILE = os.path.join(HERE, 'approvals', 'LAW_L1_GO.txt')
PRED_FILE = os.path.join(HERE, 'law_predictions.json')
SCORE_FILE = os.path.join(HERE, 'law_l1_scores.json')
SEEDS = tuple(range(100, 112))     # fresh: earlier lines used 0-91
P_FIXES = ('F0', 'F1')             # stage P tests the plain edge and the pinned edge
TREE_B, TREE_DEPTH = 2, 3
ALPHA = 0.1                        # frozen prior: add-0.1
EST_OUT_TOKENS = 200
S0_COST_PER_CALL = 0.59070 / 592   # observed S0 mean, a cost cross-check


# ---------------------------------------------------------------- grid

def stage_c_jobs(models, seeds=SEEDS, samples=2, merge_samples=1):
    js = []
    for m in models:
        for t in LI.TEMPLATES:
            for sd in seeds:
                for e in LI.ERRORS:
                    starts = ('V', 'T') if e == 'E1' else ('V',)   # an E4 truth source lists the wrong id too
                    for f in LI.LLM_FIXES:
                        base = dict(stage='C', model=m, template=t, seed=sd, error=e, fix=f)
                        for k in range(samples):
                            js += [dict(base, edge='compress', sample=k, a=s) for s in starts]
                            js += [dict(base, edge='relay', sample=k, a=s) for s in LI.RELAY_STATES]
                        for k in range(merge_samples):
                            js += [dict(base, edge='merge', sample=k, a=a, b=b) for a, b in LI.MERGE_PAIRS]
    return js


def mixed_models(models):
    return [models[i % len(models)] for i in range(4)]


def stage_p_jobs(models, seeds=SEEDS, samples=1):
    """Held-out topologies. One job is one whole topology; n_calls counts its model calls."""
    models = list(models)
    mixed = mixed_models(models)
    js = []
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
    models = '-'.join(m.replace('api-', '') for m in (j.get('models') or [j.get('model')]))
    parts = [j['stage'], j.get('edge') or j.get('topo'), models, j['template'], str(j['seed']),
             j['error'], j['fix'], str(j['sample']), j.get('a', ''), j.get('b', '')]
    return '__'.join(p for p in parts if p != '')


# ---------------------------------------------------------------- running one job

def call(llm_for, model, edge, fix, text, seed, trace):
    """One handoff edge. The call is logged into trace['calls']."""
    out, meta = H.complete(llm_for(model), LI.messages_for(edge, fix, text), seed)
    trace.setdefault('calls', []).append({
        'model': model, 'edge': edge, 'fix': fix, 'seed': seed, 'input': text, 'output': out,
        'cost': meta.get('cost', 0), 'tin': meta.get('tin', 0), 'tout': meta.get('tout', 0),
        'finish': meta.get('finish'), 'mocked': meta.get('mocked')})
    return out


def with_pin(fix, text, pin):
    """F1 only: the harness re-attaches the pinned block after every agent."""
    return LI.attach_pin(text, pin) if fix == 'F1' else text


def edge_input(cell, j):
    """(input text, pinned block) for a stage C job."""
    if j['edge'] == 'compress':
        text = LI.compress_input(cell, j['a'], j['fix'])
        return text, LI.extract_pin(text)
    if j['edge'] == 'relay':
        text = LI.relay_input(cell, j['a'], j['fix'])
        return text, LI.extract_pin(text)
    return LI.merge_input(cell, j['a'], j['b'], j['fix'])


def run_stage_c_job(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    trace = dict(j)
    text, pin = edge_input(cell, j)
    out = with_pin(j['fix'], call(llm_for, j['model'], j['edge'], j['fix'], text, H.seed_of(job_key(j)), trace), pin)
    trace['out_state'], trace['out_raw_state'] = H.grade(cell, out)
    trace['final_text'] = out
    return trace


def _run_chain(ctx, src):
    text, states = src, []
    for i, m in enumerate(ctx['models']):
        text = ctx['hop'](m, 'compress' if i == 0 else 'relay', text, i)
        states.append(ctx['state'](text))
    return {'hop_states': states, 'sink_state': states[-1]}


def _run_diamond(ctx, src):
    ms, fix, pin = ctx['models'], ctx['fix'], ctx['pin']
    root = ctx['hop'](ms[0], 'compress', src, 0)
    t1 = ctx['hop'](ms[1], 'relay', root, 1)
    t2 = ctx['hop'](ms[2], 'relay', root, 2)
    body = 'Teammate 1:\n' + LI.strip_pin(t1).strip() + '\n\nTeammate 2:\n' + LI.strip_pin(t2).strip() + '\n'
    if fix == 'F1' and pin:
        body += '\n' + pin
    sink = ctx['hop'](ms[0], 'merge', body, 3)
    return {'hop_states': [ctx['state'](x) for x in (root, t1, t2)], 'sink_state': ctx['state'](sink)}


def _run_tree(ctx, src):
    m = ctx['models'][0]
    level = [ctx['hop'](m, 'compress', src, 'r')]
    levels = [[ctx['state'](level[0])]]
    for d in range(1, TREE_DEPTH + 1):
        level = [ctx['hop'](m, 'relay', parent, (d, pi, c))
                 for pi, parent in enumerate(level) for c in range(TREE_B)]
        levels.append([ctx['state'](x) for x in level])
    return {'level_states': levels}


TOPOLOGIES = {'chain4': _run_chain, 'chain_mixed': _run_chain, 'diamond': _run_diamond, 'tree': _run_tree}


def run_stage_p_job(j, llm_for):
    cell = LI.make_item(j['template'], j['seed'], j['error'])
    trace = dict(j)
    fix, key = j['fix'], job_key(j)
    src = LI.compress_input(cell, 'V', fix)
    pin = LI.extract_pin(src)

    def hop(model, edge, text, tag):
        tag = tag if isinstance(tag, tuple) else (tag,)
        return with_pin(fix, call(llm_for, model, edge, fix, text, H.seed_of(key, *tag), trace), pin)

    ctx = {'models': j['models'], 'fix': fix, 'pin': pin, 'hop': hop, 'state': lambda t: H.grade(cell, t)[0]}
    trace.update(TOPOLOGIES[j['topo']](ctx, src))
    return trace


def run_job(j, llm_for):
    return run_stage_c_job(j, llm_for) if j['stage'] == 'C' else run_stage_p_job(j, llm_for)


# ---------------------------------------------------------------- fitting

def _merge_table(rows, alpha):
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[tuple(sorted((r['a'], r['b']), key=LM.IDX.get))].append(r['out_state'])
    table = {}
    for pair, states in by_pair.items():
        ok = [s for s in states if s]
        if ok:
            counts = [alpha + sum(1 for s in ok if s == x) for x in LM.STATES]
            table[pair] = [c / sum(counts) for c in counts]
    return table


def _error_fraction(rows):
    ok = [r for r in rows if r['out_state']]
    return (sum(1 for r in ok if r['out_state'] in LM.ERROR_STATES) / len(ok)) if ok else None


def fit_all(rows_c, alpha=ALPHA):
    """{(model, fix, error): compress fit, relay fit, merge table, 2-state baseline}."""
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows_c:
        groups[(r['model'], r['fix'], r['error'])][r['edge']].append(r)
    out = {}
    for k, g in groups.items():
        out[k] = {
            'compress': LM.fit_matrix([(r['a'], r['out_state']) for r in g['compress']], alpha=alpha),
            'relay': LM.fit_matrix([(r['a'], r['out_state']) for r in g['relay']], alpha=alpha),
            'merge': _merge_table(g['merge'], alpha),
            # 2-state baseline: one survival number for "the wrong value is present"
            'baseline': {'p0': _error_fraction([r for r in g['compress'] if r['a'] == 'V']),
                         's': _error_fraction([r for r in g['relay'] if r['a'] in LM.ERROR_STATES])},
        }
    return out


def predict_topology(fits, topo, models, fix, error):
    """(sink distribution, merge fallback mass) for one topology."""
    get = lambda m: fits[(m, fix, error)]
    root = LM.vecmat(LM.onehot('V'), get(models[0])['compress']['P'])
    if topo in ('chain4', 'chain_mixed'):
        return LM.predict_chain(root, [get(m)['relay']['P'] for m in models[1:]]), 0.0
    if topo == 'diamond':
        out, fallback = [0.0] * 4, 0.0
        for i, s in enumerate(LM.STATES):     # branches are independent given the root state
            if root[i] == 0:
                continue
            d1 = LM.vecmat(LM.onehot(s), get(models[1])['relay']['P'])
            d2 = LM.vecmat(LM.onehot(s), get(models[2])['relay']['P'])
            merged, f = LM.predict_merge(d1, d2, get(models[0])['merge'])
            out = [o + root[i] * x for o, x in zip(out, merged)]
            fallback += root[i] * f
        return out, fallback
    raise ValueError(topo)


def _expected_harm(tree):
    return [sum(d[s] * LM.DEFAULT_ACT[s] for s in LM.STATES) for d in tree]


def _edge_record(ft):
    P = ft['relay']['P']
    rec = {'relay_P': P, 'compress_row_V': ft['compress']['P'][LM.IDX['V']], 'baseline': ft['baseline'],
           'n_relay': ft['relay']['n'], 'merge_pairs': {'%s%s' % k: v for k, v in ft['merge'].items()}}
    if all(row is not None for row in P):
        start = ft['compress']['P'][LM.IDX['V']]
        rec['R_b2'] = LM.reproduction_number(P, TREE_B)
        rec['critical_b'] = LM.critical_branching(P)
        rec['R_b2_reground'] = LM.reproduction_number(LM.reground_matrix(P), TREE_B)
        rec['tree_expected'] = LM.predict_tree(start, P, TREE_B, TREE_DEPTH)
        # R says whether errors persist; harm says whether they hurt. Reground can raise R
        # (S becomes V) and still cut harm, because executors ask on visible conflicts.
        rec['tree_expected_harm'] = _expected_harm(rec['tree_expected'])
        rec['tree_expected_harm_reground'] = _expected_harm(
            LM.predict_tree(start, LM.reground_matrix(P), TREE_B, TREE_DEPTH))
    return rec


def predictions(rows_c):
    fits = fit_all(rows_c)
    models = sorted({k[0] for k in fits})
    preds = {'law_version': 'L1', 'temperature': H.TEMPERATURE, 'tree_b': TREE_B, 'tree_depth': TREE_DEPTH,
             'edges': {'%s|%s|%s' % k: _edge_record(ft) for k, ft in sorted(fits.items())}, 'topologies': {}}
    mixed = mixed_models(models) if models else []
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
                        p, fallback = predict_topology(fits, topo, ms, f, e)
                    except (KeyError, ValueError):
                        continue
                    preds['topologies']['%s|%s|%s' % (topo, f, e)] = p
                    preds['topologies']['%s|%s|%s|merge_fallback_mass' % (topo, f, e)] = fallback
    return preds


# ---------------------------------------------------------------- scoring

def _sink_key(r):
    if r['topo'] == 'chain4':
        return 'chain4|%s|%s|%s' % (r['models'][0], r['fix'], r['error'])
    return '%s|%s|%s' % (r['topo'], r['fix'], r['error'])


def _binary_log_loss(p, ys, eps=1e-6):
    p = min(1 - eps, max(eps, p))
    return -sum(math.log(p) if y else math.log(1 - p) for y in ys) / len(ys)


def _add_binary_baseline(rec, key, states, models, preds, p_wrong_pred):
    """The 4-state law against the 2-state s^k baseline, on 'wrong value present' (chains only)."""
    fix, err = key.split('|')[-2:]
    bases = [(preds['edges'].get('%s|%s|%s' % (m, fix, err)) or {}).get('baseline') or {} for m in models]
    if not (key.startswith('chain') and bases and all(b.get('p0') is not None and b.get('s') is not None for b in bases)):
        return
    p_base = bases[0]['p0']
    for b in bases[1:]:
        p_base *= b['s']
    ys = [s in LM.ERROR_STATES for s in states if s]
    rec['binary_logloss_law'] = _binary_log_loss(p_wrong_pred, ys)
    rec['binary_logloss_baseline'] = _binary_log_loss(p_base, ys)
    rec['p_wrong_baseline'] = p_base
    rec['law_beats_baseline'] = rec['binary_logloss_law'] < rec['binary_logloss_baseline']


def _score_sinks(rows_p, preds, out, lines):
    by, models_of = defaultdict(list), {}
    for r in rows_p:
        if r['topo'] != 'tree':
            by[_sink_key(r)].append(r.get('sink_state'))
            models_of[_sink_key(r)] = r['models']
    for key, states in sorted(by.items()):
        pred, obs = preds['topologies'].get(key), LM.observed_dist(states)
        if pred is None or obs is None:
            continue
        ok = [s for s in states if s]
        p_wrong_pred = pred[LM.IDX['V']] + pred[LM.IDX['S']]
        rec = {'n': len(ok), 'pred': LM.as_dict(pred), 'obs': LM.as_dict(obs),
               'mae': sum(abs(a - b) for a, b in zip(pred, obs)) / 4, 'log_loss': LM.log_loss(pred, ok),
               'p_wrong_pred': p_wrong_pred, 'p_wrong_obs': obs[LM.IDX['V']] + obs[LM.IDX['S']],
               'p_harm_pred': LM.p_harm(pred), 'p_harm_obs': LM.p_harm(obs)}
        _add_binary_baseline(rec, key, states, models_of.get(key) or [], preds, p_wrong_pred)
        out[key] = rec
        lines.append('%-48s n=%3d  MAE=%.3f  logloss=%.3f  P(wrong) pred=%.2f obs=%.2f  P(harm) pred=%.2f obs=%.2f'
                     % (key, rec['n'], rec['mae'], rec['log_loss'] or 0, rec['p_wrong_pred'], rec['p_wrong_obs'],
                        rec['p_harm_pred'], rec['p_harm_obs']))


def _score_trees(rows_p, preds, out, lines):
    trees = defaultdict(list)
    for r in rows_p:
        if r['topo'] == 'tree':
            trees['%s|%s|%s' % (r['models'][0], r['fix'], r['error'])].append(r['level_states'])
    for key, all_levels in sorted(trees.items()):
        edge = preds['edges'].get(key) or {}
        exp = edge.get('tree_expected')
        obs_err = [sum(sum(1 for s in levels[d] if s in LM.ERROR_STATES) for levels in all_levels) / len(all_levels)
                   for d in range(TREE_DEPTH + 1)]
        pred_err = [(e['V'] + e['S']) for e in exp] if exp else None
        out['tree|' + key] = {'obs_error_nodes_by_depth': obs_err, 'pred_error_nodes_by_depth': pred_err,
                              'R_b2': edge.get('R_b2'), 'n_trees': len(all_levels)}
        lines.append('tree %-38s R=%.2f  error nodes by depth obs=%s pred=%s'
                     % (key, edge.get('R_b2') or float('nan'), ['%.2f' % x for x in obs_err],
                        ['%.2f' % x for x in pred_err] if pred_err else None))


def score(rows_p, preds):
    """Observed against predicted sink distributions, and tree growth against R."""
    out, lines = {}, []
    _score_sinks(rows_p, preds, out, lines)
    _score_trees(rows_p, preds, out, lines)
    return out, lines


# ---------------------------------------------------------------- cost

def estimate_cost(jobs, price):
    """Token estimate from the real input texts (4 characters per token) and a fixed output size."""
    by_model, calls = defaultdict(float), 0
    for j in jobs:
        cell = LI.make_item(j['template'], j['seed'], j['error'])
        if j['stage'] == 'C':
            pairs = [(j['model'], j['edge'], edge_input(cell, j)[0])]
        else:
            short = LI.relay_input(cell, 'V', j['fix'])
            pairs = [(j['models'][0], 'compress', LI.compress_input(cell, 'V', j['fix']))]
            pairs += [(j['models'][i % len(j['models'])], 'relay', short) for i in range(1, j['n_calls'])]
        for m, edge, text in pairs:
            tin = (len(LI.load_prompt(edge, j['fix'])) + len(text)) / 4 + 30
            p_in, p_out = price.get(m, (0.25, 0.75))
            by_model[m] += tin / 1e6 * p_in + EST_OUT_TOKENS / 1e6 * p_out
            calls += 1
    return dict(by_model), calls


# ---------------------------------------------------------------- commands

def cmd_jobs_only(jc, jp):
    price = {'api-deepseek-4.1-flash': (0.25, 0.75), 'api-glm-5.3-flash': (0.15, 0.5)}
    for name, jobs in (('C', jc), ('P', jp)):
        cost, calls = estimate_cost(jobs, price)
        print('stage %s jobs %d calls %d est $%.2f %s (S0-mean cross-check $%.2f)'
              % (name, len(jobs), calls, sum(cost.values()), {k: round(v, 2) for k, v in cost.items()},
                 calls * S0_COST_PER_CALL))
    print('No API calls were made. Key was not read.')
    return 0


def _input_state_mismatches(rows_c):
    """Harness check: every single-edge input must be in the state its job names."""
    bad = 0
    for r in rows_c:
        if r['edge'] != 'merge':
            cell = LI.make_item(r['template'], r['seed'], r['error'])
            bad += LI.input_state(cell, edge_input(cell, r)[0]) != r['a']
    return bad


def cmd_dry_run(jc, jp, models):
    llms = H.mocks(models)
    rc, _ = H.run_jobs(jc, lambda j: run_job(j, llms.__getitem__), job_key, OUT, write=False)
    preds = predictions(rc)
    rp, _ = H.run_jobs(jp, lambda j: run_job(j, llms.__getitem__), job_key, OUT, write=False)
    _, lines = score(rp, preds)
    bad = _input_state_mismatches(rc)
    print('dry run: stage C %d jobs, stage P %d jobs, input-state mismatches %d' % (len(rc), len(rp), bad))
    for k, e in sorted(preds['edges'].items()):
        if 'R_b2' in e:
            print('  edge %-40s R(b=2)=%.2f  critical b=%.2f  R with reground=%.2f'
                  % (k, e['R_b2'], e['critical_b'], e['R_b2_reground']))
    for ln in lines[:12]:
        print('  ' + ln)
    print('No API calls were made. Ledger was not written. Key was not read.')
    return 1 if bad else 0


def cmd_predict():
    sha = H.write_predictions(PRED_FILE, predictions(H.load_traces(OUT, 'C')))
    print('wrote %s sha256 %s' % (PRED_FILE, sha))
    return 0


def cmd_score():
    import json
    with open(PRED_FILE) as f:
        preds = json.load(f)
    out, lines = score(H.load_traces(OUT, 'P'), preds)
    print('\n'.join(lines))
    with open(SCORE_FILE, 'w') as f:
        json.dump(out, f, indent=1)
    return 0


def cmd_stage(stage, jobs, models, workers):
    if stage == 'P' and not os.path.exists(PRED_FILE):
        sys.exit('Refusing stage P: write law_predictions.json with --predict first.')
    session = H.PaidSession(GO_FILE, models)
    _, report = H.run_jobs(jobs, lambda j: run_job(j, session.llm_for), job_key, OUT,
                           workers=workers, on_done=session.progress(stage), stop_on=session.stop_on)
    session.finish(stage, report, OUT)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--jobs-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--stage', choices=('C', 'P'))
    ap.add_argument('--predict', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--models', default=','.join(H.MODELS))
    ap.add_argument('--seeds', type=int, default=len(SEEDS), help='seeds per template')
    ap.add_argument('--workers', type=int, default=12)
    args = ap.parse_args(argv)
    models = [m for m in args.models.split(',') if m]
    seeds = SEEDS[:args.seeds]
    jc, jp = stage_c_jobs(models, seeds), stage_p_jobs(models, seeds)
    if args.jobs_only:
        return cmd_jobs_only(jc, jp)
    if args.dry_run:
        return cmd_dry_run(jc, jp, models)
    if args.predict:
        return cmd_predict()
    if args.score:
        return cmd_score()
    if args.stage:
        return cmd_stage(args.stage, jc if args.stage == 'C' else jp, models, args.workers)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
