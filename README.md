# Multi-agent failure propagation and recovery

When agents pass work to one another, an incorrect result can affect later decisions.
Restarting the entire workflow repeats useful work. This project asks whether recording
which results depend on each other lets us recover correctly while repeating fewer steps.

We inject faults into a running workflow, save its outputs and dependencies, and compare
recovery methods from copies of the same faulty execution. The recovery methods receive
the fault location so we can study recovery separately from fault detection.

## Progress so far

- Prepared 50 Python programming tasks from sanitized MBPP and three simulated travel cases.
- Implemented clean runs, seven fault families, intermediate verification, and three recovery baselines.
- Evaluated 10 coding tasks and all three travel cases with GPT-OSS 120B and Muse Glimmer 30B.
- Recorded task outcomes, intermediate errors, dependencies, tool actions, tokens, latency, and repeated stages.

The current results cover 10 of the 50 prepared coding tasks. Each task/condition has
one observation per model. The experiment guide contains installation steps, run commands,
fault definitions, and output formats. [Experiment guide](evals/README.md)

## How the experiment works

Each coding task contains a request and tests. The workflow runs nine stages in a fixed order.

```text
Specification → Code → Test → Review → Documentation
              → Specification → Code → Test → Review
```

The second pass lets agents revise their work. One test provides feedback during revision.
The remaining tests are hidden from the agents and score the final code. Success means
passing all final tests. Model stages use separate API calls with context assembled from
previous outputs. Tests execute in Docker, and Python determines review decisions from
test feedback. A normal workflow makes seven model calls.

We build the dependency graph during execution using the inputs declared by each workflow stage. Each node represents a stage execution, and an edge connects an earlier stage to a later stage that consumes its output. Dependencies can differ from execution order, so a later stage may be reusable when it does not depend on the faulty result.

We first run a clean baseline, then inject one fault per condition. Faults model incorrect
code, a missing requirement, a changed comparison, a tester timeout, malicious instructions,
a false test report, and a stale report. Accidental and adversarial cases are labelled.
The false/stale-report cases deliberately pair bad code with misleading feedback and
include truthful-feedback controls.

| Comparison | Behavior |
|---|---|
| No recovery | Finish the workflow after injection. |
| Always-verify | Check intermediate outputs and recompute test feedback during execution. |
| Restart | Rerun all stages after undoing supported effects. |
| Suffix | Rerun the faulty stage and every later stage. |
| Coarse | Follow recorded dependencies to select stages for replay. |

Each recovery method receives its own copy of the saved faulty execution. Injection is
disabled during recovery. Always-verify runs as a separate forward execution.

Travel agents research options, plan, and execute simulated bookings or emails. Three
cases test unauthorized recipients, booking without approval, and cancellation of a
protected booking. Actions and authorization history are checked in the simulated world.

## Results

Both evaluations use the same 10 coding tasks and three travel variants, with five task
workers. Conditions within a task run sequentially.

### Coding correctness

| Condition | GPT-OSS 120B (NRP) | Muse Glimmer 30B (Triton) |
|---|---:|---:|
| Clean | 10/10 | 9/10 |
| No recovery, confirmed injection | 63/96 | 57/95 |
| Correct after restart | 95/96 | 87/95 |
| Correct after suffix replay | 86/96 | 78/95 |
| Correct after coarse replay | 86/96 | 77/95 |
| Always-verify, scheduled fault cases | 106/120 | 106/120 |
| Always-verify, clean | 9/10 | 9/10 |

There are 12 fault/stage combinations per task, giving 120 scheduled fault cases.
GPT-OSS has 96 confirmed injections and 24 inapplicable cases. Muse has 95 confirmed
injections, 24 inapplicable cases, and one faulty execution that ended without a usable
saved state. That missing state produces one skipped row for each recovery method.
Coarse's Muse score includes one API error as unsuccessful.

Recovery scores include every usable injected case, including those already passing
without recovery. Rescue rates require matching recovery outcomes to the initially
failing cases. Eligibility depends on generated outputs, so the two models have different
eligible sets. Always-verify includes all 120 scheduled cases and one execution error
for each model.

### Recovery work and latency

Means below cover the recovery rerun. Tokens include API-reported input and output.

| Model | Method | Cost records | Stages redone / 9 | Tokens | Seconds |
|---|---|---:|---:|---:|---:|
| GPT-OSS 120B | Restart | 96 | 9.00 | 3,340 | 31.76 |
| GPT-OSS 120B | Suffix | 96 | 5.35 | 1,845 | 16.94 |
| GPT-OSS 120B | Coarse | 96 | 4.99 | 1,607 | 13.87 |
| Muse Glimmer 30B | Restart | 95 | 9.00 | 4,207 | 72.01 |
| Muse Glimmer 30B | Suffix | 95 | 5.39 | 2,478 | 43.22 |
| Muse Glimmer 30B | Coarse | 94 | 5.00 | 2,323 | 40.37 |

These means include unsuccessful scored reruns. Skips and errors without complete cost
records are excluded from the cost averages. Timing excludes the original faulty run,
surrounding cleanup, and the separate correctness audit. Provider load and parallel
workers affect latency.

### Forward-run cost

| Model | Condition | Cost records | Mean tokens | Mean seconds |
|---|---|---:|---:|---:|
| GPT-OSS 120B | Clean | 10 | 3,297 | 34.82 |
| GPT-OSS 120B | Always-verify, fault cases | 119 | 8,194 | 78.35 |
| Muse Glimmer 30B | Clean | 10 | 4,172 | 69.44 |
| Muse Glimmer 30B | Always-verify, fault cases | 119 | 10,121 | 177.03 |

Verification pays for checks throughout the forward run. Recovery costs above are the
additional work after a faulty execution.

### Travel and execution errors

GPT-OSS passes 2/3 clean travel cases. Its planning-only task fails even without an
attack, and restart makes one previously passing case incorrect. Muse passes all three
cases under clean, injected/no-recovery, verification, and restart conditions.
Neither model attempts an unauthorized action in these cases. Demonstrating recovery
from harmful actions requires cases where an injected attack changes downstream behavior.

The saved batches contain 579 GPT-OSS rows and 580 Muse rows across all conditions.
GPT-OSS retains one execution error. Muse retains four execution errors and three
skipped recovery rows. Independent conditions continue after an error.



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

## Additional workflows

The repository also includes the original travel experiments with support and monitor
recovery, graph topology sweeps (`agentrec graphs`), plus UndoBench, A2ASecBench, TAMAS, and Who&When adapters. See the
[travel and external benchmark guide](evals/travel-and-external.md) for their commands
and metrics. The [coding evaluation guide](evals/README.md) describes the MBPP runs.
