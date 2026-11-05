# Selective Recovery for Cascading Failures in Multi-Agent LLM Systems

An agent's incorrect output can become another agent's input. This project records those dependencies and uses them to decide which steps to repeat after a fault. We compare the correctness and cost of selective recovery with restarting the workflow.

The evaluation covers simulated travel booking, Python programming tasks, and small dependency graphs. Every recovery method receives the fault location and a copy of the same faulty execution. The system records messages, intermediate outputs, tool actions, dependencies, and model usage.

## Workflows

| Workload | Setup | Evaluation |
|---|---|---|
| Travel booking | Seven steps research flights and hotels, plan a trip, book both, save an itinerary, and email the traveler. Tools update a simulated world. | Qwen2.5-7B-Instruct on two task seeds and eleven fault types. |
| Python coding | Nine steps write a specification, code, test results, review, and documentation, followed by a second specification–code–test–review pass. | GPT-OSS 120B and Muse Glimmer 30B on 10 of 50 prepared sanitized MBPP tasks. |
| Travel authorization | Three cases cover unauthorized email, booking without approval, and cancellation of a protected booking. | GPT-OSS 120B and Muse Glimmer 30B, with clean, injected, verification, and restart conditions. |
| Coding examples and graphs | Three small programs and four graph shapes exercise different dependency paths. | Qwen2.5-7B-Instruct comparisons of recovery methods. |

Coding agents receive one test for revision feedback. The remaining tests stay hidden and score the final code. Generated code runs in constrained Docker containers. Testing uses tools, and Python computes review decisions from test feedback. The nine-stage workflow makes seven model calls, including the inherited reviewer calls.

## Recovery methods

The dependency graph is built during execution. Nodes represent stage executions, and edges record which earlier outputs each stage consumes. Coarse lineage follows dependencies on whole outputs. Support lineage uses recorded citations to individual claims.

| Method | Recovery behavior |
|---|---|
| Restart | Undo supported effects and rerun every stage. |
| Suffix | Rerun the faulty stage and every later stage in execution order. |
| Coarse | Rerun the faulty stage and stages reachable from it in the dependency graph. |
| Support | Use the implemented one-hop citation rule to retain steps with independent cited evidence. |
| Monitor | Select the same stages as coarse and append a note identifying the known fault to each replayed agent prompt. |
| Support-monitor | Apply the same fault note to the stages selected by support. |

The fault-note monitor makes no additional model call. Automated fault detection is a planned extension. Clean runs, injected runs without recovery, and always-verify provide additional comparisons. Always-verify checks intermediate outputs during execution and recomputes test feedback.

Travel recovery restores file contents and cancels supported bookings. Sent emails remain in the history, including when recovery sends a correction. Task correctness and irreversible effects therefore need separate checks.

## Results

### Travel booking with Qwen2.5-7B-Instruct

The local Q4_K_M model ran at temperature 0 on two task seeds. Both clean runs passed the recorded booking check. Of 22 scheduled fault cases, 16 changed the outcome. The table covers recovery from those same 16 cases.

| Method | Correct after recovery | Steps redone / 7 | Input + output tokens | Seconds |
|---|---:|---:|---:|---:|
| Restart | 16/16 | 7.0 | 6,678 | 17.4 |
| Suffix | 16/16 | 5.5 | 4,531 | 12.0 |
| Coarse | 16/16 | 4.6 | 3,650 | 9.9 |
| Monitor | 16/16 | 4.6 | 3,715 | 9.6 |
| Support | 16/16 | 4.5 | 3,578 | 9.7 |
| Support-monitor | 16/16 | 4.5 | 3,641 | 9.0 |

These runs scored the flight, hotel, itinerary, and email recipient. Their success check omitted the required refundability sentence, which the model also omitted on clean runs. The current checker includes that requirement. The saved results must therefore be interpreted using their recorded code revision. Rows, traces, and revisions are available in [the booking artifacts](artifacts/phase1/README.md).

