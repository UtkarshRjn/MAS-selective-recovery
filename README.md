# Multi-agent failure propagation and recovery

Phase 1 evaluates a fixed coding workflow on 50 pinned MBPP examples, with seven
fault families and three simulated travel authorization cases. The reported evaluations
select 10 coding examples using `--limit 10`, using GPT-OSS 120B and Muse Glimmer 30B.

See [experiment setup, commands, and metrics](evals/README.md).

## Muse results

Triton `api-muse-glimmer-30b` completed the same 10 coding tasks and 3 travel
variants with 5 workers. These are single observations per task/condition.

| Coding condition | GPT-OSS 120B (NRP) | Muse Glimmer 30B (Triton) |
|---|---:|---:|
| Clean | 10/10 | 9/10 |
| No recovery, confirmed injection | 63/96 | 57/95 |
| Restart: correct after rerun | 95/96 | 87/95 |
| Suffix: correct after rerun | 86/96 | 78/95 |
| Coarse: correct after rerun | 86/96 | 77/95 |
| Always-verify, scheduled fault cases | 106/120 | 106/120 |
| Always-verify, clean | 9/10 | 9/10 |

Muse has 95 confirmed injected executions and 24 inapplicable injections; one
additional faulty execution ended in an error without a usable saved state.
Each recovery method therefore has 95 attempted recoveries plus one skipped row.
Coarse's 77/95 includes one API error as unsuccessful. These are correctness
scores after rerunning, not rescue rates restricted to initially failing cases.
The models' eligible sets differ, so these pooled scores are not a matched-case
model comparison. Verification covers all 120 scheduled cases, including
inapplicable injections and an execution error for each model.

### Muse work and latency

| Mean per condition | Restart | Suffix | Coarse |
|---|---:|---:|---:|
| Conditions with complete cost records | 95 | 95 | 94 |
| Stages recomputed (out of 9) | 9.00 | 5.39 | 5.00 |
| Input + output tokens | 4,207 | 2,478 | 2,323 |
| Rerun seconds | 72.01 | 43.22 | 40.37 |

Costs include unsuccessful scored reruns, but exclude skipped rows and the coarse
API-error row without complete recovery accounting; missing costs are not zero.
They cover recovery reruns only, excluding the original faulty execution,
surrounding cleanup, and the separate correctness audit. Clean forward runs
average 4,172 tokens and 69.44 seconds (10 runs). Fault-scheduled always-verify
averages 10,121 tokens and 177.03 seconds (119 complete cost records). Endpoint
load and parallel workers affect latency; these are not controlled speed tests.

Travel succeeds in all 3 clean, 3 injected/no-recovery, 3 verification, and 3
restart runs. No unauthorized action was attempted, so these cases do not
establish recovery from attack-caused damage.

The final Muse batch has **580 rows**: 20 clean/verify-clean, 240 fault-scheduled
forward runs, 288 recovery rows (including 3 skips), 20 truthful-feedback
controls, and 12 travel runs. Four execution errors are retained: task 9's
no-recovery tester-timeout case and stale-report truthful control, task 11's
stale-report verification, and task 14's coarse recovery after a bad patch
(HTTP 422). Other conditions continued independently.

Raw run directory (local, ignored by Git):
`results/aws-triton-muse-glimmer-30b-full-10-w5-resume-001/`.

## Reading order

Start with `evals/README.md` for the experiment and method definitions. Then read
`src/agentrec/phase1.py` for the condition schedule, `coding.py` for stage execution,
and `benchmark_faults.py` for the mutations. `authorization.py` contains the three
travel variants. Dataset loading, Docker execution, and parallel scheduling are in
`mbpp.py`.

## Code map

- `mbpp.py`: dataset loading, Docker scoring, parallel task execution, and CLI.
- `phase1.py`: coding fault schedule, comparisons, and stage metrics.
- `coding.py`, `benchmark_faults.py`: coding stages and injected artifact changes.
- `authorization.py`: three travel policy cases and authorization measurements.
- `workflow.py`, `agents.py`, `tasks.py`, `world.py`, `checker.py`, `faults.py`: travel
  stages, prompts, simulated tools, task generation, scoring, and faults.
- `provenance.py`, `recovery.py`: dependencies and recovery.
- `conditions.py`, `resume.py`: condition-level error isolation and explicit resume.
- `llm.py`, `runner.py`, `programs.py`: model client, shared recording, and code task helpers.

Run `uv run pytest -q` for offline tests. Mock-model results only validate the harness;
they are not model accuracy or attack-success measurements. Real model runs require
`--execute-model`. Generated code is evaluated in constrained Docker containers;
travel tools never contact real booking or email services.

Historical reports, deployment helpers, downloaded data, and raw run artifacts are
ignored. Removed TAMAS, A2A, Who&When, topology, and monitoring utilities remain
available in Git history. Existing EC2 jobs use their own saved source snapshots.
