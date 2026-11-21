"""Pilot / workshop-tier runner. Hard stop is load_hard_stop() (env PILOT_STOP_AT, else run_config.json hard_stop_usd=19.75).
A hard stop above $1 requires approvals/MAIN_GO.txt. Logs spend per cell to cell_spend.jsonl.
Do not run the workshop tier until Bijin confirms the cap and MAIN_GO.txt is written."""
import json, os, random, sys, threading, time, datetime
from concurrent.futures import ThreadPoolExecutor
import agent as A, heldout as H, damage as D, tasks as T

MODELS = ['api-deepseek-4.1-flash', 'api-glm-5.3-flash']
OUT = os.path.join(A.HERE, os.environ.get('PILOT_TRACES', 'traces')); os.makedirs(OUT, exist_ok=True)

def workshop_grid():
    """Kept cells for the Amendment 6 workshop grid (C0, C2, F1-F4, T1, T2, U1)."""
    cfg = json.load(open(A.CONFIG_PATH))
    return cfg.get('workshop_grid') or {}

def kept_cells():
    g = workshop_grid()
    return set((g.get('g0_cells_n') or {}).keys())

def workshop_jobs():
    """Build the Amendment 6 G0 + no-concerns list: 1,480 G0 + 200 no-concerns = 1,680, ordered by seed.
    Each job is (model, task, seed, cell, no_concerns, confirm_rule)."""
    g = workshop_grid()
    g0_n = g['g0_cells_n']
    models = g.get('models') or MODELS
    order = [c for c in ('C0', 'C2', 'F1', 'F2', 'F3', 'F4', 'T1', 'T2', 'U1') if c in g0_n]
    js = []
    for s in range(max(g0_n.values())):
        for c in order:
            if s >= g0_n[c]: continue
            for t in g.get('tasks') or ['A', 'B']:
                for m in models:
                    js.append((m, t, s, c, False, False))
    nc = g.get('no_concerns') or {}
    nc_n = nc.get('cells_n') or {}
    for s in range(max(nc_n.values()) if nc_n else 0):
        for c in ('C0', 'F1', 'F2', 'F3', 'F4'):
            if c in nc_n and s < nc_n[c]:
                js.append((nc['model'], nc['task'], s, c, True, False))
    return js

def rule_jobs():
    """Amendment 7 confirm-rule arm: C0, C2, F3, U1, both models, both tasks, same n as G0. 680 jobs."""
    g = workshop_grid()
    rule = g.get('confirm_rule_arm') or {}
    nmap = rule.get('cells_n') or {'C0': 50, 'C2': 40, 'F3': 40, 'U1': 40}
    models = g.get('models') or MODELS
    tasks = g.get('tasks') or ['A', 'B']
    order = [c for c in ('C0', 'C2', 'F3', 'U1') if c in nmap]
    js = []
    for s in range(max(nmap.values())):
        for c in order:
            if s >= nmap[c]: continue
            for t in tasks:
                for m in models:
                    js.append((m, t, s, c, False, True))
    return js

def all_main_jobs():
    """Full workshop launch list: 1,680 G0+NC plus 680 confirm-rule = 2,360."""
    return workshop_jobs() + rule_jobs()

def mas_grid():
    cfg = json.load(open(A.CONFIG_PATH))
    return cfg.get('mas_grid') or {}

def mas_architecture_jobs():
    """Amendment 8 architecture grid: 4 arms x 4 cells x 2 tasks x 2 models x n=30 = 1,920."""
    g = mas_grid()
    n = int(g.get('n') or 30)
    models = g.get('models') or MODELS
    tasks = g.get('tasks') or ['A', 'B']
    cells = g.get('cells') or ['C0', 'F3', 'T1', 'U1']
    arms = g.get('arms') or ['S', 'P', 'P_V_plan', 'P_V_raw']
    js = []
    for s in range(n):
        for c in cells:
            for t in tasks:
                for m in models:
                    for arm in arms:
                        js.append((m, t, s, c, False, False, arm))
    return js

def mas_rule_jobs():
    """Amendment 8 confirm-rule arm on P-scoped only: C0 and U1, n=30, both tasks, both models = 240."""
    g = mas_grid()
    rule = g.get('confirm_rule_arm') or {}
    n = int(rule.get('n') or 30)
    models = g.get('models') or MODELS
    tasks = g.get('tasks') or ['A', 'B']
    cells = rule.get('cells') or ['C0', 'U1']
    arm = rule.get('arm') or 'P'
    js = []
    for s in range(n):
        for c in cells:
            for t in tasks:
                for m in models:
                    js.append((m, t, s, c, False, True, arm))
    return js

