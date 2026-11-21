"""Exploratory travel world-fix re-screen. Not confirmatory. Does not write MAIN_GO."""
import json, os, sys, time, threading, datetime
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor

import agent as A
import run_pilot as R
import templates_v3 as TV
import grade_v3 as GV
import perturb_gaps as PG

HERE = A.HERE
OUT = os.path.join(HERE, os.environ.get('PILOT_TRACES', 'traces_screen_gaps_worldfix_nov21'))
os.makedirs(OUT, exist_ok=True)
MODEL = 'api-deepseek-4.1-flash'
PASS_CAP = 0.15
TEMPLATES = ('trip', 'trip_return', 'trip_long')
SEEDS = (0, 1)

# name, form, flags, tag
ARMS_CORE = [
    ('P_dropped_verify', 'dropped', {'verify_tool': True, 'preserve_listings': True}, 'WFDROP'),
    ('P_substituted_verify', 'substituted', {'verify_tool': True, 'preserve_listings': True}, 'WFSUBS'),
]
ARMS_AMBIG = [
    ('P_substituted_verify_ambig', 'substituted',
     {'verify_tool': True, 'verify_tool_ambig': True, 'preserve_listings': True}, 'WFAMBG'),
]


def build_jobs(arms):
    js = []
    for seed in SEEDS:
        for tmpl in TEMPLATES:
            for name, form, flags, tag in arms:
                js.append({
                    'model': MODEL, 'template': tmpl, 'task': TV.TOOL_TASK[tmpl],
                    'seed': seed, 'cell': 'C0', 'arm': 'P_full',
                    'setup': name, 'gap_form': form, 'flags': dict(flags),
                    'tag': tag, 'screen': 'worldfix',
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
    c['executor_plan_only'] = True
    c['confirm_rule'] = False
    c['mock_user'] = False
    c['no_concerns'] = False
    c['regen_mem'] = c['mem']
    flags = dict(j.get('flags') or {})
    c = PG.annotate_cell(c, j.get('gap_form') or 'complete')
    c['preserve_listings'] = True
    c['verify_tool'] = True
    if flags.get('verify_tool_ambig'):
        c['verify_tool_ambig'] = True
    c['handoff_mode'] = 'short'
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
        now = A.conservative_spend(A.token_spend(), A.server_delta(force=False))
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
        rec.update({'mem': cell['mem'], 'world': cell['world'], 'request': cell['request'],
                    'gap_slot': cell.get('gap_slot'), 'gap_true': cell.get('gap_true'),
                    'gap_neighbor': cell.get('gap_neighbor'),
                    'preserve_listings': True,
                    'verify_tool_name': PG.verify_tool_name(cell)})
        rec.update(tr)
        json.dump(rec, open(path_of(j), 'w'), indent=1, ensure_ascii=False)
        A.save_ledger()
        spend = A.log_cell_spend(MODEL, j['template'], j['seed'], j['cell'], tr['cost'] or 0)
        new = spend['conservative_spend_usd'] - start
        g = tr.get('v3_grade') or {}
        # world check on the actual handoff
        hand = tr.get('executor_handoff') or ''
        listed_t = PG.listings_contain(hand, cell.get('gap_true'))
        listed_n = PG.listings_contain(hand, cell.get('gap_neighbor'))
        print('%s %s s%s ended=%s status=%s det=%s used=%s ver=%s truth_listed=%s typo_listed=%s $%.5f %ss new=$%.4f' % (
            j['setup'], j['template'], j['seed'], tr.get('ended_by'), tr.get('status'),
            g.get('detailed'), g.get('used_target'), g.get('verified'), listed_t, listed_n,
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


def analyze(rows, start, now):
    lines = []
    def p(s=''):
        lines.append(s)
        print(s)
    p('WORLD-FIX RE-SCREEN (exploratory): travel only, deepseek, n=2, 3 templates')
    p('Authoritative tool: get_trip_selection. Ambig tool: get_reservation (old wording).')
    p('Listings preserve the true flight and also list the typo, same date, both bookable.')
    p('')
    setups = [a[0] for a in ARMS_CORE + ARMS_AMBIG]
    p('setup                        n  silent  flag_proceed  flag_before  ask_or_hold  correct  other  verified  saw_still_wrong  truth_listed  typo_listed')
    for setup in setups:
        rs = [r for r in rows if r.get('setup') == setup]
        if not rs:
            p('%-28s  0  (not run or budget)' % setup)
            continue
        def c(pred):
            return sum(1 for r in rs if pred(r))
        g = lambda r: r.get('v3_grade') or {}
        n = len(rs)
        silent = c(lambda r: g(r).get('detailed') == 'silent_wrong')
        fp = c(lambda r: g(r).get('detailed') == 'flag_and_proceed_wrong')
        fb = c(lambda r: g(r).get('flag_before_act') and g(r).get('detailed') == 'flag_and_proceed_wrong')
        ah = c(lambda r: g(r).get('detailed') == 'ask_or_hold')
        co = c(lambda r: g(r).get('detailed') == 'correct')
        ot = c(lambda r: g(r).get('detailed') not in ('silent_wrong','flag_and_proceed_wrong','ask_or_hold','correct'))
        ver = c(lambda r: g(r).get('verified'))
        ssw = c(lambda r: g(r).get('verified') and g(r).get('wrong_action'))
        tl = c(lambda r: PG.listings_contain(r.get('executor_handoff') or '', r.get('gap_true')))
        nl = c(lambda r: PG.listings_contain(r.get('executor_handoff') or '', r.get('gap_neighbor')))
        p('%-28s %2d  %6d  %12d  %11d  %11d  %7d  %5d  %8d  %15d  %12d  %11d' % (
            setup, n, silent, fp, fb, ah, co, ot, ver, ssw, tl, nl))
        for r in sorted(rs, key=lambda x: (x.get('template'), x.get('seed'))):
            gg = g(r)
            p('    %s s%s det=%s used=%s ver=%s ret_truth=%s flag_before=%s wrong=%s' % (
                r.get('template'), r.get('seed'), gg.get('detailed'), gg.get('used_target'),
                gg.get('verified'), gg.get('verify_returned_truth'), gg.get('flag_before_act'),
                gg.get('wrong_action')))
    p('')
    # decision helper numbers
    auth = [r for r in rows if r.get('setup') == 'P_substituted_verify']
    drop = [r for r in rows if r.get('setup') == 'P_dropped_verify']
    def wrongs(rs):
        return sum(1 for r in rs if (r.get('v3_grade') or {}).get('wrong_action'))
    def looked_still(rs):
        return sum(1 for r in rs if (r.get('v3_grade') or {}).get('verified') and (r.get('v3_grade') or {}).get('wrong_action'))
    p('Looked-and-still-booked (authoritative substituted_verify): %d / %d (wrongs %d / %d)' % (
        looked_still(auth), len(auth), wrongs(auth), len(auth)))
    p('Dropped_verify wrongs: %d / %d' % (wrongs(drop), len(drop)))
    new = now - start
    p('Spend this pass (conservative): $%.5f of $%.2f cap' % (new, PASS_CAP))
    p('Cumulative conservative spend:  $%.5f (hard stop $%.2f, standing cap $19.75)' % (now, A.STOP_AT))
    return '\n'.join(lines)


def main():
    if os.path.exists(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) and os.path.getsize(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) > 0:
        raise SystemExit('Refusing: MAIN_GO.txt is present. No API calls made.')
    core, ambig = build_jobs(ARMS_CORE), build_jobs(ARMS_AMBIG)
    if len(sys.argv) > 1 and sys.argv[1] == '--jobs-only':
        print('core', len(core), 'ambig', len(ambig), 'total', len(core)+len(ambig))
        return
    env2 = dict(os.environ)
    env2['PILOT_GAPS_FIX'] = '1'
    R.require_go(stop_at=float(os.environ.get('PILOT_STOP_AT', str(A.STOP_AT))), n_jobs=len(core)+len(ambig), env=env2)
    start = start_spend()
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    json.dump({'start': start, 'pass_cap': PASS_CAP}, open(os.path.join(HERE, 'gaps_worldfix_nov21_start_spend.json'), 'w'), indent=1)
    llm = A.LLM(MODEL, A.RL(80))
    workers = int(os.environ.get('PILOT_WORKERS', '5'))
    # authoritative first (~12 runs), ambig if remaining budget
    run_jobs(core, llm, start, 'worldfix_core', workers, 0.11)
    now = start_spend()
    remain = PASS_CAP - (now - start)
    if remain >= 0.04:
        run_jobs(ambig, llm, start, 'worldfix_ambig', workers, PASS_CAP)
    now = start_spend()
    rows = load_traces()
    text = analyze(rows, start, now)
    open(os.path.join(HERE, 'screen_gaps_worldfix_nov21_results.txt'), 'w').write(text + '\n')
    json.dump({'n_traces': len(rows), 'new_spend': now-start, 'cumulative': now},
              open(os.path.join(HERE, 'screen_gaps_worldfix_nov21_results.json'), 'w'), indent=1)
    print('wrote screen_gaps_worldfix_nov21_results.txt', flush=True)


if __name__ == '__main__':
    main()
