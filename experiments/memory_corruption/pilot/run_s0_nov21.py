"""S0 first screen runner: composition law plus channel-correlation probe.

Does not authorize spend by itself. A paid run must be launched via run_s0_cost_watch.py and requires approvals/MAIN_GO_s0.txt.
Old approvals/MAIN_GO.txt and MAIN_GO_v3.txt do not authorize this screen.
Key is loaded only from /workspace/war_room/e1/.env by agent.LLM, never printed or copied.
Push nothing to main. No frontier models.
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
import datetime
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor

import agent as A
import s0_source as S
import s0_grade as G
import s0_summarize as SM

HERE = A.HERE
OUT = os.path.join(HERE, 'traces_s0_nov21')
PRED_PATH = os.path.join(HERE, 'freeze_s0_predictions.json')
START_SPEND_PATH = os.path.join(HERE, 's0_nov21_start_spend.json')
RESULTS_TXT = os.path.join(HERE, 's0_nov21_results.txt')
RESULTS_JSON = os.path.join(HERE, 's0_nov21_results.json')
GO_PATH = os.path.join(HERE, 'approvals', 'MAIN_GO_s0.txt')

MODELS = ('api-deepseek-4.1-flash', 'api-glm-5.3-flash')
TEMPLATES = ('trip', 'trip_long')
COMPOSE_SEEDS = tuple(range(30, 42))          # 12 seeds
PROBE_TYPO_SEEDS = tuple(range(60, 80))       # 20 seeds
PROBE_CLEAN_SEEDS = tuple(range(80, 92))      # 12 seeds
DEPTHS = (1, 2, 4)
SOFT_CAP = 2.80
HARD_CAP = 3.00
CUM_STOP = 19.75
CUM_START_KNOWN = 6.14605
EXPECTED_USD = 2.40

# Channel jobs: 4 summaries per (template, seed, condition)
# ds_p1_s0, ds_p1_s1 (i.i.d.), ds_p2_s0, glm_p1_s0
CHANNEL_SPECS = (
    ('ds_p1_s0', 'api-deepseek-4.1-flash', 'p1', 0),
    ('ds_p1_s1', 'api-deepseek-4.1-flash', 'p1', 1),
    ('ds_p2_s0', 'api-deepseek-4.1-flash', 'p2', 0),
    ('glm_p1_s0', 'api-glm-5.3-flash', 'p1', 0),
)
PAIR_TYPES = (
    ('same_model_same_prompt', 'ds_p1_s0', 'ds_p1_s1'),
    ('same_model_different_prompt', 'ds_p1_s0', 'ds_p2_s0'),
    ('cross_model', 'ds_p1_s0', 'glm_p1_s0'),
    ('verbatim_vs_ds_p1', 'verbatim', 'ds_p1_s0'),
    ('verbatim_vs_glm_p1', 'verbatim', 'glm_p1_s0'),
)
# Independence law is identified only for the first three (verbatim is nearly always typo on typo runs).
LAW_PAIRS = ('same_model_same_prompt', 'same_model_different_prompt', 'cross_model')
FA_PAIRS = (
    'same_model_same_prompt', 'same_model_different_prompt', 'cross_model',
    'verbatim_vs_ds_p1', 'verbatim_vs_glm_p1',
)


def require_s0_go():
    if not (os.path.exists(GO_PATH) and os.path.getsize(GO_PATH) > 0):
        raise SystemExit(
            'Refusing to start: approvals/MAIN_GO_s0.txt is missing or empty. '
            'Older MAIN_GO files do not authorize S0. No API calls made.'
        )


def build_composition_jobs():
    js = []
    for model in MODELS:
        for tmpl in TEMPLATES:
            for seed in COMPOSE_SEEDS:
                for depth in DEPTHS:
                    js.append({
                        'part': 'composition',
                        'model': model,
                        'template': tmpl,
                        'seed': seed,
                        'depth': depth,
                        'condition': 'typo',
                        'prompt': 'p1',
                        'sample_id': 0,
                        'tag': 'C_d%d' % depth,
                        'stage': 'compose_d%d' % depth,
                    })
    return js


def build_channel_jobs():
    js = []
    for condition, seeds in (('typo', PROBE_TYPO_SEEDS), ('clean', PROBE_CLEAN_SEEDS)):
        for tmpl in TEMPLATES:
            for seed in seeds:
                for tag, model, prompt, sample_id in CHANNEL_SPECS:
                    js.append({
                        'part': 'channel',
                        'model': model,
                        'template': tmpl,
                        'seed': seed,
                        'depth': 1,
                        'condition': condition,
                        'prompt': prompt,
                        'sample_id': sample_id,
                        'channel_tag': tag,
                        'tag': tag,
                        'stage': 'channel_%s' % condition,
                    })
    return js


def build_jobs():
    return build_composition_jobs() + build_channel_jobs()


def expected_counts():
    c = len(build_composition_jobs())
    ch = len(build_channel_jobs())
    compose_calls = len(MODELS) * len(TEMPLATES) * len(COMPOSE_SEEDS) * sum(DEPTHS)
    return {
        'composition_jobs': c,          # 144
        'channel_jobs': ch,             # 256
        'total_jobs': c + ch,           # 400
        'composition_calls': compose_calls,  # 336
        'channel_calls': ch,
        'total_calls': compose_calls + ch,   # 592
    }


def path_of(j):
    short = j['model'].replace('api-', '')
    if j['part'] == 'composition':
        name = 'comp__%s__%s__%d__d%d.json' % (short, j['template'], j['seed'], j['depth'])
    else:
        name = 'chan__%s__%s__%d__%s__%s.json' % (short, j['template'], j['seed'], j['condition'], j['tag'])
    return os.path.join(OUT, name)


def stage_order():
    return (
        'compose_d1',
        'channel_typo',
        'channel_clean',
        'compose_d2',
        'compose_d4',
    )


def jobs_in_stage(js, stage):
    return [j for j in js if j.get('stage') == stage]


def _probe_seed(j):
    # Distinct API seed per channel sample; temperature 1.0 for the probe.
    return int(j['seed']) * 100 + int(j.get('sample_id') or 0) * 10 + (1 if j.get('prompt') == 'p2' else 0)


def run_one(j, llm):
    cell = S.make_annotated_cell(j['template'], j['seed'], j['condition'])
    source = S.build_source(cell)
    ok, reasons = S.source_ok(cell, source)
    rec = dict(j)
    rec['gap_slot'] = cell.get('gap_slot')
    rec['gap_true'] = cell.get('gap_true')
    rec['gap_neighbor'] = cell.get('gap_neighbor')
    rec['source'] = source
    rec['source_ok'] = ok
    rec['source_reasons'] = reasons
    rec['verbatim_value'] = S.extract_verbatim_value(source)
    rec['verbatim_channel'] = S.extract_verbatim_channel(source, cell)
    rec['world_ids'] = sorted(S.PG.known_world_ids(S.world_for_run(cell)))
    if not ok:
        rec['status'] = 'harness_error'
        rec['cost'] = 0.0
        rec['grade'] = G.code_text('', rec['gap_true'], rec['gap_neighbor'], cell=cell, source=source)
        rec['grade']['valid'] = False
        rec['grade']['invalid_reason'] = 'bad_source'
        return rec
    trace = {'part': j['part'], 'screen': 's0'}
    if j['part'] == 'composition':
        hops, trace = SM.run_hops(
            cell, source, llm, n_hops=int(j['depth']), prompt_name='p1',
            temperature=SM.COMPOSE_TEMPERATURE, seed=int(j['seed']), trace=trace,
        )
        # Primary grade is the hop at the requested depth.
        final = hops[-1] if hops else {'text': '', 'grade': G.code_text('', rec['gap_true'], rec['gap_neighbor'], cell=cell, source=source)}
        rec['hops'] = hops
        rec['grade'] = final.get('grade')
        rec['final_text'] = final.get('text')
        rec['status'] = 'ok' if rec['grade'].get('valid') else 'invalid_summary'
    else:
        hops, trace = SM.run_hops(
            cell, source, llm, n_hops=1, prompt_name=j['prompt'],
            temperature=SM.PROBE_TEMPERATURE, seed=_probe_seed(j), trace=trace,
        )
        final = hops[-1] if hops else {'text': '', 'grade': G.code_text('', rec['gap_true'], rec['gap_neighbor'], cell=cell, source=source)}
        rec['hops'] = hops
        rec['grade'] = final.get('grade')
        rec['final_text'] = final.get('text')
        rec['status'] = 'ok' if rec['grade'].get('valid') else 'invalid_summary'
    rec['cost'] = trace.get('cost') or 0.0
    rec['calls'] = trace.get('calls') or []
    rec['json_object'] = False
    rec['max_tokens'] = SM.MAX_TOKENS
    return rec


def start_spend():
    return A.conservative_spend(A.token_spend(), A.server_delta())


def lock_predictions(rows, path=PRED_PATH):
    """Write s1 estimates and s^k predictions. Refuse to overwrite an existing lock."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f), False
    payload = {
        'locked_at_local': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'rule': 's1 is estimated only from composition depth-1 typo runs. Depths 2 and 4 were not used.',
        'models': {},
    }
    d1 = [r for r in rows if r.get('part') == 'composition' and int(r.get('depth') or 0) == 1 and r.get('condition') == 'typo']
    for model in MODELS:
        sub = [r for r in d1 if r.get('model') == model]
        typo = G.measure_survival(sub, 'typo_survival')
        disc = G.measure_survival(sub, 'disagreement_survival')
        truth = G.measure_survival(sub, 'truth_survival')
        payload['models'][model] = {
            'n_depth1': len(sub),
            'typo_survival': typo,
            'disagreement_survival': disc,
            'truth_survival': truth,
            'predict_typo': G.predict_from_s1(typo),
            'predict_disagreement': G.predict_from_s1(disc),
        }
    with open(path, 'w') as f:
        json.dump(_jsonable(payload), f, indent=1)
    return payload, True


