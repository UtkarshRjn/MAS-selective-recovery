# Study: compression

Called **L2** in the plan and in older notes.

**Question.** When an agent shortens a note, does it lose the true value more often the more it has to shorten?

**Short answer.** Yes, compression causes loss: the shorter the summary, the more often the true value is dropped. But input length alone does not predict pipelines. Loss peaked at middle lengths when the two values were far apart, and later handoffs lost far less than the first one.

## Design

- The same note core, padded with travel text to 80, 300, 800 or 1,500 words, summarized to 80 words.
- The two conflicting values sit together (adjacent) or far apart (spread).
- Extra arms: 40, 80 and 250 word summaries; pinning the wrong or the true value.
- The 800-word level is held out: predicted from 80, 300 and 1,500 before its data were seen.
- Stage P2: four 3-step pipelines with different lengths per step.
- 16,704 calls, two models, $14.06.

## Result

| Pre-registered test | Result |
|---|---|
| Loss grows with compression (both models, both error types) | **Passed** |
| 800-word prediction inside its interval in 75% of cells | Failed (3 of 8) |
| Pipeline law beats a one-table baseline in 75% of cells | Failed (10 of 32) |

Also: a lost value never came back (0 of 2,288); source tags kept the loss near 0%; pinning the true value prevented loss. Full write-up: `results/results.md`.

## Files

| File | Role |
|---|---|
| `compression_texts.py` | Builds notes of an exact length with the values together or apart |
| `run_compression.py` | Grid, single-edge runs, logistic fit, predictions, pipelines, scoring |
| `test_compression.py` | Unit tests |
| `docs/plan.md`, `docs/cap_amendment.md`, `docs/freeze_hashes_law_l2.json` | Frozen plan, the spend cap change, hashes |
| `results/` | Predictions (and their hash), scores, write-up |

## Run it (offline)

```bash
python3 -m unittest test_compression
```

```bash
python3 run_compression.py --dry-run
```
