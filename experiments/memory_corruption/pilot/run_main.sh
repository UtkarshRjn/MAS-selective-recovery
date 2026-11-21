#!/usr/bin/env bash
# Workshop-tier main runs (Amendment 7): 2,360 jobs in main_jobs.json
# (1,680 G0 + no-concerns, plus 680 confirm-rule). Requires approvals/MAIN_GO.txt.
# Hard stop $19.75 cumulative. Restarts skip finished traces in traces_main/.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s approvals/MAIN_GO.txt ]; then
  echo "Refusing to run: approvals/MAIN_GO.txt is missing." >&2; exit 1
fi
if [ ! -s main_jobs.json ]; then
  echo "Refusing to run: main_jobs.json is missing." >&2; exit 1
fi
PILOT_STOP_AT=19.75 PILOT_MAX_TOKENS=4000 PILOT_TRACES=traces_main PILOT_JOBS=main_jobs.json PILOT_WORKERS=7 \
  python3 run_pilot.py 2>&1 | tee -a run_log_main.txt