def load_traces(folder=OUT):
    rows = []
    if not os.path.isdir(folder):
        return rows
    for name in sorted(os.listdir(folder)):
        if name.endswith('.json'):
            try:
                rows.append(json.load(open(os.path.join(folder, name))))
            except Exception:
                pass
    return rows


def _channel_index(rows, condition):
    """Map (template, seed, tag) -> row, plus a synthetic verbatim row."""
    by = {}
    for r in rows:
        if r.get('part') != 'channel' or r.get('condition') != condition:
            continue
        key = (r.get('template'), int(r.get('seed')), r.get('channel_tag') or r.get('tag'))
        by[key] = r
    # Verbatim from any row of that (template, seed): source is identical.
    verbatim = {}
    seen = set()
    for r in rows:
        if r.get('part') != 'channel' or r.get('condition') != condition:
            continue
        key = (r.get('template'), int(r.get('seed')))
        if key in seen:
            continue
        seen.add(key)
        text = r.get('verbatim_channel') or ''
        typo = r.get('gap_neighbor')
        truth = r.get('gap_true')
        # Rebuild the cell so restricted named ids can exclude return flights.
        try:
            import s0_source as _S
            _cell = _S.make_annotated_cell(r.get('template'), r.get('seed'), r.get('condition') or 'typo')
            _src = r.get('source') or _S.build_source(_cell)
        except Exception:
            _cell, _src = None, r.get('source')
        grade = G.code_text(text, truth, typo, cell=_cell, source=_src)
        verbatim[key] = {
            'template': r.get('template'), 'seed': r.get('seed'),
            'channel_tag': 'verbatim', 'grade': grade,
            'final_text': text, 'part': 'channel', 'condition': condition,
            'model': 'verbatim', 'valid': grade.get('valid'),
        }
    return by, verbatim