def all_mas_jobs():
    """Full Amendment 8 launch list: 1,920 + 240 = 2,160."""
    return mas_architecture_jobs() + mas_rule_jobs()

def smoke_a8_jobs():
    """Amendment 8 smoke: deepseek, both tasks, n=2 all arms and cells, extra P F3/T1 seeds 2-3, plus 8 rule jobs. 80 runs."""
    model = 'api-deepseek-4.1-flash'
    arms = ['S', 'P', 'P_V_plan', 'P_V_raw']
    cells = ['C0', 'F3', 'T1', 'U1']
    js = []
    for s in (0, 1):
        for c in cells:
            for t in 'AB':
                for arm in arms:
                    js.append((model, t, s, c, False, False, arm))
    for s in (2, 3):
        for c in ('F3', 'T1'):
            for t in 'AB':
                js.append((model, t, s, c, False, False, 'P'))
    for s in (0, 1):
        for c in ('C0', 'U1'):
            for t in 'AB':
                js.append((model, t, s, c, False, True, 'P'))
    return js

def smoke_a9_jobs():
    """Amendment 9 factorial smoke v2 (126 runs, no confirm-rule):
    8 arms x C0/F3/T1 x 2 tasks x n=2 (96);
    extra P_scoped F3, both tasks, seeds 2-4 (6) so the F3 floor has 10 planned runs;
    extra C0 for the four verifier arms, both tasks, seeds 2-3 (16);
    U1 for S_full and S_scoped only, both tasks, n=2 (8).
    Deepseek only."""
    model = 'api-deepseek-4.1-flash'
    arms = list(A.FACTOR_ARMS)
    cells = ['C0', 'F3', 'T1']
    js = []
    for s in (0, 1):
        for c in cells:
            for t in 'AB':
                for arm in arms:
                    js.append((model, t, s, c, False, False, arm))
    for s in (2, 3, 4):
        for t in 'AB':
            js.append((model, t, s, 'F3', False, False, 'P_scoped'))
    v_arms = ['P_full_Vplan', 'P_full_Vraw', 'P_scoped_Vplan', 'P_scoped_Vraw']
    for s in (2, 3):
        for t in 'AB':
            for arm in v_arms:
                js.append((model, t, s, 'C0', False, False, arm))
    for s in (0, 1):
        for t in 'AB':
            for arm in ('S_full', 'S_scoped'):
                js.append((model, t, s, 'U1', False, False, arm))
    return js

def _parse_job(x):
    """Accept [model, task, seed, cell] plus optional NC/RULE and an optional arm as a 7th field."""
    x = list(x)
    nc = False
    rule = False
    arm = None
    if len(x) >= 7:
        arm = x[6] if x[6] not in (None, '', False, 0, '0') else None
        rule = bool(x[5]) and x[5] not in (False, 0, '0', '', None)
        tag = x[4]
        nc = tag in (True, 'NC', 'nc', 'no_concerns', 'NO_CONCERNS', 1, '1')
        x = x[:4]
    elif len(x) >= 6:
        rule = bool(x[5]) and x[5] not in (False, 0, '0', '', None)
        tag = x[4]
        nc = tag in (True, 'NC', 'nc', 'no_concerns', 'NO_CONCERNS', 1, '1')
        x = x[:4]
    elif len(x) >= 5:
        tag = x[4]
        nc = tag in (True, 'NC', 'nc', 'no_concerns', 'NO_CONCERNS', 1, '1')
        rule = tag in ('RULE', 'rule', 'confirm_rule', 'CONFIRM_RULE', 'R')
        x = x[:4]
    out = (x[0], x[1], int(x[2]), x[3], nc, rule)
    if arm is not None:
        return out + (arm,)
    return out

