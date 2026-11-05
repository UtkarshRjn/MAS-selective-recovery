# GPT-OSS saved run files

This directory contains a **partial, interrupted batch of 506 rows** downloaded before the run resumed. The published tables describe the completed 579-row batch. The remaining rows and traces still need to be retrieved.

Use these files to inspect the available executions. They cannot reproduce every aggregate in the published tables. Preserve the interrupted status and missing conditions when analyzing them.

- `rows.jsonl` contains the 506 available result records.
- `run-artifacts.tar.gz` contains the same rows, per-task traces, configuration, selected tasks, status, and deployed source snapshot.
- `SHA256SUMS` verifies the result file and archive.

The archive extracts into `gpt-oss-coding-partial/` to identify its coverage. Per-task rows duplicate the top-level records and should not be counted again.
