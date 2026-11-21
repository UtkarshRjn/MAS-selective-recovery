"""Runs ONLY the pilot: 15 cells x 2 seeds x 2 tasks x 2 flash models = 120 runs. Stops at $0.90."""
import json, os, random, sys, threading, time, datetime
from concurrent.futures import ThreadPoolExecutor
import agent as A, heldout as H, damage as D, tasks as T

MODELS = ['api-deepseek-4.1-flash', 'api-glm-5.3-flash']
OUT = os.path.join(A.HERE, os.environ.get('PILOT_TRACES', 'traces')); os.makedirs(OUT, exist_ok=True)

def jobs(limit=None):
    if os.environ.get('PILOT_JOBS'): return [tuple(x) for x in json.load(open(os.environ['PILOT_JOBS']))]
    js = [(m, t, s, c) for s in (0, 1) for c in D.CELLS for t in 'AB' for m in MODELS]
    random.Random(7).shuffle(js); return js[:limit] if limit else js

def path(j): return os.path.join(OUT, '%s__%s__%d__%s.json' % (j[0], j[1], j[2], j[3]))

def main(limit=None, workers=8):
    if A._ledger['baseline_server_spend'] is None:
        A._ledger['baseline_server_spend'] = A.server_spend(); A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds'); A.save_ledger()
    rl = A.RL(80); llms = {m: A.LLM(m, rl) for m in MODELS}; stop = threading.Event()
    todo = [j for j in jobs(limit) if not os.path.exists(path(j))]
    print('todo', len(todo), flush=True)
    def one(j):
        if stop.is_set(): return
        m, t, s, c = j; cell = H.make_any(t, s, c); cell['request'] = T.TASKS[t]['request'](cell['world'])
        t0 = time.time(); tr = A.run_agent(cell, llms[m]); tr['wall_s'] = round(time.time() - t0, 1)
        if tr['status'] == 'budget_stop': stop.set(); print('BUDGET STOP', flush=True); return
        rec = {'model': m, 'task': t, 'seed': s, 'cell': c, 'field': cell['field'], 'write_path': cell['write_path'],
               'mem': cell['mem'], 'trusted': cell['trusted'], 'world': cell['world'], 'request': cell['request'], **tr}
        json.dump(rec, open(path(j), 'w'), indent=1, ensure_ascii=False); A.save_ledger()
        print(f"{m[4:12]} {t}{s} {c:4s} {tr['status']:15s} ended={tr['ended_by']} calls={tr['n_calls']} ${tr['cost']:.5f} {tr['wall_s']}s total=${A.token_spend():.4f}", flush=True)
    with ThreadPoolExecutor(workers) as ex: list(ex.map(one, todo))
    A.server_delta(force=True); A.save_ledger()
    print('token_spend', round(A.token_spend(), 5), 'server_delta', round(A._SS['v'], 5))

if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