def jobs(limit=None):
    if os.environ.get('PILOT_JOBS'):
        js = [_parse_job(x) for x in json.load(open(os.environ['PILOT_JOBS']))]
    elif os.environ.get('PILOT_MAS_GRID') == 'all':
        js = all_mas_jobs()
    elif os.environ.get('PILOT_A9_GRID') == 'smoke' or os.environ.get('PILOT_MAS_GRID') == 'smoke_a9':
        js = smoke_a9_jobs()
    elif os.environ.get('PILOT_MAS_GRID') == 'smoke':
        js = smoke_a8_jobs()
    elif os.environ.get('PILOT_WORKSHOP_GRID') == 'all':
        js = all_main_jobs()
    elif os.environ.get('PILOT_WORKSHOP_GRID'):
        js = workshop_jobs()
    else:
        js = [(m, t, s, c, False, False, None) for s in (0, 1) for c in D.CELLS for t in 'AB' for m in MODELS]
        random.Random(7).shuffle(js)
    if A.STOP_AT > 1.0:
        kept = kept_cells()
        bad = [j for j in js if j[3] not in kept]
        if bad:
            raise SystemExit('Refusing to start: %d jobs use cells cut in Amendment 6 (%s). No API calls made.' % (
                len(bad), ', '.join(sorted({j[3] for j in bad}))))
    return js[:limit] if limit else js

def _flags(j):
    """Normalize a job to (model, task, seed, cell, no_concerns, confirm_rule, arm)."""
    if len(j) >= 7:
        return j[0], j[1], int(j[2]), j[3], bool(j[4]), bool(j[5]), j[6]
    if len(j) >= 6:
        return j[0], j[1], int(j[2]), j[3], bool(j[4]), bool(j[5]), None
    if len(j) == 5:
        return j[0], j[1], int(j[2]), j[3], bool(j[4]), False, None
    return j[0], j[1], int(j[2]), j[3], False, False, None

def path(j):
    m, t, s, c, nc, rule, arm = _flags(j)
    name = '%s__%s__%d__%s' % (m, t, s, c)
    if arm:
        name += '__' + A.ARM_TAG.get(arm, str(arm))
    if nc: name += '__NC'
    if rule: name += '__RULE'
    return os.path.join(OUT, name + '.json')

MAIN_GO = os.path.join(A.HERE, 'approvals', 'MAIN_GO.txt')
SMOKE_A8_GO = os.path.join(A.HERE, 'approvals', 'SMOKE_A8_GO.txt')
SMOKE_A9_GO = os.path.join(A.HERE, 'approvals', 'SMOKE_A9_GO.txt')
SCREEN_NOV21_GO = os.path.join(A.HERE, 'approvals', 'SCREEN_NOV21_GO.txt')
RESCREEN_NOV21_GO = os.path.join(A.HERE, 'approvals', 'RESCREEN_NOV21_GO.txt')
V3_SANITY_GO = os.path.join(A.HERE, 'approvals', 'V3_SANITY_GO.txt')
V3_EXPLORE_GO = os.path.join(A.HERE, 'approvals', 'V3_EXPLORE_GO.txt')
GAPS_NOV21_GO = os.path.join(A.HERE, 'approvals', 'GAPS_NOV21_GO.txt')
GAPS_VERIFY_NOV21_GO = os.path.join(A.HERE, 'approvals', 'GAPS_VERIFY_NOV21_GO.txt')
GAPS_WORLDFIX_NOV21_GO = os.path.join(A.HERE, 'approvals', 'GAPS_WORLDFIX_NOV21_GO.txt')
V3_MAX_JOBS = 80
GAPS_MAX_JOBS = 120
GAPS_VERIFY_MAX_JOBS = 120
SMOKE_A8_MAX_JOBS = 180
SMOKE_MAX_JOBS = 180
SCREEN_MAX_JOBS = 120
RESCREEN_MAX_JOBS = 60

def _smoke_go_present():
    for path in (SMOKE_A8_GO, SMOKE_A9_GO):
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            return True
    return False