def pair_rows_for(rows, condition, pair_name):
    left_tag = right_tag = None
    for name, a, b in PAIR_TYPES:
        if name == pair_name:
            left_tag, right_tag = a, b
            break
    if not left_tag:
        return []
    by, verbatim = _channel_index(rows, condition)
    # Collect all (template, seed) in this condition
    keys = set()
    for (tmpl, seed, tag) in by:
        keys.add((tmpl, seed))
    keys |= set(verbatim.keys())
    out = []
    for tmpl, seed in sorted(keys):
        def get(tag):
            if tag == 'verbatim':
                return verbatim.get((tmpl, seed))
            return by.get((tmpl, seed, tag))
        r1, r2 = get(left_tag), get(right_tag)
        if not r1 or not r2:
            continue
        out.append({
            'template': tmpl, 'seed': seed, 'pair': pair_name,
            'c1': r1.get('grade') or {}, 'c2': r2.get('grade') or {},
            'tag1': left_tag, 'tag2': right_tag,
        })
    return out


def analyze(rows, start=None, now=None, predictions=None, complete=False):
    lines = []
    def p(s=''):
        lines.append(s)
    p('S0 results (composition law + channel correlation)')
    p('complete=%s  n_traces=%d' % (complete, len(rows)))
    if start is not None and now is not None:
        p('new spend $%.5f  cumulative $%.5f  hard cap $%.2f  cum stop $%.2f' % (
            now - start, now, HARD_CAP, CUM_STOP))
    p('')

    # Composition survival
    law_by_model = {}
    phenom_by_model = {}
    for model in MODELS:
        p('== composition %s ==' % model)
        law_by_model[model] = {}
        obs = {'typo_survival': {}, 'disagreement_survival': {}, 'truth_survival': {}}
        for depth in DEPTHS:
            sub = [r for r in rows if r.get('part') == 'composition' and r.get('model') == model and int(r.get('depth') or 0) == depth]
            n_inv = sum(1 for r in sub if not (r.get('grade') or {}).get('valid'))
            p('  depth %d: %d traces, %d invalid' % (depth, len(sub), n_inv))
            for metric in ('typo_survival', 'disagreement_survival', 'truth_survival'):
                rec = G.measure_survival(sub, metric)
                obs[metric][depth] = rec
                w = rec['wilson']
                p('    %s  %s/%s  p=%.3f  wilson[%.3f, %.3f]' % (
                    metric, rec['k'], rec['n_valid'],
                    w['p'] if w['p'] is not None else -1,
                    w['lo'] if w['lo'] is not None else -1,
                    w['hi'] if w['hi'] is not None else -1,
                ))
        # Law tests need locked s1 predictions
        pred_m = (predictions or {}).get('models') or {}
        pm = pred_m.get(model) or {}
        for metric, pred_key in (('typo_survival', 'predict_typo'), ('disagreement_survival', 'predict_disagreement')):
            s1_rec = None
            if pm.get(pred_key):
                # Rebuild a survival-like record from the lock
                s1_rec = pm.get(metric) or {}
            if not s1_rec:
                s1_rec = obs[metric].get(1) or {}
            result = G.law_result(s1_rec, obs[metric], use_depths=(2, 4))
            law_by_model[model][metric] = result
            p('  simple law (%s) miss=%s' % (metric, result['simple_miss']))
            for t in result['tests']:
                p('    depth %s obs=%s pred=%s miss=%s %s' % (
                    t['depth'],
                    'NA' if t['observed'] is None else '%.3f' % t['observed'],
                    'NA' if t['predicted'] is None else '%.3f' % t['predicted'],
                    t['miss'], ','.join(t['reasons']) or 'ok',
                ))
            bak = result['backup']
            if bak.get('fit'):
                p('  backup saturating s_inf=%.3f a=%.3f  pred_d4=%s  hits=%s' % (
                    bak['fit']['s_inf'], bak['fit']['a'],
                    'NA' if bak.get('depth4_predicted') is None else '%.3f' % bak['depth4_predicted'],
                    bak.get('hits_depth4'),
                ))
            else:
                p('  backup saturating family: no valid fit')
            p('  family_claim=%s  ceiling_at_depth_1=%s  (a pass here means not refuted, not law confirmed; report CI widths above)' % (
                result.get('family_claim'), result.get('ceiling_at_depth_1')))
        dead, vals = G.phenomenon_dead(obs['typo_survival'].get(4), obs['disagreement_survival'].get(4))
        phenom_by_model[model] = {'dead': dead, 'vals': vals}
        p('  phenomenon dead (both >0.9 at d4): %s  %s' % (dead, vals))
        d4rows = [r for r in rows if r.get('part') == 'composition' and r.get('model') == model and int(r.get('depth') or 0) == 4]
        hopwise = {
            'typo_survival': G.hopwise_conditional(d4rows, 'typo_survival'),
            'disagreement_survival': G.hopwise_conditional(d4rows, 'disagreement_survival'),
        }
        law_by_model[model]['hopwise_conditional'] = hopwise
        p('  hop-wise conditional transmission (descriptive, not a primary):')
        for metric, recs in hopwise.items():
            bits = []
            for j in ('1', '2', '3', '4'):
                w = recs.get(j) or {}
                bits.append('j=%s %s/%s p=%s' % (
                    j, w.get('k'), w.get('n'),
                    'NA' if w.get('p') is None else '%.3f' % w['p'],
                ))
            p('    %s  %s' % (metric, '; '.join(bits)))
        p('')

    simple_fail = False
    ceiling_both = True
    family_claims = {'typo_survival': [], 'disagreement_survival': []}
    if MODELS[0] in law_by_model and MODELS[1] in law_by_model:
        for metric in ('typo_survival', 'disagreement_survival'):
            recs = [law_by_model[m].get(metric, {}) for m in MODELS]
            family_claims[metric] = [r.get('family_claim') for r in recs]
            # Do not label a ceiling-at-depth-1 miss as "hops interact".
            if all(r.get('simple_miss') and not r.get('ceiling_at_depth_1') for r in recs):
                simple_fail = True
            if not all(r.get('ceiling_at_depth_1') for r in recs):
                ceiling_both = False
        if not all(bool(law_by_model[m].get('typo_survival', {}).get('ceiling_at_depth_1')) for m in MODELS):
            ceiling_both = False
    else:
        ceiling_both = False
    phenom_fail = all(phenom_by_model.get(m, {}).get('dead') for m in MODELS)
    law_claims = {metric: G.law_claim_across_models(family_claims[metric]) for metric in family_claims}

    p('== composition kill ==')
    p('simple law fails (both models miss s^k, excluding ceiling-at-depth-1): %s' % simple_fail)
    p('ceiling at depth 1 on both models: %s' % ceiling_both)
    p('family claims (tie rule: both hit => not_discriminated, no law): %s' % law_claims)
    p('phenomenon dead (both models, both metrics, d4>0.9): %s' % phenom_fail)
    p('n=24 is a screen; a pass means not refuted. Report interval widths with every rate.')
    p('')

    # Channel correlation
    corr = {}
    fa = {}
    p('== channel typo correlation ==')
    typo_pairs = {}
    for name, _a, _b in PAIR_TYPES:
        pr = pair_rows_for(rows, 'typo', name)
        typo_pairs[name] = pr
        rec = G.correlation_from_pairs(pr)
        corr[name] = rec
        p('  %s n=%s p1=%s p2=%s both=%s indep=%s excess=%s identified=%s near0=%s' % (
            name, rec.get('n'),
            _fmt(rec.get('p1')), _fmt(rec.get('p2')), _fmt(rec.get('p_both')),
            _fmt(rec.get('p_indep')), _fmt(rec.get('excess')),
            rec.get('identified'), rec.get('near_zero_correlation'),
        ))
        by_t = rec.get('by_template') or {}
        for tmpl, sub in sorted(by_t.items()):
            p('    template %s excess=%s p_both=%s p_indep=%s n=%s' % (
                tmpl, _fmt(sub.get('excess')), _fmt(sub.get('p_both')),
                _fmt(sub.get('p_indep')), sub.get('n')))
    p('== channel clean false-alarm (named-id conflict only; omissions are secondary) ==')
    for name, _a, _b in PAIR_TYPES:
        if name not in FA_PAIRS:
            continue
        pr = pair_rows_for(rows, 'clean', name)
        rec = G.false_alarm_rate(pr)
        fa[name] = rec
        sd = rec.get('state_diff') or {}
        om1 = rec.get('omission_c1') or {}
        om2 = rec.get('omission_c2') or {}
        anyd = rec.get('any_id_difference') or {}
        p('  %s n=%s fa_rate=%s kill=%s  state_diff=%s  any_id_diff=%s  omit_c1=%s omit_c2=%s' % (
            name, rec.get('n'), _fmt(rec.get('rate')), rec.get('kill'),
            _fmt(sd.get('rate')), _fmt(anyd.get('rate')), _fmt(om1.get('rate')), _fmt(om2.get('rate'))))
    law_pairs_only = {k: corr[k] for k in LAW_PAIRS if k in corr}
    ck = G.correlation_kill(law_pairs_only, fa)
    p('correlation kill: false_alarm=%s  law_kill=%s  reason=%s' % (
        ck['false_alarm_kill'], ck['correlation_law_kill'], ck.get('correlation_reason')))
    p('')
    p('Verbatim pairs are reported for diversity (P(visible disagreement) when the typo is in the source).')
    p('The independence test is not identified when verbatim is deterministically typo; those pairs are not used for the correlation-law kill.')

    text = '\n'.join(lines)
    payload = {
        'complete': complete,
        'n_traces': len(rows),
        'composition_law_by_model': law_by_model,
        'phenomenon_by_model': phenom_by_model,
        'simple_law_fails': simple_fail,
        'ceiling_at_depth_1_both': ceiling_both,
        'family_claims': law_claims,
        'phenomenon_dead': phenom_fail,
        'correlation': corr,
        'false_alarm': fa,
        'correlation_kill': ck,
        'counts': expected_counts(),
        'start': start, 'now': now,
        'new_spend': None if (start is None or now is None) else now - start,
    }
    return text, payload


