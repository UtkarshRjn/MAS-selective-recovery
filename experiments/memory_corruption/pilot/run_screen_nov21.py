"""Exploratory November 21 screening. Not confirmatory. Does not write MAIN_GO."""
import json, os, sys, time, threading, datetime
from concurrent.futures import ThreadPoolExecutor
import agent as A
import run_pilot as R
import heldout as H
import tasks as T
import analyze as AN

HERE = A.HERE
OUT = os.path.join(HERE, os.environ.get('PILOT_TRACES', 'traces_screen_nov21'))
os.makedirs(OUT, exist_ok=True)
MODEL = 'api-deepseek-4.1-flash'
SETUPS = [
    # name, arm, handoff_mode, self_review, tag
    ('S_full', 'S_full', 'short', False, 'SF'),
    ('S_match', 'S_full', 'short', True, 'SR'),
    ('P_short', 'P_full', 'short', False, 'PSH'),
    ('P_long', 'P_full', 'long', False, 'PLN'),
    ('P_comp', 'P_full', 'compress', False, 'PCM'),
    ('P_comp_flags', 'P_full', 'compress_keep_flags', False, 'PCF'),
]

def screen_jobs():
    js = []
    for seed in (0, 1):
        for cell in ('C0', 'F3', 'U1'):
            for task in 'AB':
                for name, arm, mode, review, tag in SETUPS:
                    js.append({
                        'model': MODEL, 'task': task, 'seed': seed, 'cell': cell,
                        'arm': arm, 'setup': name, 'handoff_mode': mode,
                        'self_review': review, 'tag': tag, 'confirm_rule': False,
                    })
    return js

def path_of(j):
    name = '%s__%s__%d__%s__%s.json' % (j['model'], j['task'], j['seed'], j['cell'], j['tag'])
    return os.path.join(OUT, name)

def main():
    js = screen_jobs()
    json.dump(js, open(os.path.join(HERE, 'screen_nov21_jobs.json'), 'w'), indent=1)
    env = os.environ
    R.require_go(stop_at=float(env.get('PILOT_STOP_AT', '5.00')), n_jobs=len(js), env=env)
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    workers = int(env.get('PILOT_WORKERS', '5'))
    llm = A.LLM(MODEL, A.RL(80))
    todo = [j for j in js if not os.path.exists(path_of(j))]
    print('todo', len(todo), 'planned', len(js), 'stop', A.STOP_AT, 'traces', OUT, flush=True)
    stop = threading.Event()
    def one(j):
        if stop.is_set():
            return
        cell = H.make_any(j['task'], j['seed'], j['cell'])
        cell['regen_mem'] = H.make_any(j['task'], j['seed'], j['cell'])['mem']
        cell['request'] = T.TASKS[j['task']]['request'](cell['world'])
        cell['no_concerns'] = False
        cell['confirm_rule'] = False
        cell['mock_user'] = False
        cell['arm'] = j['arm']
        cell['handoff_mode'] = j['handoff_mode']
        cell['self_review'] = j['self_review']
        t0 = time.time()
        tr = A.run_agent(cell, llm)
        tr['wall_s'] = round(time.time() - t0, 1)
        if tr.get('status') == 'budget_stop':
            stop.set()
            print('BUDGET STOP', flush=True)
            return
        rec = dict(j)
        rec.update({'field': cell['field'], 'write_path': cell['write_path'],
                    'mem': cell['mem'], 'trusted': cell['trusted'], 'world': cell['world'],
                    'request': cell['request']})
        rec.update(tr)
        json.dump(rec, open(path_of(j), 'w'), indent=1, ensure_ascii=False)
        A.save_ledger()
        spend = A.log_cell_spend(MODEL, j['task'], j['seed'], j['cell'], tr['cost'])
        print('%s %s%s %s %s ended=%s $%.5f %ss conservative=$%.4f stop=$%.2f' % (
            j['setup'], j['task'], j['seed'], j['cell'], tr.get('status'),
            tr.get('ended_by'), tr.get('cost') or 0, tr.get('wall_s'),
            spend['conservative_spend_usd'], spend['hard_stop_usd']), flush=True)
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))
    A.server_delta(force=True)
    A.save_ledger()
    print('token_spend', round(A.token_spend(), 5), 'server_delta', round(A._SS['v'], 5))

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--jobs-only':
        js = screen_jobs()
        json.dump(js, open(os.path.join(HERE, 'screen_nov21_jobs.json'), 'w'), indent=1)
        print(len(js))
    else:
        main()
