# GPT-OSS saved run files

This directory contains the completed 579-row GPT-OSS batch used in the reported results. It includes 568 coding records and 11 travel authorization records. One execution error is retained.

- `rows.jsonl` contains the aggregate result records.
- `tasks/` contains per-task rows and execution traces.
- `config.json`, `manifest.json`, and `run_status.json` record settings, selected tasks, and final status.
- `resume.json` records the continuation from the interrupted batch.
- `source-snapshot/` preserves the deployed source files. `source-manifest.json` records their original hashes.

The continuation includes completed records copied from the earlier run. Use the top-level rows once for aggregation. Per-task rows duplicate those records.

Among 96 coding cases with confirmed injections, 63 pass without recovery. Restart, suffix, and coarse produce correct results in 95, 86, and 86 cases respectively. These totals include cases that already passed before recovery.
