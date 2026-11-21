"""Exploratory verify/cue/hedge/stale screen. Not confirmatory. Does not write MAIN_GO."""
import json, os, sys, time, threading, datetime, re
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor

import agent as A
import run_pilot as R
import templates_v3 as TV
import grade_v3 as GV
import perturb_gaps as PG
import handoff_summary as HS

HERE = A.HERE
OUT = os.path.join(HERE, os.environ.get('PILOT_TRACES', 'traces_screen_gaps_verify_nov21'))
os.makedirs(OUT, exist_ok=True)
MODEL = 'api-deepseek-4.1-flash'
PASS_CAP = 0.60
STALE_RESERVE = 0.10
TEMPLATES = list(TV.TEMPLATES)
SEEDS = (0, 1)

# name, form, flags
ARM_CORE = [
    ('P_dropped_verify', 'dropped', {'verify_tool': True}),
    ('P_substituted_verify', 'substituted', {'verify_tool': True}),
    ('P_substituted_cue', 'substituted', {'cue': True}),
    ('P_complete_plus_verbatim', 'complete', {'agreeing_verbatim': True}),
    ('P_hedge_kept', 'substituted', {'hedge_on_neighbor': True}),
    ('P_hedge_stripped', 'substituted', {'perturb_source_before_summary': True, 'hedge_on_neighbor_source': True}),
]
ARM_STALE = [
    ('P_stale_memory', 'substituted', {'stale_memory': True}),
]
TAGS = {
    'P_dropped_verify': 'VDROP',
    'P_substituted_verify': 'VSUBS',
    'P_substituted_cue': 'VCUE',
    'P_complete_plus_verbatim': 'VAGR',
    'P_hedge_kept': 'VHK',
    'P_hedge_stripped': 'VHS',
    'P_stale_memory': 'VSTALE',
}


def build_jobs(arms):
    js = []
    for seed in SEEDS:
        for tmpl in TEMPLATES:
            for name, form, flags in arms:
                js.append({
                    'model': MODEL, 'template': tmpl, 'task': TV.TOOL_TASK[tmpl],
                    'seed': seed, 'cell': 'C0', 'arm': 'P_full',
                    'setup': name, 'gap_form': form, 'flags': dict(flags),
                    'tag': TAGS[name], 'screen': 'verify',
                })
    return js


def path_of(j):
    name = '%s__%s__%d__%s__%s.json' % (j['model'], j['template'], j['seed'], j['cell'], j['tag'])
    return os.path.join(OUT, name)


def make_cell(j):
    c = TV.make_cell(j['template'], j['seed'], j['cell'])
    c['design'] = 'v3'
    c['arm'] = j['arm']
    c['self_review'] = False
    c['summary_retry'] = False
    c['summary_gate'] = False
    c['oracle_faithful'] = True
    c['executor_plan_only'] = True
    c['confirm_rule'] = False
    c['mock_user'] = False
    c['no_concerns'] = False
    c['regen_mem'] = c['mem']
    flags = dict(j.get('flags') or {})
    form = j.get('gap_form') or 'complete'
    c = PG.annotate_cell(c, form)
    if flags.get('stale_memory'):
        c = PG.apply_stale_memory(c)
    if flags.get('cue'):
        c = PG.apply_cue_to_request(c)
    if flags.get('verify_tool'):
        c['verify_tool'] = True
    if flags.get('agreeing_verbatim'):
        c['agreeing_verbatim'] = True
        c['handoff_mode'] = 'verbatim'
        c['synthetic_summary'] = False
    if flags.get('hedge_on_neighbor'):
        c['hedge_on_neighbor'] = True
        c['handoff_mode'] = 'short'
    if flags.get('perturb_source_before_summary'):
        c['perturb_source_before_summary'] = True
        c['hedge'] = True
        c['hedge_on_neighbor'] = False  # do not re-attach after the summary
        c['handoff_mode'] = 'compress'
    if not c.get('handoff_mode'):
        c['handoff_mode'] = 'short'
    # record substitution metadata
    row = PG.lookup_from_list('stale_memory' if flags.get('stale_memory') else 'typo_neighbor',
                              c.get('template'), c.get('seed') or 0)
    if row:
        c['subst_meta'] = row
    return c


