#!/usr/bin/env bash
# Re-pilot (Amendment 2): C0, T1, T2, F1, U1 x n=2 seeds x 2 tasks x 2 flash models = 40 runs, with the manipulation check.
# DO NOT RUN until Bijin gives a written go. The guard below refuses to start without approvals/REPILOT_GO.txt.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s approvals/REPILOT_GO.txt ]; then
  echo "Refusing to run: approvals/REPILOT_GO.txt (Bijin's exact words and time) is missing." >&2; exit 1
fi
# Hard stop at $0.80 TOTAL (Bijin, 5:07 PM PT) pilot spend (cumulative ledger.json, max of token-priced and server /key/info delta).
PILOT_STOP_AT=0.80 PILOT_MAX_TOKENS=2000 PILOT_TRACES=traces_repilot PILOT_JOBS=repilot_jobs.json \
  python3 run_pilot.py 2>&1 | tee run_log_repilot.txt
python3 analyze_repilot.py | tee repilot_results.txt
