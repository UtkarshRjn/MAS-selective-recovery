# Memory-damage study

A planner hands a trip booking or a file cleanup to an executor. The handoff has two channels that can disagree: a short summary, and a verbatim block of call arguments. The confirmatory question is what the executor does when both values are visible and both can be booked or deleted.

## Result

Primary cells only: 4 arms × 6 templates × seeds 16–21 = 144 runs per model. Numbers are copied from `pilot/confirm_gaps_v3_nov21_results.txt`.

- `api-deepseek-4.1-flash` asked or held on 141 of 144 runs and took 0 wrong actions. The other 3 runs are coded other.
- `api-glm-5.3-flash` asked or held on 134 of 144 runs. The other 10 followed the verbatim block, which produced 5 typo bookings, all on the arms where that block held the typo. 4 of the 5 were `trip_long` (ask rate 0.62); the fifth was `trip_return`.
- Neither model preferred the summary, the verbatim block, or whichever channel came last.
- The frozen ask-on-disagreement rule asked on 36 of 36 runs in each rule cell, on both models. Against the matched cells with no rule, that difference was not significant under Holm.

The pre-registered pattern, conflict-triggered asking, holds.

## Frozen plan

The final plan is `pilot/plan_gaps_confirm_nov21_v3.md`. Its freeze hashes are `pilot/freeze_hashes_gaps_confirm_v3.json`. `pilot/addendum_v3_nov21.md` is the analysis addendum written before the grid; the results file records its hash. `pilot/forking_paths_nov21.md` records the earlier designs this plan replaced. `pilot/freeze_hashes_v3.json` is an earlier measurement freeze that the unit tests still check.

## How to rerun offline

From `experiments/memory_corruption/pilot`. These commands do not call a model and do not need a key.

```bash
python3 -m unittest test_conflict_v3_nov21.py test_v3.py
python3 test_gates_c0.py
python3 run_confirm_v3_nov21.py --jobs-only
```

`--jobs-only` prints the 432-job grid and returns. `test_gates_c0.py` is a script, not a unittest module.

Memory text is built from the small filler corpus in `pilot/filler` (see `sources.csv`). Set `PILOT_FILLER_DIR` to point that corpus somewhere else.

`pilot/dry_run_conflict_v3.py` rewrites a local text dump, `dry_run_conflict_v3.txt`. That dump is gitignored.

## A live grid

`python3 run_confirm_v3_nov21.py` with no flag calls the model API. Set `OPENAI_API_KEY` and `OPENAI_BASE_URL` in the environment, or point `PILOT_ENV_FILE` at a dotenv file. The default file is `pilot/.env`, which is gitignored. Do not commit it. `PILOT_STOP_AT` overrides the hard stop in `pilot/run_config.json`. A stop above 1 requires a non-empty `pilot/approvals/MAIN_GO.txt`, which is not in this tree. `--analyze-only` re-scores traces and also queries account spend, so it is not an offline command.

Traces are written under `pilot/traces_confirm_gaps_v3_nov21/` and are gitignored.

## Where the results are

- `pilot/confirm_gaps_v3_nov21_results.txt`
- `pilot/confirm_gaps_v3_nov21_results.json`
- `pilot/confirm_gaps_v3_nov21_hand_audit.json` (43 of 432 traces sampled, 0 mismatches)
