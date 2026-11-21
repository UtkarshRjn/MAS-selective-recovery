#!/usr/bin/env bash
# Amendment 8 smoke (80 jobs). Does not write MAIN_GO and does not start the main run.
# Requires approvals/SMOKE_A8_GO.txt and PILOT_SMOKE_ONLY=1.
# Temporary cumulative stop $3.00 (already spent about $0.96). The $19.75 hard stop still applies in code.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s approvals/SMOKE_A8_GO.txt ]; then
  echo "Refusing to run: approvals/SMOKE_A8_GO.txt is missing." >&2; exit 1
fi
if [ -s approvals/MAIN_GO.txt ]; then
  echo "Refusing to run smoke: MAIN_GO.txt is present. This script must not start the main run." >&2; exit 1
fi
if [ ! -s smoke_a8_jobs.json ]; then
  echo "Refusing to run: smoke_a8_jobs.json is missing." >&2; exit 1
fi
mkdir -p traces_smoke_a8
PILOT_STOP_AT=3.00 PILOT_MAX_TOKENS=4000 PILOT_TRACES=traces_smoke_a8 \
  PILOT_JOBS=smoke_a8_jobs.json PILOT_WORKERS=5 PILOT_SMOKE_ONLY=1 \
  python3 run_pilot.py 2>&1 | tee run_log_smoke_a8.txt
python3 analyze_smoke_a8.py | tee smoke_a8_results.txt
