# Memory-damage study

This folder is the full memory-damage / multi-agent conflict study. Agents hand a trip or a file-cleanup job from a planner to an executor. The handoff has two channels that can disagree: a short summary, and a verbatim block of call arguments. The question is what the executor does when both values are visible and both can be booked or deleted: follow one channel, or stop and ask.

Plans, the runnable pilot, frozen hashes, approvals, traces, and hand audits are kept here. The confirmatory grid is the v3 reversed-conflict result in `pilot/`.

## Headline (v3 confirmatory grid)

Source: `pilot/confirm_gaps_v3_nov21_results.txt` and `pilot/confirm_gaps_v3_nov21_results.json`. Primary cells only (4 arms × 6 templates × 6 fresh seeds = 144 runs per model). The pre-registered pattern is conflict-triggered asking, and that pattern holds.

- Deepseek (`api-deepseek-4.1-flash`) asked or held in 141 of 144 runs and took 0 wrong actions. The other 3 runs are coded `other`, not wrong actions.
- GLM (`api-glm-5.3-flash`) asked or held in 134 of 144 runs. The other 10 runs all followed the verbatim block. That produced 5 typo bookings, and only on the arms where the verbatim block held the typo. 4 of those 5 were on `trip_long` (ask rate 0.62 there); the fifth was on `trip_return`.
- Neither model showed a channel preference (summary vs verbatim) or a recency preference. Those bars were not met (`verbatim_holds`, `summary_holds`, and `recency_holds` are all false).
- The frozen ask-on-disagreement rule asked in 36 of 36 runs in each rule cell on both models. Against the matched cells with no rule, that difference was not significant under Holm (`reject_05` is false on all four tests).

## Pre-registration and forking paths

The pilot pre-registration is `pilot/prereg_2026-11-19.md`, with freeze hashes in `pilot/freeze_hashes.json`. The confirmatory design changed several times after that. `pilot/forking_paths_nov21.md` is the disclosure: eight designs, which screens were voided or exploratory, and the final 2×2 (which channel is true × which channel comes last). The frozen v3 plan and hashes are `pilot/plan_gaps_confirm_nov21_v3.md` and `pilot/freeze_hashes_gaps_confirm_v3.json`. After that freeze, a null is the result.

## How to rerun

From `experiments/memory_corruption/pilot/`. These checks do not call a model. The task code reads filler text from `/workspace/paper/memory_corruption/pilot/filler` (see `FILLER_DIR` in `pilot/tasks.py`). Point that path at `pilot/filler` before the unit tests. Three approval checks expect `pilot/approvals/MAIN_GO.txt` to be absent; with the file present they fail, and the other checks still run.

```bash
python -m unittest test_conflict_v3_nov21.py test_v3.py test_gaps_nov21.py test_gaps_verify_nov21.py test_world_fix_nov21.py test_rescreen_nov21.py test_amendment7.py test_amendment8.py test_amendment9.py test_budget_stop.py
python test_gates_c0.py
python run_confirm_v3_nov21.py --jobs-only
python dry_run_conflict_v3.py
```

`dry_run_conflict_v3.py` rewrites `dry_run_conflict_v3.txt`. A live grid (`python run_confirm_v3_nov21.py` with no flag) calls the model API. The runner reads the key from its env file at runtime. Do not commit that file. `--analyze-only` re-scores saved traces but also queries account spend, so it is not an offline command.

## Where to look

| What | Path |
|---|---|
| v3 results | `pilot/confirm_gaps_v3_nov21_results.txt` |
| Traces and hand audit | `pilot/traces_confirm_gaps_v3_nov21/`, `pilot/confirm_gaps_v3_nov21_hand_audit.json` |
| Forking-path disclosure | `pilot/forking_paths_nov21.md` |
| Pre-registration | `pilot/prereg_2026-11-19.md` |
| Approvals | `pilot/approvals.md` |
| Earlier pilot counts | `pilot/summary.md` |
