# Study: role vs truth

Called **L3** in the plan and in older notes.

**Question.** A note says "Book flight X" (the plan line) and also "the listing showed Y as the cheapest flight" (the evidence line). One of X and Y is right. When an agent summarizes the note, which value does it keep?

**Short answer.** It keeps the value in the plan line, and it drops the value in the evidence line at about the same rate whether that value is right or wrong. On both models, the plan's value was never dropped (0 of 3,035 and 0 of 3,072 summaries).

## Design

| Factor | Levels |
|---|---|
| Role | wrong value in the plan line, or true value in the plan line |
| Position of the evidence line | next to the plan line, middle, end |
| Load | 24 trip rules, or neutral notes of the same length |
| Summary budget | 40, 80 or 160 words |
| Extra arm | a prompt that asks for source tags and CONFLICT lines |

- Every note has exactly 900 words. A role swap changes only the two IDs.
- The budget-80 cells were predicted from budgets 40 and 160, and hashed before any budget-80 call.
- 4,608 single summaries and 768 two-step chains, two models, $5.43.

## Result

| Pre-registered test | deepseek | glm |
|---|---|---|
| Plan value dropped in ≤ 2% | Pass (0%) | Pass (0%) |
| Evidence loss equal across roles (within 10 points) | Pass (28.0% vs 23.3%) | Pass (22.7% vs 21.6%) |
| Loss rises with distance | Fail: the middle is worst, not the end | Fail |
| Loss falls with budget | Pass (50% / 23% / 3%) | Pass (52% / 13% / 2%) |
| 24 rules raise loss | Pass | Pass |
| The second step adds ≤ 5 points | Pass (3 of 345) | Pass (0 of 366) |
| Budget-80 prediction inside its interval in 75% of cells | Fail (4 of 12) | Fail (6 of 12) |

Known limits: the plan line is always first and is an instruction, so role is mixed with position and wording; the prompt asks for "values the executor needs to act". Full write-up: `results/results.md`.

## Files

| File | Role |
|---|---|
| `role_texts.py` | Builds the 900-word notes for every cell |
| `run_role_vs_truth.py` | Grid, logistic fit, predictions, two-step chains, scoring |
| `test_role_vs_truth.py` | Unit tests, including the length and role-swap checks |
| `docs/plan.md`, `docs/freeze_hashes_law_l3.json` | Frozen plan and hashes |
| `results/` | Predictions (and their hash), scores, write-up |

## Run it (offline)

```bash
python3 -m unittest test_role_vs_truth
```

```bash
python3 run_role_vs_truth.py --dry-run
```
