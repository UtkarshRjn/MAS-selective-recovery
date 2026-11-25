# Analysis corrections to the confirmatory v3 grid

Written after the grid. This note changes no arm, prompt, grader, or frozen file. None of the
files hashed in `freeze_hashes_gaps_confirm_v3.json` were edited. The changes are in
`run_confirm_v3_nov21.py` (not hashed) and the new `stats_exact.py`, with tests in
`test_stats_exact.py`.

`confirm_gaps_v3_nov21_results.txt` and `.json` were written by the old code and are left as
they were. Regenerating them needs the traces, which are not in this tree. The corrected
numbers below come from the counts stored in the results JSON, using the new functions.

The pre-registered decision does not change: conflict-triggered asking holds, and no
preference pattern holds.

## 1. Multinomial logit: separation was reported as significant

The 2x2 logit has four parameters per equation and one parameter for each design cell. If an
outcome has no runs in a design cell, its MLE is infinite. BFGS still reported
`converged=True`. Every bootstrap replicate started from that point and returned it
unchanged. The result was zero-width intervals such as `[-1.580, -1.580]` with
`excl0=True`.

Fix: an equation counts as estimable only if every design cell has at least one run of that
outcome and at least one ask_or_hold run. A non-estimable equation is reported as
`not estimable`, with `est` and CI set to null and `excludes_zero=False`. A zero-width
interval never counts as excluding 0.

| Model | Equation | Events per cell (sv-true, vs-true, sv-rev, vs-rev) | Old report | Corrected |
|---|---|---|---|---|
| deepseek | follow_verbatim vs ask | 0, 0, 0, 0 | all 4 terms `excl0=True` | not estimable |
| deepseek | follow_summary vs ask | 0, 0, 0, 0 | all 4 terms `excl0=True` | not estimable |
| glm | follow_verbatim vs ask | 3, 2, 3, 2 | as printed | unchanged (estimable) |
| glm | follow_summary vs ask | 0, 0, 0, 0 | 3 terms `excl0=True` | not estimable |

Effect on decisions: the recency check had passed its logit step on deepseek only because of
this artifact. Recency still fails on the 5-of-6 template rule, so the outcome is unchanged.

## 2. Rule secondaries: exact test, monotone Holm

The old code used a two-proportion z test on 36/36 against 33–35/36. That test is not valid
at rates this close to 100%, and it ignored the seed pairing. Rule and matched cells share
(template, seed) by construction (`build_jobs`), so the corrected test is exact McNemar.
Fisher exact is the fallback when runs do not pair. The rule cells asked on 36 of 36 runs,
so every non-ask in the matched cell is a discordant pair, and the counts determine the test
exactly.

The old Holm code also produced non-monotone adjusted p-values. For two equal p-values it
reported 0.1537 and 0.0768. Adjusted p-values now take the step-down maximum.

| Model | Secondary | Discordant pairs | Old p (z) | Old p_holm | McNemar p | Corrected p_holm | Reject at 0.05 |
|---|---|---:|---:|---:|---:|---:|---|
| deepseek | P_conflict_rule | 2 | 0.1515 | 0.3030 | 0.500 | 1.000 | no |
| deepseek | P_reversed_conflict_rule | 1 | 0.3139 | 0.3139 | 1.000 | 1.000 | no |
| glm | P_conflict_rule | 3 | 0.0768 | 0.1537 | 0.250 | 0.500 | no |
| glm | P_reversed_conflict_rule | 3 | 0.0768 | 0.0768 | 0.250 | 0.500 | no |

Fisher exact, unpaired, for reference: deepseek 0.493 and 1.000, glm 0.239 and 0.239.

Power note: with matched cells already asking on 92–97% of runs, the rule could raise the
ask rate by at most 3 discordant pairs. The smallest possible McNemar p is then 0.25, so
these secondaries could not have rejected. Report them as uninformative, not as evidence
that the rule has no effect.

## Rerun

From this folder, with numpy, scipy and httpx installed:

```bash
python3 -m unittest test_stats_exact test_conflict_v3_nov21 test_v3
```

Without numpy and scipy, the runner integration test in `test_stats_exact.py` is skipped and
the other 10 tests still run. A later `--analyze-only` run on the traces would rewrite the
results files with these corrections. That command queries account spend, so it is not
offline.
