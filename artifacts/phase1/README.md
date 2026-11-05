# Phase 1 results and logs

These are the raw runs behind the Qwen numbers in the [Phase 1 report](../../docs/phase1_report.pdf). Each row is one run. Each trace in `qwen-booking/traces/` records the model exchanges, tool effects, provenance graph, and recovery decision for one task, fault, and method.

```bash
uv run python analysis/phase1_tables.py     # rebuilds the tables below from these rows
```

| Directory | Report claim | Model | Code revision | Command |
|---|---|---|---|---|
| `qwen-booking/` | Travel booking in Table 1 (section 5.1): coarse lineage against restart and suffix, the fault-note monitor against coarse, and the support rule on `wrong_hotel_id` (section 5.3 and Figure 1). Support-plus-note is the last row of Appendix C. | Qwen2.5-7B-Instruct Q4_K_M, llama.cpp, temperature 0, seed 0 | `d85ed9c` | `uv run agentrec experiment --tasks 2 --faults all --methods restart,suffix,coarse,support,monitor,support_monitor` |
| `qwen-coding-and-graphs/` | Appendix C: monitor cost on `clamp`, support failures on `clamp` and `clip_text`, and monitor’s three incorrect synthetic-graph outputs | same | `a889670` | `uv run agentrec graphs` |

To reproduce a row, check out the listed revision, start the model server with `./scripts/start_server.sh`, and run the command. The two task seeds are 0 and 1. Results are written to `results/`, which is not tracked.

Pin the revision. The booking rows score success as the correct flight, hotel, itinerary, and confirmation email. The current checker also requires the refundability sentence. Qwen never writes that sentence, so a rerun on `main` reports every booking as failed.

## What the rows show

Booking: 2 clean runs, both correct. Of 22 scheduled faults, 16 changed the outcome. Every method recovered all 16. The means below cover only those 16 recovery reruns.

| Method | Recovered | Steps redone | Tokens | Seconds |
|---|---|---|---|---|
| restart | 16/16 | 7.0/7 | 6,678 | 17.4 |
| suffix | 16/16 | 5.5/7 | 4,531 | 12.0 |
| coarse | 16/16 | 4.6/7 | 3,650 | 9.9 |
| monitor | 16/16 | 4.6/7 | 3,715 | 9.6 |
| support | 16/16 | 4.5/7 | 3,578 | 9.7 |
| support_monitor | 16/16 | 4.5/7 | 3,641 | 9.0 |

Coding programs: one run per program and fault. On `clamp` with a dropped requirement, monitor passes in 13.87 s and 2,109 tokens, compared with 8.22 s and 1,453 tokens for coarse. Support redoes 2 of 9 steps and fails the hidden suite on `clamp` and `clip_text`.

## Coding and authorization logs

| Directory | Saved rows | Coverage |
|---|---:|---|
| `muse-coding/` | 580 | Complete saved Muse batch, including four execution errors and three skipped recovery conditions. |
| `gpt-oss-coding/` | 579 | Complete saved GPT-OSS batch, including one execution error. |

Each coding directory contains `rows.jsonl`, `traces/`, `config.json`, `manifest.json`, and a short README. Trace filenames identify the task, fault, and method. Use the aggregate rows once when analyzing results.

The per-model READMEs link to the exact deployed source snapshots and original continuation metadata preserved in Git history. Those versions predate the current timing and checker changes. Model endpoint names and deployment paths remain in the configuration for reproducibility.

Run commands and model options are documented in the [coding experiment guide](../../evals/README.md). `analysis/phase1_tables.py` rebuilds the Qwen tables only.
