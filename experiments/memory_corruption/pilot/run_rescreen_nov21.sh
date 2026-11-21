#!/usr/bin/env bash
# Reviewer-mandated exploratory re-screen. Does not write MAIN_GO.
# Requires approvals/RESCREEN_NOV21_GO.txt. New spend cap $0.40.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -s approvals/RESCREEN_NOV21_GO.txt ]; then
  echo "Refusing to run: approvals/RESCREEN_NOV21_GO.txt is missing. No API calls made." >&2; exit 1
fi
if [ -s approvals/MAIN_GO.txt ]; then
  echo "Refusing to run re-screen: MAIN_GO.txt is present." >&2; exit 1
fi
mkdir -p traces_rescreen_nov21
# Current conservative spend is about $2.273; $2.273 + $0.40 = $2.673.
PILOT_STOP_AT=2.67 PILOT_MAX_TOKENS=4000 PILOT_TRACES=traces_rescreen_nov21 \
  PILOT_WORKERS=5 PILOT_RESCREEN=1 \
  python3 run_rescreen_nov21.py 2>&1 | tee run_log_rescreen_nov21.txt
