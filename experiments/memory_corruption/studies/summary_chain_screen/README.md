# Study: summary chain screen

**Question.** A wrong value enters a plan. The plan is summarized, then the summary is summarized again, several times. Does the wrong value survive each step? And when two independent summaries are written, do they make the same mistakes?

**Short answer.** The wrong value survived every step (it was the plan's recommended value). Whether the true value also survived was decided almost entirely at the first summary; later summaries kept what they were given. The two summary channels failed together only rarely.

## Design

- No executor. Only summarizer calls.
- Part 1, composition: 2 models × 2 templates × 12 seeds × depths 1, 2 and 4 (144 jobs). Predictions for depths 2 and 4 were locked after depth 1.
- Part 2, channel correlation: 2 templates × 32 seeds × 4 summary channels (256 jobs).
- Models: `api-deepseek-4.1-flash`, `api-glm-5.3-flash`. 400 jobs, 592 calls, $0.59.

## Result

| | deepseek | glm |
|---|---|---|
| Typo still present at depth 1, 2, 4 | 100% | 100% |
| Both values present at depth 1, 2, 4 | 63%, 71%, 67% | 92%, 96%, 96% |
| Simple law (survival^k) for "both values present" | missed | held |

No kill rule fired. Clean false alarms were 0. One prompt (p2) returned an empty summary on 45 of 64 calls, so the different-prompt correlation cell has too little data.

## Files

| File | Role |
|---|---|
| `run_summary_chain.py` | The grid, scoring and analysis |
| `run_summary_chain_cost_watch.py` | The only allowed way to start a paid run: it checks the spend after part 1 |
| `dry_run_summary_chain.py` | Renders every job with a scripted mock model |
| `check_false_alarm_gap.py` | Offline check of the false-alarm rule |
| `test_summary_chain.py` | Unit tests |
| `docs/` | Plan (v4), addendum, change notes v2–v4, freeze hashes |
| `results/` | Results, locked predictions, cost report, false-alarm check |

## Run it (offline, no key, no cost)

```bash
python3 -m unittest test_summary_chain
```

```bash
python3 run_summary_chain_cost_watch.py --jobs-only
```

```bash
python3 dry_run_summary_chain.py
```

The dry run must write a file whose sha256 equals `dry_run_s0_nov21_txt` in `docs/freeze_hashes_s0_v4.json`.