Coarse repeated fewer steps than restart while preserving the measured booking outcome. The monitor repeated the same steps as coarse and used slightly more tokens. Its small observed time difference comes from one observation per condition.

### Coding and graph checks with Qwen2.5-7B-Instruct

On the wrong-hotel-id booking fault, support retained the correct flight booking and repeated five steps instead of coarse's six. On the small coding examples, the same support rule retained too much work and failed `clamp` and `clip_text` after a requirement was removed.

The fault note also had mixed results. For `clamp` with a missing requirement, monitor used 2,109 tokens and 13.87 seconds, compared with coarse's 1,453 tokens and 8.22 seconds. On the synthetic graphs, monitor produced three incorrect outputs where coarse succeeded. The [table-generation script](analysis/phase1_tables.py) reproduces these comparisons from committed rows.

### MBPP experiment

Both models evaluated the same 10 tasks with seven fault families and 12 fault/stage combinations per task. Faults include bad code, changed comparisons, missing requirements, tester timeouts, instruction hijacks, false test reports, and stale reports. Accidental and adversarial cases are labelled in the [experiment guide](evals/README.md).

False-report and stale-report cases deliberately combine bad code with misleading feedback and include truthful-feedback controls. Each recovery method starts from the same saved faulty run with injection disabled. Monitor and support were evaluated in the Qwen experiments above, while the MBPP comparison covers restart, suffix, and coarse.

Each task/condition has one observation per model, at temperature 0 and seed 0, with a 2,048-token output limit. Five tasks run in parallel, with conditions within a task executed sequentially.

### Coding correctness

| Condition | GPT-OSS 120B | Muse Glimmer 30B |
|---|---:|---:|
| Clean | 10/10 | 9/10 |
| No recovery, confirmed injection | 63/96 | 57/95 |
| Correct after restart | 95/96 | 87/95 |
| Correct after suffix replay | 86/96 | 78/95 |
| Correct after coarse replay | 86/96 | 77/95 |
| Always-verify, scheduled fault cases | 106/120 | 106/120 |
| Always-verify, clean | 9/10 | 9/10 |

Each task has 12 scheduled fault/stage combinations, so 10 tasks make 120 attempts. GPT-OSS confirmed 96 injections. Muse confirmed 95. Each model had 24 inapplicable attempts with no suitable target. These are excluded from recovery scores. Muse also had one faulty run with no saved state, so each recovery method skipped that row. Coarse's Muse score counts one API error as unsuccessful. Its cost row below uses the 94 reruns that finished with a cost record.

Recovery scores include runs that already passed without recovery. A rescue rate still needs those 33 GPT-OSS failures, and the corresponding Muse failures, matched one by one. The two models do not have the same eligible set. Always-verify uses all 120 scheduled cases and is not ranked directly against the recovery fractions.

### Recovery cost

Tokens count model input and output. Means include unsuccessful scored reruns. These saved measurements cover replay and exclude the original faulty run, surrounding cleanup, and final correctness audit. Current recovery timing also includes dependency selection and effect cleanup, so reproducing historical timings requires the original source revision.

| Model | Method | Cost records | Stages redone / 9 | Tokens | Seconds |
|---|---|---:|---:|---:|---:|
| GPT-OSS 120B | Restart | 96 | 9.00 | 3,340 | 31.76 |
| GPT-OSS 120B | Suffix | 96 | 5.35 | 1,845 | 16.94 |
| GPT-OSS 120B | Coarse | 96 | 4.99 | 1,607 | 13.87 |
| Muse Glimmer 30B | Restart | 95 | 9.00 | 4,207 | 72.01 |
| Muse Glimmer 30B | Suffix | 95 | 5.39 | 2,478 | 43.22 |
| Muse Glimmer 30B | Coarse | 94 | 5.00 | 2,323 | 40.37 |

Forward runs, not recovery reruns:

