# L3 results (23 November 2026, PT)

Plan: `plan_law_l3_draft.md` (decisions in section 7). Freeze: `freeze_hashes_law_l3.json`
(code as run: commit fa851f5). Predictions for the budget-80 cells were written before any
budget-80 data existed: `l3_predictions.json` (sha256 7c05f39e...). Scores: `l3_scores.txt`,
`l3_scores.json`. Traces (not in git): `traces_law_l3/`.

## Run

- Single summaries: 4,608 of 4,608. Two-hop chains: 768 of 768. Failed jobs passed on a rerun.
- Spend: $5.43 for L3, under the $7 cap. L1 + L2 + L3: $23.71.
- Models: api-deepseek-4.1-flash, api-glm-5.3-flash. Every input: exactly 900 words.

## The question

A handoff names one value in the plan line ("Book flight_id X") and a different value in an
evidence line ("The listing showed Y as the cheapest flight"). Which one does a summarizer
keep? We swapped which of the two is the true value.

## Pre-registered tests

| Test | DeepSeek | GLM |
|---|---|---|
| H1a: the plan's value is lost in at most 2% of runs | **Pass** (0%) | **Pass** (0%) |
| H1b: evidence loss is the same whichever value is true (within 10 points) | **Pass** (28% vs 23%) | **Pass** (23% vs 22%) |
| H2: loss rises with distance: adjacent < middle < end | Fail | Fail |
| H3: loss falls with summary budget: 40 > 80 > 160 words | **Pass** | **Pass** |
| H4: 24 binding rules raise loss at the same length | **Pass** | **Pass** |
| H5: the second hop adds at most 5 points of loss | **Pass** | **Pass** |
| Held-out budget 80 inside the predicted interval in 75% of cells | Fail (4 of 12) | Fail (6 of 12) |

The kill rule (H1b fails on both models) did not fire.

## What the data show

1. **Summarizers keep the plan's value, not the true value.** The plan's value was never lost:
   0 times in every single summary and in every chain, on both models. The evidence value was
   lost at nearly the same rate whether it was the truth or the error. So when the wrong value
   sits in the plan, the truth is deleted; when the true value sits in the plan, the error is
   deleted. "Errors survive handoffs" in L1 and L2 really meant "the plan's value survives".
2. **Summary budget is a strong dose.** Evidence loss, all positions pooled:

   | Budget | DeepSeek | GLM |
   |---|---|---|
   | 40 words | 50% | 52% |
   | 80 words | 23% | 13% |
   | 160 words | 3% | 2% |

3. **The middle is worst, not the end.** Evidence loss at an 80-word budget:

   | Evidence line | DeepSeek | GLM |
   |---|---|---|
   | next to the plan | 1% | 4% |
   | in the middle | 48% | 28% |
   | at the end | 21% | 7% |

   This is why H2 failed. Loss is not a simple function of distance: the end of the text is
   remembered better than the middle. This matches the "lost in the middle" effect reported for
   long-context models, and it likely explains L2's peak at 800 words.
4. **Competing important content raises loss.** With 24 binding rules instead of neutral notes of
   the same length: 14% to 37% (DeepSeek) and 10% to 34% (GLM).
5. **The first hop decides.** In two-hop chains, the second hop added 3 new losses in 346
   chains (DeepSeek) and 0 in 366 (GLM). It never restored a lost value.
6. **Provenance tags prevent the loss.** With the provenance prompt, evidence loss was 0.2%
   (DeepSeek) and 0% (GLM).

## Why the held-out test failed

The pre-registered model is additive (role + position + load + ln budget). The data have
strong interactions: the position effect is large at 40 words and almost gone at 160 words.
An additive model cannot fit that, so its budget-80 predictions missed in half or more of the
cells. The direction of every effect was right; the size per cell was not.

## What this means

- Main claim, supported on both models: **in a handoff, role decides which of two conflicting
  values survives, not truth.** The practical rule: put verified values in the role that the
  next agent acts on, and keep unverified ones out of it. A provenance prompt, or a larger
  summary budget, keeps both values.
- Not supported: a simple additive formula that predicts each cell.
- Limits: two flash models from one gateway; one travel task; injected errors; no executor.
  The role finding must be tested on more model families (including frontier models) and on a
  real multi-agent setting before it is a general claim.
