"""V3 sanity and exploratory screens. Not confirmatory. Does not write MAIN_GO."""
import json, os, sys, time, threading, datetime
from concurrent.futures import ThreadPoolExecutor
import agent as A
import run_pilot as R
import templates_v3 as TV
import grade_v3 as GV

HERE = A.HERE
OUT = os.path.join(HERE, os.environ.get('PILOT_TRACES', 'traces_v3_sanity'))
os.makedirs(OUT, exist_ok=True)
MODEL = 'api-deepseek-4.1-flash'
PASS_CAP = 1.00  # new spend for this whole pass
STAGE_CAPS = {'sanity': 0.40, 'explore_fab': 0.85, 'explore_dose': 1.00}

ARM_TYPES = [
    # name, arm, mode, self_review, tag, extra
    ('S_match', 'S_full', 'short', True, 'SR', {}),
    ('P_short', 'P_full', 'short', False, 'PSH', {}),
    ('P_comp', 'P_full', 'compress', False, 'PC80', {}),
    ('P_comp_250', 'P_full', 'compress_250', False, 'PC250', {}),
    ('P_comp_1k', 'P_full', 'compress_1k', False, 'PC1K', {}),
    ('P_struct', 'P_full', 'structured', False, 'PST', {}),
    ('P_verbatim', 'P_full', 'verbatim', False, 'PVA', {}),
    ('P_oracle', 'P_full', 'oracle', False, 'POR', {}),
]

# Cheap templates first so a budget stop still covers every arm type.
SANITY_ORDER = ['trip', 'cleanup', 'trip_return', 'cleanup_keep', 'trip_long', 'cleanup_long']


def _drop_key(template):
    return 'flight_id' if TV.DOMAIN[template] == 'travel' else 'email'


def build_job(template, seed, cell, arm_tuple, extra=None):
    name, arm, mode, review, tag, base = arm_tuple
    ex = dict(base)
    if extra:
        ex.update(extra)
    return {
        'model': MODEL, 'template': template, 'task': TV.TOOL_TASK[template],
        'seed': seed, 'cell': cell, 'arm': arm, 'setup': name,
        'handoff_mode': mode, 'self_review': review, 'tag': tag,
        'extra': ex,
    }


def sanity_jobs():
    js = []
    for tmpl in SANITY_ORDER:
        for arm in ARM_TYPES:
            js.append(build_job(tmpl, 0, 'C0', arm))
    return js


def explore_fab_jobs():
    """Controlled slot drop vs complete vs verbatim restore. n=2, C0, all 6 templates."""
    short = [a for a in ARM_TYPES if a[0] == 'P_short'][0]
    verb = [a for a in ARM_TYPES if a[0] == 'P_verbatim'][0]
    js = []
    for seed in (0, 1):
        for tmpl in SANITY_ORDER:
            js.append(build_job(tmpl, seed, 'C0', short, {}))
            js.append(build_job(tmpl, seed, 'C0',
                                ('P_short_drop', 'P_full', 'short', False, 'PSD', {}),
                                {'drop_slots': [_drop_key(tmpl)]}))
            js.append(build_job(tmpl, seed, 'C0',
                                ('P_verbatim_drop', 'P_full', 'verbatim', False, 'PVD', {}),
                                {'drop_slots_in_summary': [_drop_key(tmpl)]}))
    return js


def explore_dose_jobs():
    """Dose curve on the two long templates. n=2, C0."""
    want = {'P_short', 'P_comp', 'P_comp_250', 'P_comp_1k'}
    arms = [a for a in ARM_TYPES if a[0] in want]
    js = []
    for seed in (0, 1):
        for tmpl in ('trip_long', 'cleanup_long'):
            for arm in arms:
                js.append(build_job(tmpl, seed, 'C0', arm))
    return js


def path_of(j):
    name = '%s__%s__%d__%s__%s.json' % (j['model'], j['template'], j['seed'], j['cell'], j['tag'])
    # disambiguate drop arms
    if j['setup'] in ('P_short_drop', 'P_verbatim_drop'):
        name = '%s__%s__%d__%s__%s.json' % (j['model'], j['template'], j['seed'], j['cell'], j['tag'])
    return os.path.join(OUT, name)


