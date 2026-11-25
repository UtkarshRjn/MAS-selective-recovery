# L1 results (22 November 2026, PT)

Frozen plan: docs/plan.md. Freeze: docs/freeze_hashes_law_l1.json (sha256 2750582d...).
Predictions written from stage C only: results/predictions.json (sha256 70a4d120...).
Raw scores: results/scores.txt and results/scores.json. Traces (gitignored): traces/.

## Run

- Stage C: 4,896 of 4,896 jobs, 0 failed. Stage P: 576 of 576 jobs, 0 failed.
- Spend: $4.2249 conservative (token counter and server delta), against a $10.00 cap.
- Models: api-deepseek-4.1-flash, api-glm-5.3-flash. Temperature 0.7. Prior add-0.1.

## Stage C (single edges)

- Relay edges (about 80 words in, 40 to 100 out) kept their input state in 48 of 48 runs for
  every model, fix and error. No loss and no recovery: S never returned to V or T (0 of 192).
- All change was at the compression edge (about 1,000 to 1,500 words in). P(V to S), the true
  value lost while the wrong value is kept:

| Fix | DeepSeek E1 | GLM E1 | DeepSeek E4 | GLM E4 |
|---|---|---|---|---|
| F0 plain | 23% | 23% | 11% | 6% |
| F1 pinned | 48% | 25% | 19% | 2% |
| F2 provenance | 6% | 6% | 6% | 2% |

## Pre-registered tests

1. Composition against the 2-state baseline (binary log loss on "wrong value present"):
   **failed. The law lost in 12 of 12 chain cells, so the kill rule fired.** The cause: the
   wrong value was present at the sink in every run (P = 1.00), and the unsmoothed baseline
   predicted exactly 1.0, while the smoothed law predicted 0.98. The outcome had no variance,
   so this test could not show anything in favour of either model. The failure is reported
   as it is; the test is not changed after the data.
2. Snowball threshold: R = 1.99 on every edge predicted growth. Observed error nodes in the
   binary trees were 1, 2, 4, 8 at depths 0 to 3 (predicted 1.00, 1.98, 3.95, 7.87).
   **Passed, but trivially**, because relays were identity.
3. Fix trade-off (descriptive): F2 lowest harm, F1 highest on DeepSeek typos.
4. Independence (diamond): observed sink distributions were within 0.05 MAE of the
   prediction in 3 of 4 cells; diamond F0/E4 missed P(harm) by 0.10 (0.14 predicted, 0.24
   observed).

## Descriptive: the 4-state predictions

The split between visible (V) and silent (S) errors, which decides harm, was predicted
closely on held-out topologies: MAE between predicted and observed sink distributions was
0.008 to 0.077 over 16 chain and diamond cells; predicted against observed P(harm) differed by
at most 0.14 (DeepSeek F1/E1 chain: 0.47 predicted, 0.61 observed). The 2-state baseline
cannot predict this split at all. This is a post-hoc observation, not the pre-registered test.

## What this means

- The pre-registered L1 composition claim is a null, and the reason is a design flaw: the
  baseline and the binary outcome were degenerate once relays turned out to be identity.
- The informative findings are the compression-edge rates, the fix ranking, and S being
  absorbing. L2 is designed around them, and its pipeline baseline is a 4-state
  constant-matrix model, so the comparison is not degenerate.