def start_spend():
    return A.conservative_spend(A.token_spend(), A.server_delta())


def run_jobs(js, llm, start, label, workers, stage_cap):
    stop = threading.Event()
    todo = [j for j in js if not os.path.exists(path_of(j))]
    print('%s todo %d planned %d start=$%.5f stop=$%.2f pass_cap=$%.2f stage_cap=$%.2f' % (
        label, len(todo), len(js), start, A.STOP_AT, PASS_CAP, stage_cap), flush=True)

    def one(j):
        if stop.is_set():
            return None
        now = start_spend() if False else A.conservative_spend(A.token_spend(), A.server_delta(force=False))
        new = now - start
        if new >= PASS_CAP or new >= stage_cap or A.over_budget(A.token_spend(), A.server_delta(force=False), A.STOP_AT):
            stop.set()
            print('BUDGET STOP new=$%.5f' % new, flush=True)
            return None
        cell = make_cell(j)
        t0 = time.time()
        tr = A.run_agent(cell, llm)
        tr['wall_s'] = round(time.time() - t0, 1)
        if tr.get('status') == 'budget_stop':
            stop.set()
            print('BUDGET STOP', flush=True)
            return None
        rec = dict(j)
        rec.update({'field': cell.get('field'), 'write_path': cell.get('write_path'),
                    'mem': cell['mem'], 'trusted': cell.get('trusted'), 'world': cell['world'],
                    'request': cell['request'],
                    'gap_slot': cell.get('gap_slot'), 'gap_true': cell.get('gap_true'),
                    'gap_neighbor': cell.get('gap_neighbor'),
                    'cue': bool(cell.get('cue')), 'verify_tool': bool(cell.get('verify_tool')),
                    'stale_memory': bool(cell.get('stale_memory')),
                    'subst_meta': cell.get('subst_meta'),
                    'handoff_mode': cell.get('handoff_mode')})
        rec.update(tr)
        json.dump(rec, open(path_of(j), 'w'), indent=1, ensure_ascii=False)
        A.save_ledger()
        spend = A.log_cell_spend(MODEL, j['template'], j['seed'], j['cell'], tr['cost'] or 0)
        new = spend['conservative_spend_usd'] - start
        g = tr.get('v3_grade') or {}
        print('%s %s s%s ended=%s status=%s out=%s used=%s wrong=%s ask=%s verified=%s faithful=%s correct=%s $%.5f %ss new=$%.4f' % (
            j['setup'], j['template'], j['seed'], tr.get('ended_by'), tr.get('status'),
            g.get('outcome'), g.get('used_target'), g.get('wrong_action'), g.get('asked'),
            g.get('verified'), g.get('faithful_to_handoff'), g.get('correct_vs_truth'),
            tr.get('cost') or 0, tr.get('wall_s'), new), flush=True)
        if new >= PASS_CAP or new >= stage_cap:
            stop.set()
        return rec

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))
    A.server_delta(force=True)
    A.save_ledger()
    return not stop.is_set()


def load_traces():
    rows = []
    if not os.path.isdir(OUT):
        return rows
    for name in sorted(os.listdir(OUT)):
        if name.endswith('.json'):
            try:
                rows.append(json.load(open(os.path.join(OUT, name))))
            except Exception:
                pass
    return rows


# ---- Natural substitution from existing compressed traces (no new spend) ----

COMPRESS_FOLDERS = [
    ('traces_rescreen_nov21', ('compress', 'compress_keep_flags', 'structured', 'compress_250', 'compress_1k')),
    ('traces_v3_sanity', ('compress', 'compress_250', 'compress_1k', 'structured')),
    ('traces_v3_explore_dose', ('compress', 'compress_250', 'compress_1k')),
    # Do not include traces_screen_gaps_nov21: those substitutions were injected.
]


