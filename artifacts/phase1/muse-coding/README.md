# Muse experiment evidence

The completed batch contains 580 result rows and 573 saved execution traces. It retains 4 execution errors and 3 skipped conditions. Errors or skipped conditions can lack a trace.

| File | Contents |
|---|---|
| `rows.jsonl` | One aggregate record per condition. Use this file for analysis. |
| `traces/` | Agent messages, intermediate outputs, dependencies, and simulated state. Filenames identify the task, fault, and method. |
| `config.json` | Model and execution settings. |
| `manifest.json` | Selected task definitions and dataset metadata. |

The batch resumed an interrupted run and includes earlier completed records once. Historical deployment paths in configuration refer to the original machine.

The exact deployed source snapshot and original run/resume metadata are preserved at commit `aec632102fd172d7ba432ddb357743d03d31a5ff`. Use that snapshot when reproducing historical scoring and timing. [Source snapshot](https://github.com/UtkarshRjn/MAS-selective-recovery/tree/aec632102fd172d7ba432ddb357743d03d31a5ff/artifacts/phase1/muse-coding/source-snapshot) · [Run and resume metadata](https://github.com/UtkarshRjn/MAS-selective-recovery/tree/aec632102fd172d7ba432ddb357743d03d31a5ff/artifacts/phase1/muse-coding)
