# L2 results (23 November 2026, PT)

Plan: `docs/plan.md` (decisions in section 5a). Cap change: `docs/cap_amendment.md`.
Freeze: `docs/freeze_hashes_law_l2.json` (code as run: commit 3209a35).
Predictions, written from stage C2 before stage P2: `results/predictions.json` (sha256 3791bb6d...).
Scores: `results/scores.txt`, `results/scores.json`. Traces (not in git): `traces/`.

## Run

- Stage C2: 12,096 of 12,096 jobs. 26 jobs failed on network errors and passed on a second run.
- Stage P2: 1,536 of 1,536 pipelines, 0 failed.
- Spend: $14.06 for L2 (token counter), under the $16 cap. L1 plus L2: $18.29.
- Models: api-deepseek-4.1-flash and api-glm-5.3-flash. Temperature 0.7. Prior add-0.1.

## The question

A handoff text can hold a wrong value and the true value together (V, a visible conflict).
After an agent summarizes it, the true value can be lost, so that only the wrong value is
left (S, a silent error). L2 asks: does that loss grow with how much the agent must compress?

## Pre-registered tests

| Test | Result |
|---|---|
| 1. H1: loss grows with compression (plain prompt, values together), slope above 0 on both models and both error types | **Passed** in 4 of 4 cells |
| 2. The 800-word result, predicted from 80, 300 and 1500 words, falls inside the predicted interval in at least 75% of cells | **Failed**: 3 of 8 plain-prompt cells |
| 3. The pipeline law beats a one-matrix baseline in at least 75% of pipeline cells | **Failed**: 10 of 32 cells |

The kill rule (test 1 fails on both models) did not fire.

## What the data show

Loss of the true value (V to S), plain prompt, 80-word summaries:

| Input words | DeepSeek, values together | DeepSeek, values far apart | GLM, values together | GLM, values far apart |
|---|---|---|---|---|
| 80 | 0% | - | 0% | - |
| 300 | 2% | 8% | 24% | 31% |
| 800 | 4% | **53%** | 25% | **51%** |
| 1500 | 6% | 17% | 23% | 42% |

Summary length, at 800 words in, values together:

| Summary | DeepSeek | GLM |
|---|---|---|
| 40 words | 28% | 73% |
| 80 words | 4% | 25% |
| 250 words | 0% | 0% |

1. **Summary length is the strongest lever.** Fewer words for the summary means more loss,
   on both models, with no exception. A 250-word summary never lost the true value.
2. **Distance between the two values matters more than input length.** When the values
   were far apart, loss was 2 to 13 times higher. Loss peaked at 800 words, not at 1500. This
   peak is why test 2 failed. A likely cause is the design: the padding text in front of the
   true value is different at each length (trip rules at 800 words, notes about earlier trips
   at 1500). Content and length are not separated.
3. **A lost value never comes back.** 0 recoveries from S in 2,288 runs.
4. **Provenance tags (F2) almost remove the loss**: about 1% on DeepSeek and 0% on GLM, over
   all lengths and placements.
5. **Pinning the true value protects it completely** (0% loss on both models, 288 runs).
   Pinning the wrong value changes nothing (the same loss as no pin). L1's result that a pin
   makes loss worse did not repeat.
6. **The first handoff decides.** In the pipelines with the plain prompt, the first hop made
   19 silent errors (DeepSeek) and 88 (GLM) in 384 runs each. All later hops together added
   only 2 and 10. The law multiplies a loss at every hop, so it over-predicts later loss. That
   is why test 3 failed. Later hops read a short summary in which the two values sit close
   together, and close values are rarely lost.
7. **The models differ.** GLM lost the true value 4 to 6 times more often than DeepSeek.

## Analysis notes

- With F2, almost no loss happened, so the F2 slopes cannot be estimated. The frozen code
  still prints "excl0=True" and "OUTSIDE" for them, because the bootstrap intervals have zero
  width. Those F2 lines carry no information. Tests 1 and 2 are read on the plain prompt (F0).
- The cleaned code (commit 865c93b and later) gives the same predictions hash and identical
  scores from the same traces.

## What this means

- Supported: compression causes loss (test 1); summary length is a strong, clean dose; lost
  values do not come back; provenance tags and pinning the true value are effective fixes.
- Not supported: a law that predicts pipelines from input length alone (tests 2 and 3).
- Next, as a new pre-registered study: make the summary budget and the distance between the
  values the two controlled variables, keep the padding content the same at every length,
  and model the first hop separately from later hops.
- Limits: two flash models from one gateway, one travel domain, synthetic padding, no
  executor in L2.