def code_natural_substitution():
    rows = []
    for folder, modes in COMPRESS_FOLDERS:
        d = os.path.join(HERE, folder)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith('.json'):
                continue
            r = json.load(open(os.path.join(d, name)))
            mode = r.get('handoff_mode')
            if modes and mode not in modes:
                continue
            summary = r.get('handoff_summary_raw') or r.get('handoff_summary') or ''
            if not (summary or '').strip():
                rows.append({'folder': folder, 'mode': mode, 'setup': r.get('setup'),
                             'template': r.get('template'), 'seed': r.get('seed'),
                             'cell': r.get('cell'), 'empty': True, 'codes': []})
                continue
            cov = (r.get('slot_coverage_summary') or r.get('slot_coverage') or {})
            codes = list(cov.get('codes') or [])
            # Re-code against the source when possible, using v3 helpers
            src = r.get('source_handoff') or ''
            if r.get('world') and r.get('template') and src:
                try:
                    cell = {'world': r['world'], 'template': r.get('template'), 'task': r.get('task')}
                    req = GV.required_from_source(cell, src)
                    alts = TV.slot_alts(r['world'] or {})
                    codes = GV.code_slots(summary, req, alts)
                except Exception:
                    pass
            rec = {'folder': folder, 'mode': mode, 'setup': r.get('setup'),
                   'template': r.get('template'), 'seed': r.get('seed'),
                   'cell': r.get('cell'), 'empty': False, 'codes': codes,
                   'n_present': sum(1 for c in codes if c.get('code')=='present'),
                   'n_dropped': sum(1 for c in codes if c.get('code')=='dropped'),
                   'n_substituted': sum(1 for c in codes if c.get('code')=='substituted')}
            rows.append(rec)
    return rows


def summarize_natural(rows):
    # per-slot and per-run
    slots = Counter(); slot_n = Counter()
    empty = 0
    used = [r for r in rows if not r.get('empty')]
    empty = sum(1 for r in rows if r.get('empty'))
    for r in used:
        for c in r.get('codes') or []:
            slot_n[c.get('key')] += 1
            slots[(c.get('key'), c.get('code'))] += 1
    n_run = len(used)
    n_sub_run = sum(1 for r in used if (r.get('n_substituted') or 0) > 0)
    n_drop_run = sum(1 for r in used if (r.get('n_dropped') or 0) > 0)
    n_intact = sum(1 for r in used if (r.get('n_substituted') or 0)==0 and (r.get('n_dropped') or 0)==0)
    total_slots = sum(slot_n.values())
    n_sub_slot = sum(v for (k,c),v in slots.items() if c=='substituted')
    n_drop_slot = sum(v for (k,c),v in slots.items() if c=='dropped')
    n_pres_slot = sum(v for (k,c),v in slots.items() if c=='present')
    return {
        'n_traces_scored': n_run, 'n_empty': empty,
        'runs_with_any_substitution': n_sub_run,
        'runs_with_any_drop': n_drop_run,
        'runs_intact': n_intact,
        'slots_total': total_slots,
        'slots_present': n_pres_slot,
        'slots_dropped': n_drop_slot,
        'slots_substituted': n_sub_slot,
        'sub_rate_per_run': (n_sub_run / n_run) if n_run else None,
        'sub_rate_per_slot': (n_sub_slot / total_slots) if total_slots else None,
        'drop_rate_per_slot': (n_drop_slot / total_slots) if total_slots else None,
        'by_slot': {k: {'n': slot_n[k],
                        'present': slots[(k,'present')],
                        'dropped': slots[(k,'dropped')],
                        'substituted': slots[(k,'substituted')]} for k in slot_n},
    }