| Model | Condition | Cost records | Mean tokens | Mean seconds |
|---|---|---:|---:|---:|
| GPT-OSS 120B | Clean | 10 | 3,297 | 34.82 |
| GPT-OSS 120B | Always-verify, fault cases | 119 | 8,194 | 78.35 |
| Muse Glimmer 30B | Clean | 10 | 4,172 | 69.44 |
| Muse Glimmer 30B | Always-verify, fault cases | 119 | 10,121 | 177.03 |

### Travel authorization cases

GPT-OSS passes 2 of 3 clean travel cases. The planning-only task fails with no attack, and restart makes one previously passing case incorrect. Muse passes all 3 cases under clean, no recovery, verification, and restart. Neither model attempts an unauthorized action in these cases.

## Findings and remaining questions

Suffix and coarse fail all ten GPT-OSS stale-report cases. The report is identified as faulty, but the bad code remains eligible for reuse. Repeating the tests exposes the error without reopening code generation. Recovery needs a rule for responding to errors discovered during replay.

The measurements also show why reuse needs validation. The support rule saves work on a wrong hotel identifier, but fails on two coding examples with missing requirements. The next step is to validate retained outputs, reopen the stages needed to correct newly discovered errors, and test restoration of files and simulated actions. Broader task coverage and repeated runs will measure how consistently these choices help. An independent fault-detection agent can then be evaluated alongside recovery with known fault locations.

Current results have one observation per condition and no variability estimates. Coding outcomes are pooled across accidental and adversarial faults. Per-category results and rescue rates on initially failing cases require further analysis. Provider load and concurrent workers affect latency.

## Setup and reproduction

Install Python 3.11+, uv, and Docker for coding evaluation. Local model runs also require llama.cpp.

```bash
uv sync --locked
uv run pytest -q

# Rebuild the Qwen result tables from committed rows.
uv run python analysis/phase1_tables.py

# Inspect the coding evaluation options.
uv run agentrec mbpp run --help
```

The [coding experiment guide](evals/README.md) contains dataset setup, model endpoint configuration, full run commands, fault definitions, and resume instructions. The [travel and external benchmark guide](evals/travel-and-external.md) covers booking runs and graph sweeps. Recorded source revisions and commands for the published Qwen results are listed with the [result artifacts](artifacts/phase1/README.md).

The repository contains the Qwen rows and traces. The GPT-OSS and Muse tables summarize saved batches of 579 and 580 rows respectively, whose raw artifacts have yet to be included in the repository. GPT-OSS retains one execution error. Muse retains four execution errors and three skipped recovery rows.

## External benchmarks

Adapters provide additional workloads and failure records. UndoBench exercises a retry before a state-changing action. A2ASecBench checks whether untrusted agent messages lead to blocked actions. TAMAS supplies attack scenarios, and Who&When supplies failure traces for attribution. These adapters are described in the [external benchmark guide](evals/travel-and-external.md). Recovery after completed external actions remains future work.

## Repository guide

| Location | Contents |
|---|---|
| [src/agentrec/](src/agentrec/) | Workflows, model clients, fault injection, dependency tracking, recovery, and CLI. |
| [tests/](tests/) | Automated checks for the harness and recovery behavior. |
| [evals/mbpp-50.json](evals/mbpp-50.json) | Manifest for the 50 prepared coding tasks. |
| [evals/README.md](evals/README.md) | Coding protocol, configuration, and run instructions. |
| [evals/travel-and-external.md](evals/travel-and-external.md) | Travel, graph, and external benchmark instructions. |
| [artifacts/phase1/](artifacts/phase1/) | Committed Qwen results, traces, and source revisions. |
| [analysis/phase1_tables.py](analysis/phase1_tables.py) | Rebuilds the Qwen tables from saved rows. |
| [docs/phase1_report.tex](docs/phase1_report.tex) | Repository copy of the Phase 1 report. |
