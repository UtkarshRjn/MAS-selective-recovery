"""S0 v2 paid-run wrapper: after depth 1, stop if the projected total is above $2.80.

Does not authorize spend by itself. Requires approvals/MAIN_GO_s0.txt.
Older MAIN_GO.txt and MAIN_GO_v3.txt do not authorize this screen.
Key is loaded only from /workspace/war_room/e1/.env by agent.LLM, never printed or copied.
Do not launch run_summary_chain.py directly for the paid grid.
"""
from __future__ import annotations

import json
import os
import sys
import datetime

import _core  # noqa: F401  (puts core/ on the import path)
import agent as A
import run_summary_chain as R

PROJ_CAP = 2.80
REPORT_PATH = os.path.join(R.HERE, 'results', 's0_cost_watch_report.json')
PLANNED_CALLS = R.expected_counts()['total_calls']  # 592


def project_total(new_spend, n_calls_done, n_calls_planned=PLANNED_CALLS):
    """Linear projection of total new spend from calls already completed."""
    if n_calls_done is None or n_calls_done <= 0:
        return None
    return (float(new_spend) / float(n_calls_done)) * float(n_calls_planned)


def count_calls(rows, stage=None):
    n = 0
    for r in rows:
        if stage and r.get('stage') != stage:
            continue
        hops = r.get('hops') or []
        # Count actual summarizer hops that were attempted, including invalid ones that made a call.
        # Skipped follow-on hops after an invalid hop have invalid_reason prev_hop_invalid and cost 0.
        for h in hops:
            if h.get('invalid_reason') == 'prev_hop_invalid' and not h.get('text'):
                continue
            n += 1
        if not hops and (r.get('cost') or 0) > 0:
            n += 1
    return n


def write_report(payload, path=REPORT_PATH):
    with open(path, 'w') as f:
        json.dump(R._jsonable(payload), f, indent=1)
    return path


def main():
    js = R.build_jobs()
    counts = R.expected_counts()
    if len(sys.argv) > 1 and sys.argv[1] in ('--jobs-only', '--analyze-only', '--lock-predictions'):
        # Delegate zero-cost utilities; they do not spend.
        return R.main()

    R.require_s0_go()
    start = R.start_spend()
    if A._ledger.get('baseline_server_spend') is None:
        A._ledger['baseline_server_spend'] = A.server_spend()
        A._ledger['baseline_time_local'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        A.save_ledger()
    if not os.path.exists(R.START_SPEND_PATH):
        json.dump({
            'start': start, 'hard_cap': R.HARD_CAP, 'soft_cap': R.SOFT_CAP,
            'proj_cap': PROJ_CAP, 'cum_stop': R.CUM_STOP,
            'expected_usd': R.EXPECTED_USD, 'wrapper': 'run_s0_cost_watch.py',
            'models': list(R.MODELS),
        }, open(R.START_SPEND_PATH, 'w'), indent=1)
    else:
        start = json.load(open(R.START_SPEND_PATH))['start']
    rl = A.RL(80)
    llms = {m: A.LLM(m, rl) for m in R.MODELS}
    workers = int(os.environ.get('PILOT_WORKERS', '8'))
    print('S0 v2 paid run via cost-watch wrapper start=$%.5f proj_cap=$%.2f hard=$%.2f jobs=%d' % (
        start, PROJ_CAP, R.HARD_CAP, len(js)), flush=True)

    all_ok = True
    stopped_for_projection = False
    projection = None
    for stage in R.stage_order():
        chunk = R.jobs_in_stage(js, stage)
        now = R.start_spend()
        if now - start >= R.HARD_CAP or now >= R.CUM_STOP:
            print('BUDGET STOP before stage', stage, flush=True)
            all_ok = False
            break
        print('stage', stage, 'jobs', len(chunk), 'new=$%.4f' % (now - start), flush=True)
        ok = R.run_jobs(chunk, llms, start, workers)
        if stage == 'compose_d1':
            rows = R.load_traces()
            pred, wrote = R.lock_predictions(rows)
            print('locked predictions' if wrote else 'predictions already locked', R.PRED_PATH, flush=True)
            now = R.start_spend()
            new = now - start
            n_calls = count_calls(rows, stage='compose_d1')
            projection = project_total(new, n_calls, counts['total_calls'])
            print('COST WATCH after step 1: new=$%.5f calls=%d projected_total=%s cap=$%.2f' % (
                new, n_calls, 'NA' if projection is None else '$%.5f' % projection, PROJ_CAP), flush=True)
            if projection is not None and projection > PROJ_CAP:
                stopped_for_projection = True
                all_ok = False
                print(
                    'COST WATCH STOP: projected total $%.5f is above $%.2f. '
                    'Later stages, including depth 4, were not started. '
                    'This is not a silent drop of depth 4.' % (projection, PROJ_CAP),
                    flush=True,
                )
                break
        if not ok:
            all_ok = False
            break

    now = R.start_spend()
    rows = R.load_traces()
    pred = json.load(open(R.PRED_PATH)) if os.path.exists(R.PRED_PATH) else None
    text, payload = R.analyze(rows, start, now, predictions=pred, complete=(len(rows) >= counts['total_jobs'] and all_ok))
    payload['cost_watch'] = {
        'wrapper': 'run_s0_cost_watch.py',
        'proj_cap': PROJ_CAP,
        'projection_after_step1': projection,
        'stopped_for_projection': stopped_for_projection,
        'real_final_new_spend': None if start is None else now - start,
        'real_final_cumulative': now,
        'note': 'Report the real final spend. In-flight workers can overshoot a cap by a few cents.',
    }
    extra = '\nCost watch: projection_after_step1=%s stopped_for_projection=%s real_final_new=$%.5f cumulative=$%.5f\n' % (
        'NA' if projection is None else '%.5f' % projection,
        stopped_for_projection,
        now - start, now,
    )
    open(R.RESULTS_TXT, 'w').write(text + extra)
    json.dump(R._jsonable(payload), open(R.RESULTS_JSON, 'w'), indent=1)
    write_report(payload['cost_watch'])
    print(text, flush=True)
    print(extra, flush=True)
    print('wrote', R.RESULTS_TXT, R.RESULTS_JSON, REPORT_PATH, flush=True)


if __name__ == '__main__':
    main()