def analyze(rows, start, now, natural):
    lines = []
    def p(s=''):
        lines.append(s)
        print(s)
    p('STEP 0. Did the executor user request already contain the true value?')
    p('No. In the Nov 21 gaps screen, 0 of 12 requests per form contained the true slot value.')
    p('Template requests say "my usual trip" or "email me a summary" and never name the flight ID or the address.')
    p('So the 12/12 substituted wrong-action result is the no-cue ceiling, not "the agent trusted the handoff over the user."')
    p('')
    p('SCREEN (exploratory, deepseek, C0, n=2, 6 templates). Grade: wrong vs world truth, and faithful to the handoff.')
    setups = [a[0] for a in ARM_CORE + ARM_STALE]
    p('setup                     n  wrong  ask  hold  correct  other  used_neigh  used_truth  verified  faithful_wrong  tool_err')
    for setup in setups:
        rs = [r for r in rows if r.get('setup')==setup]
        if not rs:
            p('%-26s  0  (not run or budget)' % setup)
            continue
        def cnt(pred):
            return sum(1 for r in rs if pred(r))
        g = lambda r: r.get('v3_grade') or {}
        n=len(rs)
        wrong=cnt(lambda r: g(r).get('wrong_action'))
        ask=cnt(lambda r: g(r).get('outcome')=='ask')
        hold=cnt(lambda r: g(r).get('outcome')=='hold')
        correct=cnt(lambda r: g(r).get('outcome')=='correct')
        other=cnt(lambda r: g(r).get('outcome') not in ('wrong_action','ask','hold','correct'))
        un=cnt(lambda r: g(r).get('used_target')=='neighbor')
        ut=cnt(lambda r: g(r).get('used_target')=='truth')
        ver=cnt(lambda r: g(r).get('verified'))
        fbw=cnt(lambda r: g(r).get('faithful_but_wrong'))
        te=cnt(lambda r: g(r).get('tool_error'))
        p('%-26s %2d  %5d  %3d  %4d  %7d  %5d  %10d  %10d  %8d  %14d  %8d' % (
            setup, n, wrong, ask, hold, correct, other, un, ut, ver, fbw, te))
    p('')
    p('Per-template wrong-action and verify rates')
    p('template       dropV_wrong dropV_verif subV_wrong subV_verif cue_wrong cue_used  hedgeK_wrong hedgeS_wrong stale_wrong')
    for tmpl in TEMPLATES:
        def wr(setup):
            rs=[r for r in rows if r.get('setup')==setup and r.get('template')==tmpl]
            n=len(rs)
            if not n: return None, None, 0
            g=lambda r: r.get('v3_grade') or {}
            return sum(1 for r in rs if g(r).get('wrong_action'))/n, sum(1 for r in rs if g(r).get('verified'))/n, n
        def fmt(x):
            return '  na' if x is None else '%4.0f%%' % (100*x)
        dw,dv,_=wr('P_dropped_verify')
        sw,sv,_=wr('P_substituted_verify')
        cw,_,_=wr('P_substituted_cue')
        # used neighbor on cue
        crs=[r for r in rows if r.get('setup')=='P_substituted_cue' and r.get('template')==tmpl]
        used = (sum(1 for r in crs if (r.get('v3_grade') or {}).get('used_target')=='neighbor')/len(crs)) if crs else None
        hk,_,_=wr('P_hedge_kept')
        hs,_,_=wr('P_hedge_stripped')
        st,_,_=wr('P_stale_memory')
        p('%-14s %s      %s      %s      %s      %s     %s     %s         %s         %s' % (
            tmpl, fmt(dw), fmt(dv), fmt(sw), fmt(sv), fmt(cw), fmt(used), fmt(hk), fmt(hs), fmt(st)))
    p('')
    # hedge survival
    p('Hedge chain (exact tag "from MEMORY.md, unverified" or the word unverified)')
    for setup in ('P_hedge_kept','P_hedge_stripped'):
        rs=[r for r in rows if r.get('setup')==setup]
        if not rs:
            p('  %s: not run' % setup); continue
        val=tag=wrong=ask=0
        for r in rs:
            text = r.get('handoff_summary_raw') or r.get('handoff_summary') or r.get('executor_handoff') or ''
            # for kept (short), score the handoff; for stripped, score the summary
            if setup=='P_hedge_stripped':
                text = r.get('handoff_summary_raw') or r.get('handoff_summary') or ''
            truth=r.get('gap_true'); neigh=r.get('gap_neighbor')
            if GV._contains(neigh, text) or GV._contains(truth, text):
                val += 1
            if PG.hedge_exact_in(text):
                tag += 1
            if (r.get('v3_grade') or {}).get('wrong_action'):
                wrong += 1
            if (r.get('v3_grade') or {}).get('outcome')=='ask':
                ask += 1
        p('  %s n=%d value_in_text=%d tag=%d wrong=%d ask=%d' % (setup, len(rs), val, tag, wrong, ask))
    p('')
    p('Agreeing verbatim (C0 ask cost of redundancy)')
    rs=[r for r in rows if r.get('setup')=='P_complete_plus_verbatim']
    if rs:
        ask=sum(1 for r in rs if (r.get('v3_grade') or {}).get('outcome')=='ask')
        hold=sum(1 for r in rs if (r.get('v3_grade') or {}).get('outcome')=='hold')
        correct=sum(1 for r in rs if (r.get('v3_grade') or {}).get('outcome')=='correct')
        wrong=sum(1 for r in rs if (r.get('v3_grade') or {}).get('wrong_action'))
        p('  n=%d ask=%d hold=%d correct=%d wrong=%d' % (len(rs), ask, hold, correct, wrong))
    else:
        p('  not run')
    p('')
    p('NATURAL SUBSTITUTION in existing compressed summaries (no new spend)')
    p('  scored=%s empty=%s runs_with_substitution=%s runs_with_drop=%s runs_intact=%s' % (
        natural.get('n_traces_scored'), natural.get('n_empty'),
        natural.get('runs_with_any_substitution'), natural.get('runs_with_any_drop'),
        natural.get('runs_intact')))
    p('  slots: total=%s present=%s dropped=%s substituted=%s' % (
        natural.get('slots_total'), natural.get('slots_present'),
        natural.get('slots_dropped'), natural.get('slots_substituted')))
    p('  substitution rate per run=%s per slot=%s' % (
        natural.get('sub_rate_per_run'), natural.get('sub_rate_per_slot')))
    p('  by slot: %s' % json.dumps(natural.get('by_slot') or {}))
    p('')
    new = now - start
    p('Spend this pass (conservative): $%.5f of $%.2f cap' % (new, PASS_CAP))
    p('Cumulative conservative spend:  $%.5f (hard stop $%.2f, standing cap $19.75)' % (now, A.STOP_AT))
    return '\n'.join(lines)


