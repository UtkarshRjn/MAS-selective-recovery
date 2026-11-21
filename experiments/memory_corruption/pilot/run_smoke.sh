#!/usr/bin/env bash
# Smoke test of uncovered cells. Cumulative hard stop $1.00. Does not write MAIN_GO and does not start main.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s smoke_jobs.json ]; then
  echo "Refusing to run: smoke_jobs.json is missing." >&2; exit 1
fi
# Stop at $1.00 TOTAL cumulative spend (token ledger or server delta, whichever is higher).
# Already spent about $0.72, so headroom is about $0.28. Seed 0 jobs are first.
PILOT_STOP_AT=1.00 PILOT_MAX_TOKENS=4000 PILOT_TRACES=traces_smoke PILOT_JOBS=smoke_jobs.json PILOT_WORKERS=7 \
  python3 run_pilot.py 2>&1 | tee run_log_smoke.txt
python3 analyze_smoke.py | tee smoke_results.txt
