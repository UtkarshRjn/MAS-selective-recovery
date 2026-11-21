"""Reviewer-mandated exploratory re-screen, November 21, 2026. Not confirmatory. Does not write MAIN_GO."""
import json, os, sys, time, threading, datetime
from concurrent.futures import ThreadPoolExecutor
import agent as A
import run_pilot as R
import heldout as H
import tasks as T

HERE = A.HERE
OUT = os.path.join(HERE, os.environ.get('PILOT_TRACES', 'traces_rescreen_nov21'))
os.makedirs(OUT, exist_ok=True)
MODEL = 'api-deepseek-4.1-flash'
NEW_CAP = 0.40
SETUPS = [
    ('P_comp', 'P_full', 'compress', 'PCM'),
    ('P_comp_flags', 'P_full', 'compress_keep_flags', 'PCF'),
    ('P_oracle', 'P_full', 'oracle', 'POR'),
    ('P_struct', 'P_full', 'structured', 'PST'),
]

def jobs_for(seeds, cells, tasks):
    js = []
    for seed in seeds:
        for cell in cells:
            for task in tasks:
                for name, arm, mode, tag in SETUPS:
                    js.append({
                        'model': MODEL, 'task': task, 'seed': seed, 'cell': cell,
                        'arm': arm, 'setup': name, 'handoff_mode': mode, 'tag': tag,
                        'self_review': False, 'confirm_rule': False,
                    })
    return js

def sanity_jobs():
    return jobs_for((0,), ('C0',), 'A')

def full_jobs():
    return jobs_for((0, 1), ('C0', 'F3', 'U1'), 'AB')

def path_of(j):
    name = '%s__%s__%d__%s__%s.json' % (j['model'], j['task'], j['seed'], j['cell'], j['tag'])
    return os.path.join(OUT, name)

def start_spend():
    return A.conservative_spend(A.token_spend(), A.server_delta())

def run_jobs(js, llm, start, label, workers):
    stop = threading.Event()
    todo = [j for j in js if not os.path.exists(path_of(j))]
    print('%s todo %d planned %d start_conservative=$%.5f stop=$%.2f new_cap=$%.2f' % (
        label, len(todo), len(js), start, A.STOP_AT, NEW_CAP), flush=True)

    def one(j):
        if stop.is_set():
            return None
        now = A.conservative_spend(A.token_spend(), A.server_delta(force=False))
        if now - start >= NEW_CAP or A.over_budget(A.token_spend(), A.server_delta(force=False), A.STOP_AT):
            stop.set()
            print('BUDGET STOP new=$%.5f cap=$%.2f conservative=$%.5f' % (now - start, NEW_CAP, now), flush=True)
            return None
        cell = H.make_any(j['task'], j['seed'], j['cell'])
        cell['regen_mem'] = H.make_any(j['task'], j['seed'], j['cell'])['mem']
        cell['request'] = T.TASKS[j['task']]['request'](cell['world'])
        cell['no_concerns'] = False
        cell['confirm_rule'] = False
        cell['mock_user'] = False
        cell['arm'] = j['arm']
        cell['handoff_mode'] = j['handoff_mode']
        cell['self_review'] = False
        t0 = time.time()
        tr = A.run_agent(cell, llm)
        tr['wall_s'] = round(time.time() - t0, 1)
        if tr.get('status') == 'budget_stop':
            stop.set()
            print('BUDGET STOP', flush=True)
            return None
        rec = dict(j)
        rec.update({'field': cell['field'], 'write_path': cell['write_path'],
                    'mem': cell['mem'], 'trusted': cell['trusted'], 'world': cell['world'],
                    'request': cell['request']})
        rec.update(tr)
        json.dump(rec, open(path_of(j), 'w'), indent=1, ensure_ascii=False)
        A.save_ledger()
        spend = A.log_cell_spend(MODEL, j['task'], j['seed'], j['cell'], tr['cost'])
        new = spend['conservative_spend_usd'] - start
        print('%s %s%s %s %s ended=%s valid=%s slots=%s reason=%s $%.5f %ss new=$%.4f conservative=$%.4f' % (
            j['setup'], j['task'], j['seed'], j['cell'], tr.get('status'),
            tr.get('ended_by'), tr.get('summary_valid'), tr.get('slots_complete'),
            tr.get('summary_invalid_reason'), tr.get('cost') or 0, tr.get('wall_s'),
            new, spend['conservative_spend_usd']), flush=True)
        if new >= NEW_CAP:
            stop.set()
        return rec

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))
    A.server_delta(force=True)
    A.save_ledger()
    return not stop.is_set()

def valid_of(j):
    p = path_of(j)
    if not os.path.exists(p):
        return False
    d = json.load(open(p))
    return d.get('summary_valid') is True and d.get('status') != 'budget_stop'

def main():
    if os.path.exists(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) and os.path.getsize(os.path.join(HERE, 'approvals', 'MAIN_GO.txt')) > 0:
        raise SystemExit('Refusing to run re-screen: MAIN_GO.txt is present. No API calls made.')
    env = os.environ
    sj = sanity_jobs()
    fj = full_jobs()
    json.dump({'sanity': sj, 'full': fj}, open(os.path.join(HERE, 'rescreen_nov21_jobs.json'), 'w'), indent=1)
    # authorize the full list; sanity is a subset
    R.require_go(stop_at=float(env.get('PILOT_STOP_AT', str(A.STOP_AT))), n_jobs=len(fj), env=env)
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    start = start_spend()
    open(os.path.join(HERE, 'rescreen_nov21_start_spend.json'), 'w').write(json.dumps({
        'token_spend_usd': A.token_spend(),
        'server_delta_usd': A.server_delta(),
        'conservative_usd': start,
        'new_cap_usd': NEW_CAP,
        'stop_at_usd': A.STOP_AT,
    }, indent=1))
    llm = A.LLM(MODEL, A.RL(80))
    ok = run_jobs(sj, llm, start, 'sanity', workers=int(env.get('PILOT_WORKERS', '4')))
    missing = [j for j in sj if not valid_of(j)]
    if missing:
        print('SANITY FAIL: not all 4 arms produced a valid summary. Stopping before the full re-screen.', flush=True)
        for j in missing:
            p = path_of(j)
            if os.path.exists(p):
                d = json.load(open(p))
                print(' fail', j['setup'], 'valid', d.get('summary_valid'), 'reason', d.get('summary_invalid_reason'),
                      'words', (d.get('summary_check') or {}).get('words'),
                      'raw_len', len(d.get('handoff_summary_raw') or d.get('handoff_summary') or ''), flush=True)
            else:
                print(' fail', j['setup'], 'no trace', flush=True)
        return 2
    print('SANITY PASS: all 4 arms produced a valid summary. Starting the remaining re-screen jobs.', flush=True)
    run_jobs(fj, llm, start, 'full', workers=int(env.get('PILOT_WORKERS', '5')))
    now = A.conservative_spend(A.token_spend(), A.server_delta())
    print('done token_spend', round(A.token_spend(), 5), 'server_delta', round(A._SS['v'], 5),
          'conservative', round(now, 5), 'new', round(now - start, 5))
    return 0

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--jobs-only':
        json.dump({'sanity': sanity_jobs(), 'full': full_jobs()},
                  open(os.path.join(HERE, 'rescreen_nov21_jobs.json'), 'w'), indent=1)
        print('sanity', len(sanity_jobs()), 'full', len(full_jobs()))
    else:
        sys.exit(main() or 0)