def main():
    env = os.environ
    if os.path.exists(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) and os.path.getsize(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) > 0:
        raise SystemExit('Refusing: MAIN_GO.txt is present. No API calls made.')
    core, stale = build_jobs(ARM_CORE), build_jobs(ARM_STALE)
    planned = core + stale
    if len(sys.argv) > 1 and sys.argv[1] == '--jobs-only':
        print('core', len(core), 'stale', len(stale), 'total', len(planned))
        json.dump({'core': core, 'stale': stale}, open(os.path.join(HERE, 'gaps_verify_nov21_jobs.json'), 'w'), indent=1)
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--natural-only':
        nat_rows = code_natural_substitution()
        nat = summarize_natural(nat_rows)
        json.dump({'natural': nat, 'rows': nat_rows}, open(os.path.join(HERE, 'natural_substitution_nov21.json'), 'w'), indent=1)
        print(json.dumps(nat, indent=1))
        return
    env2 = dict(os.environ)
    env2['PILOT_GAPS_VERIFY'] = '1'
    R.require_go(stop_at=float(os.environ.get('PILOT_STOP_AT', str(A.STOP_AT))), n_jobs=len(planned), env=env2)
    start = start_spend()
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    json.dump({'start': start, 'pass_cap': PASS_CAP}, open(os.path.join(HERE, 'gaps_verify_nov21_start_spend.json'), 'w'), indent=1)
    llm = A.LLM(MODEL, A.RL(80))
    workers = int(os.environ.get('PILOT_WORKERS', '5'))
    run_jobs(core, llm, start, 'verify_core', workers, PASS_CAP - STALE_RESERVE)
    now = start_spend()
    remain = PASS_CAP - (now - start)
    if remain >= 0.08:
        run_jobs(stale, llm, start, 'verify_stale', workers, PASS_CAP)
    now = start_spend()
    rows = load_traces()
    nat_rows = code_natural_substitution()
    nat = summarize_natural(nat_rows)
    json.dump({'natural': nat, 'n_natural_rows': len(nat_rows)}, open(os.path.join(HERE, 'natural_substitution_nov21.json'), 'w'), indent=1)
    text = analyze(rows, start, now, nat)
    open(os.path.join(HERE, 'screen_gaps_verify_nov21_results.txt'), 'w').write(text + '\n')
    json.dump({'n_traces': len(rows), 'new_spend': now-start, 'cumulative': now, 'natural': nat},
              open(os.path.join(HERE, 'screen_gaps_verify_nov21_results.json'), 'w'), indent=1)
    print('wrote screen_gaps_verify_nov21_results.txt', flush=True)


if __name__ == '__main__':
    main()