def _fmt(x):
    if x is None:
        return 'NA'
    return '%.3f' % x


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k) if not isinstance(k, (str, int, float, bool)) and k is not None else k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, float):
        return o
    return o


def run_jobs(js, llms, start, workers):
    os.makedirs(OUT, exist_ok=True)
    stop = threading.Event()
    lock = threading.Lock()
    done_n = [0]
    planned = [j for j in js if not os.path.exists(path_of(j))]

    def one(j):
        if stop.is_set():
            return None
        now = A.conservative_spend(A.token_spend(), A.server_delta(force=False))
        new = now - start
        if new >= HARD_CAP or now >= CUM_STOP:
            stop.set()
            print('BUDGET STOP new=$%.5f cum=$%.5f' % (new, now), flush=True)
            return None
        if new >= SOFT_CAP:
            print('SOFT CAP warning new=$%.5f (continuing until hard $%.2f)' % (new, HARD_CAP), flush=True)
        t0 = time.time()
        rec = run_one(j, llms[j['model']])
        rec['wall_s'] = round(time.time() - t0, 1)
        json.dump(rec, open(path_of(j), 'w'), indent=1, ensure_ascii=False)
        A.save_ledger()
        spend = A.log_cell_spend(j['model'], j['template'], j['seed'], j.get('tag') or j['part'], rec.get('cost') or 0)
        new = spend['conservative_spend_usd'] - start
        with lock:
            done_n[0] += 1
            n = done_n[0]
        g = rec.get('grade') or {}
        print('%s %s %s s%s %s state=%s $%.5f new=$%.4f n=%d/%d' % (
            j['part'], j['model'].replace('api-', ''), j['template'], j['seed'],
            j.get('tag'), g.get('state'), rec.get('cost') or 0, new, n, len(planned),
        ), flush=True)
        if new >= HARD_CAP or spend['conservative_spend_usd'] >= CUM_STOP:
            stop.set()
        return rec

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, planned))
    A.server_delta(force=True)
    A.save_ledger()
    return not stop.is_set()