def make_cell(j):
    c = TV.make_cell(j['template'], j['seed'], j['cell'])
    c['design'] = 'v3'
    c['arm'] = j['arm']
    c['handoff_mode'] = j['handoff_mode']
    c['self_review'] = j['self_review']
    c['summary_retry'] = False
    c['summary_gate'] = False
    c['oracle_faithful'] = True
    c['executor_plan_only'] = not str(j['arm']).startswith('S')
    c['confirm_rule'] = False
    c['mock_user'] = False
    c['no_concerns'] = False
    c['regen_mem'] = c['mem']
    extra = j.get('extra') or {}
    for k, v in extra.items():
        c[k] = v
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
        rec.update({'field': cell.get('field'), 'write_path': cell.get('write_path'),
                    'mem': cell['mem'], 'trusted': cell.get('trusted'), 'world': cell['world'],
                    'request': cell['request']})
        rec.update(tr)
        json.dump(rec, open(path_of(j), 'w'), indent=1, ensure_ascii=False)
        A.save_ledger()
        spend = A.log_cell_spend(MODEL, j['template'], j['seed'], j['cell'], tr['cost'] or 0)
        new = spend['conservative_spend_usd'] - start
        cov = (tr.get('slot_coverage') or {})
        g = tr.get('v3_grade') or {}
        print('%s %s s%s %s ended=%s status=%s complete=%s lost=%s wrong=%s fab=%s kind=%s $%.5f %ss new=$%.4f' % (
            j['setup'], j['template'], j['seed'], j['cell'], tr.get('ended_by'), tr.get('status'),
            cov.get('complete'), cov.get('lost'), g.get('wrong_action'), g.get('fabricate_and_act'),
            g.get('kind') or g.get('lost_split'), tr.get('cost') or 0, tr.get('wall_s'), new), flush=True)
        if new >= PASS_CAP or new >= stage_cap:
            stop.set()
        return rec

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))
    A.server_delta(force=True)
    A.save_ledger()
    return not stop.is_set()


def main():
    stage = os.environ.get('PILOT_V3_STAGE', 'sanity')
    if os.path.exists(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) and os.path.getsize(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) > 0:
        raise SystemExit('Refusing: MAIN_GO.txt is present. No API calls made.')
    if stage == 'sanity':
        js = sanity_jobs(); cap = STAGE_CAPS['sanity']
    elif stage == 'explore_fab':
        js = explore_fab_jobs(); cap = STAGE_CAPS['explore_fab']
    elif stage == 'explore_dose':
        js = explore_dose_jobs(); cap = STAGE_CAPS['explore_dose']
    else:
        raise SystemExit('unknown stage %r' % stage)
    json.dump(js, open(os.path.join(HERE, 'v3_%s_jobs.json' % stage), 'w'), indent=1)
    env = os.environ
    R.require_go(stop_at=float(env.get('PILOT_STOP_AT', str(A.STOP_AT))), n_jobs=len(js), env=env)
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    start_path = os.path.join(HERE, 'v3_pass_start_spend.json')
    if os.path.exists(start_path):
        start = json.load(open(start_path))['conservative_usd']
    else:
        start = start_spend()
        json.dump({'token_spend_usd': A.token_spend(), 'server_delta_usd': A.server_delta(),
                   'conservative_usd': start, 'pass_cap_usd': PASS_CAP, 'stop_at_usd': A.STOP_AT},
                  open(start_path, 'w'), indent=1)
    llm = A.LLM(MODEL, A.RL(80))
    run_jobs(js, llm, start, stage, workers=int(env.get('PILOT_WORKERS', '5')), stage_cap=start + cap - start if False else cap)
    # stage_cap is new-spend cap for the stage, compared against (now-start) in run_jobs
    now = A.conservative_spend(A.token_spend(), A.server_delta())
    print('done', stage, 'token', round(A.token_spend(), 5), 'server', round(A._SS['v'], 5),
          'conservative', round(now, 5), 'new', round(now - start, 5))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--jobs-only':
        print('sanity', len(sanity_jobs()), 'fab', len(explore_fab_jobs()), 'dose', len(explore_dose_jobs()))
        json.dump({'sanity': sanity_jobs(), 'fab': explore_fab_jobs(), 'dose': explore_dose_jobs()},
                  open(os.path.join(HERE, 'v3_all_jobs.json'), 'w'), indent=1)
    else:
        main()
