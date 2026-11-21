#!/usr/bin/env bash
# Amendment 9 factorial smoke v2 (126 jobs). Does not write MAIN_GO.
# Requires approvals/SMOKE_A9_GO.txt. Temporary cumulative stop $3.00.
# The $19.75 hard stop still applies in code. Access is encoded in the arm name;
# do not set PILOT_DESIGN=a9, which would not apply to factorial arm names anyway
# but would confuse a mixed legacy list.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s approvals/SMOKE_A9_GO.txt ]; then
  echo "Refusing to run: approvals/SMOKE_A9_GO.txt is missing. No API calls made." >&2; exit 1
fi
if [ -s approvals/MAIN_GO.txt ]; then
  echo "Refusing to run smoke: MAIN_GO.txt is present. This script must not start the main run." >&2; exit 1
fi
if [ ! -s smoke_a9_jobs.json ]; then
  echo "Refusing to run: smoke_a9_jobs.json is missing." >&2; exit 1
fi
mkdir -p traces_smoke_a9
PILOT_STOP_AT=3.00 PILOT_MAX_TOKENS=4000 PILOT_TRACES=traces_smoke_a9 \
  PILOT_JOBS=smoke_a9_jobs.json PILOT_WORKERS=5 PILOT_SMOKE_ONLY=1 \
  python3 run_pilot.py 2>&1 | tee run_log_smoke_a9.txt
