"""Shared harness for the handoff-law experiments (law_run.py = L1, l2_run.py = L2).

This module knows how to run jobs. It does not know what an experiment measures.
- complete():   one model call (a real client or a mock)
- grade():      the state of the target value in a text (V, S, T or N)
- run_jobs():   run many jobs in parallel, resume from disk, stop cleanly at the budget
- PaidSession:  every check and every piece of spend tracking that a paid run needs
- MockLLM:      a deterministic stand-in model for offline dry runs
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import law_inputs as LI
import law_model as LM
import s0_grade as G

MODELS = ('api-deepseek-4.1-flash', 'api-glm-5.3-flash')
TEMPERATURE = 0.7          # above 0, so a chain is not one model's fixed point
MAX_TOKENS = 4000
REQUESTS_PER_MINUTE = 80   # same limit as the S0 runner
PROGRESS_EVERY = 50        # save the ledger and print spend after this many jobs


# ---------------------------------------------------------------- one call and one grade

def seed_of(*parts):
    """A stable API seed from any parts (for example a job key and a hop index)."""
    return int(hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:8], 16) % 1_000_000


def complete(llm, messages, seed):
    """Plain-text completion. Returns (text, meta). A mock never imports the API client."""
    if hasattr(llm, 'complete_s0'):
        raw, meta = llm.complete_s0(messages, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, seed=seed)
    else:
        import s0_summarize as SS
        raw, meta = SS.complete_s0(llm, messages, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                                   seed=seed, json_object=False)
    return (raw or '').strip(), meta


def grade(cell, text):
    """(state, raw_state): state is V, S, T, N, or None for an invalid text."""
    g = G.code_text(text, cell['gap_true'], cell['gap_neighbor'], G.known_ids_of(cell), cell=cell)
    return LM.state_of_grade(g), g.get('state')


class MockLLM:
    """Deterministic stand-in: keeps or drops each sentence by a hash of (model, seed, sentence)."""

    def __init__(self, model, keep=0.75):
        self.model, self.keep = model, keep

    def complete_s0(self, messages, max_tokens=MAX_TOKENS, temperature=0.0, seed=0, json_object=False):
        text = LI.strip_pin(messages[-1]['content'].split('\n\n', 1)[-1])
        sentences = [s.strip() for s in text.replace('\n', ' ').split('. ') if s.strip()]
        kept = [s for s in sentences if self._keeps(seed, s)]
        out = ('. '.join(kept[:8]) or 'Book the trip as requested.').replace('{', '(').replace('}', ')')
        return out, {'tin': 0, 'tout': 0, 'cost': 0.0, 'finish': 'stop', 'mocked': True}

    def _keeps(self, seed, sentence):
        h = hashlib.md5(('%s|%s|%s' % (self.model, seed, sentence)).encode()).hexdigest()
        return int(h[:4], 16) / 65535 < self.keep


def mocks(models):
    """One mock per model, each with a different keep rate, so the dry run is not trivial."""
    return {m: MockLLM(m, keep=0.6 + 0.1 * i) for i, m in enumerate(models)}


# ---------------------------------------------------------------- running many jobs

@dataclass
class RunReport:
    done: int
    planned: int
    failed: int
    stopped: str | None


def run_jobs(jobs, run_one, key_of, out_dir, write=True, workers=1, on_done=None, stop_on=()):
    """Run each job with run_one(job), in parallel, and return (rows, RunReport).

    - Resume: a job whose trace file already exists is loaded, not run again.
    - Stop: an exception in stop_on (the budget stop) ends new work; finished traces stay.
    - Failure: any other exception is counted and printed; that job runs again next time.
    """
    if write:
        os.makedirs(out_dir, exist_ok=True)
    rows, todo = [], []
    for j in jobs:
        path = os.path.join(out_dir, key_of(j) + '.json')
        if write and os.path.exists(path):
            with open(path) as f:
                rows.append(json.load(f))
        else:
            todo.append((j, path))
    lock = threading.Lock()
    state = {'stopped': None, 'failed': 0}

    def one(item):
        j, path = item
        if state['stopped']:
            return
        try:
            trace = run_one(j)
        except stop_on as e:
            state['stopped'] = '%s: %s' % (type(e).__name__, e)
            return
        except Exception as e:  # one bad job must not end the grid
            with lock:
                state['failed'] += 1
            print('job failed %s: %s' % (key_of(j), type(e).__name__), flush=True)
            return
        if write:
            with open(path, 'w') as f:
                json.dump(trace, f, indent=1)
        with lock:
            rows.append(trace)
            if on_done:
                on_done(len(rows), len(jobs))

    if workers <= 1:
        for item in todo:
            one(item)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
    return rows, RunReport(len(rows), len(jobs), state['failed'], state['stopped'])


def load_traces(out_dir, prefix):
    """Every trace in out_dir whose file name starts with prefix + '__', in name order."""
    rows = []
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            if name.startswith(prefix + '__') and name.endswith('.json'):
                with open(os.path.join(out_dir, name)) as f:
                    rows.append(json.load(f))
    return rows


def write_predictions(path, preds):
    """Write predictions as sorted JSON and return its sha256 (recorded before the test stage)."""
    blob = json.dumps(preds, indent=1, sort_keys=True)
    with open(path, 'w') as f:
        f.write(blob)
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------- paid runs

class PaidSession:
    """All checks and spend tracking for a paid run.

    It refuses to start without a non-empty go file, without PILOT_STOP_AT, or with a model
    that has no price. It records the server spend baseline, gives one shared client per model,
    saves the ledger every PROGRESS_EVERY jobs, and exposes the budget exception to stop on.
    """

    def __init__(self, go_file, models):
        if not (os.path.exists(go_file) and open(go_file).read().strip()):
            sys.exit('Refusing a paid run: %s is missing or empty.' % go_file)
        if not os.environ.get('PILOT_STOP_AT'):
            sys.exit('Refusing a paid run: set PILOT_STOP_AT to the cap given in writing.')
        import agent as A
        self.A = A
        unknown = [m for m in models if m not in A.PRICE]
        if unknown:
            sys.exit('Refusing a paid run: no price for %s in agent.PRICE.' % unknown)
        print('hard stop (PILOT_STOP_AT) $%.2f on conservative spend' % A.STOP_AT, flush=True)
        if A._ledger.get('baseline_server_spend') is None:
            A._ledger['baseline_server_spend'] = A.server_spend()
            A.save_ledger()
        self._rl = A.RL(REQUESTS_PER_MINUTE)
        self._llms = {}
        self._lock = threading.Lock()
        self.stop_on = (A.Budget,)

    def llm_for(self, model):
        with self._lock:
            if model not in self._llms:
                self._llms[model] = self.A.LLM(model, self._rl)
            return self._llms[model]

    def spend(self, force=False):
        A = self.A
        return A.conservative_spend(A.token_spend(), A.server_delta(force=force))

    def progress(self, stage):
        def on_done(k, n):
            if k % PROGRESS_EVERY == 0 or k == n:
                self.A.save_ledger()
                print('%s %d/%d  conservative spend $%.4f' % (stage, k, n, self.spend()), flush=True)
        return on_done

    def finish(self, stage, report, out_dir):
        self.A.save_ledger()
        print('stage %s: %d of %d jobs done, %d failed, stopped=%s, conservative spend $%.4f, traces in %s'
              % (stage, report.done, report.planned, report.failed, report.stopped,
                 self.spend(force=True), out_dir))