def main():
    js = build_jobs()
    counts = expected_counts()
    if len(sys.argv) > 1 and sys.argv[1] == '--jobs-only':
        print('jobs', counts)
        by = Counter((j['part'], j.get('stage'), j['model']) for j in js)
        for k, v in sorted(by.items()):
            print(k, v)
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--lock-predictions':
        rows = load_traces()
        pred, wrote = lock_predictions(rows)
        print('predictions', PRED_PATH, 'wrote' if wrote else 'already_locked', 'n_d1',
              sum((pred.get('models') or {}).get(m, {}).get('n_depth1') or 0 for m in MODELS))
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--analyze-only':
        start = json.load(open(START_SPEND_PATH))['start'] if os.path.exists(START_SPEND_PATH) else None
        now = None
        rows = load_traces()
        pred = json.load(open(PRED_PATH)) if os.path.exists(PRED_PATH) else None
        text, payload = analyze(rows, start, now, predictions=pred, complete=len(rows) >= counts['total_jobs'])
        open(RESULTS_TXT, 'w').write(text + '\n')
        json.dump(_jsonable(payload), open(RESULTS_JSON, 'w'), indent=1)
        print(text)
        print('wrote', RESULTS_TXT)
        return

    # Paid path. Nothing below here should run during the freeze.
    require_s0_go()
    start = start_spend()
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    if not os.path.exists(START_SPEND_PATH):
        json.dump({
            'start': start, 'hard_cap': HARD_CAP, 'soft_cap': SOFT_CAP,
            'cum_stop': CUM_STOP, 'expected_usd': EXPECTED_USD,
            'cum_start_known': CUM_START_KNOWN,
            'seeds_compose': list(COMPOSE_SEEDS),
            'seeds_typo': list(PROBE_TYPO_SEEDS),
            'seeds_clean': list(PROBE_CLEAN_SEEDS),
            'models': list(MODELS),
        }, open(START_SPEND_PATH, 'w'), indent=1)
    else:
        start = json.load(open(START_SPEND_PATH))['start']
    rl = A.RL(80)
    llms = {m: A.LLM(m, rl) for m in MODELS}
    workers = int(os.environ.get('PILOT_WORKERS', '8'))
    print('S0 paid run start=$%.5f hard=$%.2f cum_stop=$%.2f jobs=%d' % (
        start, HARD_CAP, CUM_STOP, len(js)), flush=True)

    all_ok = True
    for stage in stage_order():
        chunk = jobs_in_stage(js, stage)
        now = start_spend()
        if now - start >= HARD_CAP or now >= CUM_STOP:
            print('BUDGET STOP before stage', stage, flush=True)
            all_ok = False
            break
        print('stage', stage, 'jobs', len(chunk), 'new=$%.4f' % (now - start), flush=True)
        ok = run_jobs(chunk, llms, start, workers)
        if stage == 'compose_d1':
            rows = load_traces()
            pred, wrote = lock_predictions(rows)
            print('locked predictions' if wrote else 'predictions already locked', PRED_PATH, flush=True)
        if not ok:
            all_ok = False
            break

    now = start_spend()
    rows = load_traces()
    pred = json.load(open(PRED_PATH)) if os.path.exists(PRED_PATH) else None
    text, payload = analyze(rows, start, now, predictions=pred, complete=(len(rows) >= counts['total_jobs'] and all_ok))
    open(RESULTS_TXT, 'w').write(text + '\n')
    json.dump(_jsonable(payload), open(RESULTS_JSON, 'w'), indent=1)
    print(text, flush=True)
    print('wrote', RESULTS_TXT, RESULTS_JSON, flush=True)


if __name__ == '__main__':
    main()