def require_go(stop_at=None, n_jobs=None, env=None):
    """Refuse a run whose hard stop is above the $1 pilot cap unless approvals/MAIN_GO.txt is present and non-empty.
    Smoke is the one exception: PILOT_SMOKE_ONLY=1, a non-empty SMOKE_A8_GO.txt or SMOKE_A9_GO.txt, and at most 180 jobs.
    That pair does not authorize the main grid."""
    env = os.environ if env is None else env
    stop_at = A.STOP_AT if stop_at is None else float(stop_at)
    if stop_at <= 1.0:
        return
    gapsfix = env.get('PILOT_GAPS_FIX') in ('1', 'true', 'True', 'yes')
    if gapsfix:
        if not (GAPS_WORLDFIX_NOV21_GO and os.path.exists(GAPS_WORLDFIX_NOV21_GO) and os.path.getsize(GAPS_WORLDFIX_NOV21_GO) > 0):
            raise SystemExit(
                'Refusing to start: PILOT_GAPS_FIX is set but approvals/GAPS_WORLDFIX_NOV21_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > GAPS_VERIFY_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_GAPS_FIX cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), GAPS_VERIFY_MAX_JOBS)
            )
        return
    gapsv = env.get('PILOT_GAPS_VERIFY') in ('1', 'true', 'True', 'yes')
    if gapsv:
        if not (GAPS_VERIFY_NOV21_GO and os.path.exists(GAPS_VERIFY_NOV21_GO) and os.path.getsize(GAPS_VERIFY_NOV21_GO) > 0):
            raise SystemExit(
                'Refusing to start: PILOT_GAPS_VERIFY is set but approvals/GAPS_VERIFY_NOV21_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > GAPS_VERIFY_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_GAPS_VERIFY cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), GAPS_VERIFY_MAX_JOBS)
            )
        return
    gaps = env.get('PILOT_GAPS') in ('1', 'true', 'True', 'yes')
    if gaps:
        if not (GAPS_NOV21_GO and os.path.exists(GAPS_NOV21_GO) and os.path.getsize(GAPS_NOV21_GO) > 0):
            raise SystemExit(
                'Refusing to start: PILOT_GAPS is set but approvals/GAPS_NOV21_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > GAPS_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_GAPS cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), GAPS_MAX_JOBS)
            )
        return
    v3go = env.get('PILOT_V3') in ('1', 'true', 'True', 'yes')
    if v3go:
        go = V3_SANITY_GO if env.get('PILOT_V3_STAGE','sanity')=='sanity' else V3_EXPLORE_GO
        # Either GO file authorizes v3 exploratory work.
        ok = False
        for path in (V3_SANITY_GO, V3_EXPLORE_GO):
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                ok = True
        if not ok:
            raise SystemExit(
                'Refusing to start: PILOT_V3 is set but approvals/V3_SANITY_GO.txt or V3_EXPLORE_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > V3_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_V3 cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), V3_MAX_JOBS)
            )
        return
    rescreen = env.get('PILOT_RESCREEN') in ('1', 'true', 'True', 'yes')
    if rescreen:
        if not (RESCREEN_NOV21_GO and os.path.exists(RESCREEN_NOV21_GO) and os.path.getsize(RESCREEN_NOV21_GO) > 0):
            raise SystemExit(
                'Refusing to start: PILOT_RESCREEN is set but approvals/RESCREEN_NOV21_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > RESCREEN_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_RESCREEN cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), RESCREEN_MAX_JOBS)
            )
        return
    screen = env.get('PILOT_SCREEN') in ('1', 'true', 'True', 'yes')
    if screen:
        if not (SCREEN_NOV21_GO and os.path.exists(SCREEN_NOV21_GO) and os.path.getsize(SCREEN_NOV21_GO) > 0):
            raise SystemExit(
                'Refusing to start: PILOT_SCREEN is set but approvals/SCREEN_NOV21_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > SCREEN_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_SCREEN cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), SCREEN_MAX_JOBS)
            )
        return
    smoke = env.get('PILOT_SMOKE_ONLY') in ('1', 'true', 'True', 'yes')
    if smoke:
        if not _smoke_go_present():
            raise SystemExit(
                'Refusing to start: PILOT_SMOKE_ONLY is set but approvals/SMOKE_A8_GO.txt or SMOKE_A9_GO.txt is missing or empty. No API calls made.'
            )
        if n_jobs is not None and int(n_jobs) > SMOKE_A8_MAX_JOBS:
            raise SystemExit(
                'Refusing to start: PILOT_SMOKE_ONLY cannot run %d jobs (max %d). No API calls made.' % (int(n_jobs), SMOKE_A8_MAX_JOBS)
            )
        return
    if not os.path.exists(MAIN_GO) or os.path.getsize(MAIN_GO) == 0:
        raise SystemExit(
            'Refusing to start: hard stop is $%.2f and approvals/MAIN_GO.txt is missing or empty. No API calls made.' % stop_at
        )

