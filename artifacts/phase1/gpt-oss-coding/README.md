# GPT-OSS saved run files

This directory contains a **partial, interrupted batch of 506 rows** downloaded before the run resumed. The published tables describe the completed 579-row batch. The remaining rows and traces still need to be retrieved.

Use these files to inspect the available executions. They cannot reproduce every aggregate in the published tables. Preserve the interrupted status and missing conditions when analyzing them.

- `rows.jsonl` contains the 506 available result records.
- `tasks/` contains per-task rows and execution traces.
- `config.json`, `manifest.json`, and `run_status.json` record settings, selected tasks, and interrupted status.
- `source-snapshot/` preserves the deployed source files.

Per-task rows duplicate the top-level records and should be counted once.
