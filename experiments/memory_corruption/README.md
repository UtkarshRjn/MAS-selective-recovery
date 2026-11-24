# Memory-damage study

A planner hands a trip booking or a file cleanup to an executor. The handoff has two channels that can disagree: a short summary, and a verbatim block of call arguments. The confirmatory question is what the executor does when both values are visible and both can be booked or deleted.

## Result

Primary cells only: 4 arms × 6 templates × seeds 16–21 = 144 runs per model. Numbers are copied from `pilot/confirm_gaps_v3_nov21_results.txt`.

- `api-deepseek-4.1-flash` asked or held on 141 of 144 runs and took 0 wrong actions. The other 3 runs are coded other.
- `api-glm-5.3-flash` asked or held on 134 of 144 runs. The other 10 followed the verbatim block, which produced 5 typo bookings, all on the arms where that block held the typo. 4 of the 5 were `trip_long` (ask rate 0.62); the fifth was `trip_return`.
- Neither model preferred the summary, the verbatim block, or whichever channel came last.
- The frozen ask-on-disagreement rule asked on 36 of 36 runs in each rule cell, on both models. Against the matched cells with no rule, that difference was not significant under Holm.

The pre-registered pattern, conflict-triggered asking, holds.

Two analysis corrections were made after the grid, and neither changes the decision. See `pilot/analysis_corrections_v3.md`. The results file reports zero-width multinomial-logit intervals as significant; those equations are not estimable (deepseek both equations, GLM follow_summary). The rule-secondary p-values come from an invalid z test; exact McNemar gives p_holm 1.0 (deepseek) and 0.5 (GLM).

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

## S0 screen (composition law and channel correlation)

A second, cheaper screen with no executor, only summarizer calls. Plan: `pilot/plan_s0_nov21.md` (v4). Freeze: `pilot/freeze_hashes_s0_v4.json`. Results: `pilot/s0_nov21_results.txt`, with predictions locked after depth 1 in `pilot/freeze_s0_predictions.json`. No kill rule fired. Spend was $0.59070 new. Offline checks: `python3 -m unittest test_s0_nov21` and `python3 run_s0_cost_watch.py --jobs-only`. A paid run goes only through `run_s0_cost_watch.py`.

## Handoff law (L1 and L2)

A third line asks a simple question: when one agent hands work to the next, how often does a wrong value survive, and does the true value get lost?

Each handoff text is in one of four states for the target value:

- V: the wrong value and the true value are both present (a visible conflict, so the agent can ask)
- S: only the wrong value is present (a silent error, so the agent acts on it)
- T: only the true value is present
- N: the value was dropped

**L1** measures single handoffs, predicts larger systems (chains, a diamond, trees), and then tests the predictions. Results: `pilot/law_l1_results.md`.

**L2** tests whether the loss of the true value depends on how much a handoff compresses its input. Plan: `pilot/plan_law_l2_draft.md`. Results: `pilot/law_l2_results.md`.

**L3** swaps which value is true, to separate role from truth. Main result: a summarizer keeps the value that the plan names and drops the value in the evidence, whichever one is true. Plan: `pilot/plan_law_l3_draft.md`. Results: `pilot/law_l3_results.md`.

Files:

| File | What it does |
|---|---|
| `pilot/harness.py` | Runs jobs: model calls, grading, parallel runs, resume, budget stop, paid-run checks |
| `pilot/law_model.py` | The math: 4x4 state matrices, chains, merges, trees, the snowball number R |
| `pilot/law_inputs.py` | L1 worlds, error types and handoff texts |
| `pilot/l2_inputs.py`, `pilot/l3_inputs.py` | L2 and L3 handoff texts at a set length |
| `pilot/law_run.py`, `pilot/l2_run.py`, `pilot/l3_run.py` | The L1, L2 and L3 experiments: grid, fit, predictions, scores |

Offline checks, from `pilot/`:

```bash
python3 -m unittest test_harness test_law test_l2 test_l3
```

```bash
python3 law_run.py --dry-run
```

```bash
python3 l2_run.py --dry-run
```

The freeze files (`freeze_hashes_law_l1.json`, `freeze_hashes_law_l2.json`) hash the code as it ran, in commit `462a76f`. Later commits reorganize that code without changing what it computes: the cleaned code gives the same prediction hashes and the same scores from the same traces.

## Handover

`HANDOVER.md` explains how to set up, run, and extend both lines, how API access and spend work, and what to ask before spending.