def main(limit=None, workers=None):
    if workers is None:
        workers = int(os.environ.get('PILOT_WORKERS', '7'))
    # Parse jobs first so a smoke-only run can be size-checked before any API call.
    all_jobs = jobs(limit)
    require_go(n_jobs=len(all_jobs))
    if A._ledger['baseline_server_spend'] is None:
        A._ledger['baseline_server_spend'] = A.server_spend(); A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds'); A.save_ledger()
    rl = A.RL(80); llms = {m: A.LLM(m, rl) for m in MODELS}; stop = threading.Event()
    todo = [j for j in all_jobs if not os.path.exists(path(j))]
    planned = len(all_jobs)
    smoke = os.environ.get('PILOT_SMOKE_ONLY') in ('1', 'true', 'True', 'yes')
    if smoke:
        halt_mode = 'smoke'
    elif A.STOP_AT > 1.0:
        halt_mode = 'main'
    else:
        halt_mode = 'off'
    counts = {'retry_runs': 0, 'failed_retries': 0, 'done': 0}
    cl = threading.Lock()
    print('todo', len(todo), 'planned', planned, 'length_retry_halt', halt_mode, 'max_tokens', A.DEFAULT_MAX_TOKENS, flush=True)
    def one(j):
        if stop.is_set(): return
        m, t, s, c, nc, rule, arm = _flags(j)
        cell = H.make_any(t, s, c); cell['regen_mem'] = H.make_any(t, s, c)['mem']; cell['request'] = T.TASKS[t]['request'](cell['world'])
        cell['no_concerns'] = bool(nc)
        cell['confirm_rule'] = bool(rule)
        if arm:
            cell['arm'] = arm
        # Amendment 9 planner-from-memory-only design. Default remains a8 so Amendment 8 tests stay valid.
        if os.environ.get('PILOT_DESIGN'):
            cell['design'] = os.environ['PILOT_DESIGN']
        elif os.environ.get('PILOT_A9_GRID') in ('1', 'true', 'True', 'yes', 'all', 'smoke'):
            cell['design'] = 'a9'
        # Mock user: Amendment 7 workshop launch (no arm, hard stop above $1) or any confirm-rule job.
        # Amendment 8 architecture arms leave the mock user off unless the job is on the confirm-rule arm.
        if 'PILOT_MOCK_USER' in os.environ:
            cell['mock_user'] = os.environ['PILOT_MOCK_USER'] not in ('0', 'false', 'False', '')
        elif arm:
            cell['mock_user'] = bool(rule)
        else:
            cell['mock_user'] = bool(rule) or A.STOP_AT > 1.0
        t0 = time.time(); tr = A.run_agent(cell, llms[m]); tr['wall_s'] = round(time.time() - t0, 1)
        if tr['status'] == 'budget_stop': stop.set(); print('BUDGET STOP', flush=True); return
        rec = {'model': m, 'task': t, 'seed': s, 'cell': c, 'no_concerns': bool(nc), 'confirm_rule': bool(rule),
               'arm': arm, 'field': cell['field'], 'write_path': cell['write_path'],
               'mem': cell['mem'], 'trusted': cell['trusted'], 'world': cell['world'], 'request': cell['request'], **tr}
        json.dump(rec, open(path(j), 'w'), indent=1, ensure_ascii=False); A.save_ledger()
        spend = A.log_cell_spend(m, t, s, c, tr['cost'])
        tag = c + ('/NC' if nc else '') + (('/' + (A.ARM_TAG.get(arm, arm) if arm else '')) if arm else '') + ('/RULE' if rule else '')
        nret = int(tr.get('n_length_retries') or 0)
        failed = bool(tr.get('length_retry_failed') or tr.get('status') == 'harness_error')
        print(f"{m[4:12]} {t}{s} {tag:7s} {tr['status']:15s} ended={tr['ended_by']} calls={tr['n_calls']} ${tr['cost']:.5f} {tr['wall_s']}s cell_spend=${spend['cost']:.5f} conservative=${spend['conservative_spend_usd']:.4f} stop=${spend['hard_stop_usd']:.2f} length_retries={nret}", flush=True)
        with cl:
            if nret: counts['retry_runs'] += 1
            if failed: counts['failed_retries'] += 1
            counts['done'] += 1
            halt, why = A.retry_halt(counts['retry_runs'], counts['failed_retries'], planned, mode=halt_mode)
            if halt:
                stop.set(); print('RETRY HALT:', why, flush=True)
    with ThreadPoolExecutor(workers) as ex: list(ex.map(one, todo))
    A.server_delta(force=True); A.save_ledger()
    print('token_spend', round(A.token_spend(), 5), 'server_delta', round(A._SS['v'], 5))

if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
