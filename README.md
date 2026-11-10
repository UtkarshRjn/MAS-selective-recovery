# Selective Recovery for Cascading Failures in Multi-Agent LLM Systems

An agent's incorrect output can become another agent's input. This project records those dependencies and uses them to decide which steps to repeat after a fault. We compare the correctness and cost of selective recovery with restarting the workflow.

The two main evaluations cover simulated travel booking and Python programming tasks. Every recovery method receives the fault location and a copy of the same faulty execution. The system records messages, intermediate outputs, tool actions, dependencies, and model usage.

## Workflows

| Workload | Setup | Evaluation |
|---|---|---|
| Travel booking | Seven steps research flights and hotels, plan a trip, book both, save an itinerary, and email the traveler. Tools update a simulated world. | Qwen2.5-7B-Instruct on two task seeds and eleven fault types. |
| Python coding | Nine steps write a specification, code, test results, review, and documentation, followed by a second specification–code–test–review pass. | GPT-OSS 120B and Muse Glimmer 30B on 10 of 50 prepared sanitized MBPP tasks. |

Coding uses one test for revision feedback and hidden tests to score the final code. Generated code runs in Docker. Workflow details appear in the report’s Experimental Setup section. [Report, section 4, page 1](docs/phase1_report.pdf#page=1)

## Recovery methods

We compare restarting the whole workflow, replaying its remaining suffix, and selecting steps through recorded dependencies (coarse). Support uses citations to retain independent work. Monitor and support-monitor add a known-fault note to the selected steps. Always-verify checks intermediate outputs during the forward run.

The report explains graph construction, replay, and the fault-note monitor in “Dependency recording and replay” and “Monitor prototype.” [Report, sections 3.1–3.2, page 1](docs/phase1_report.pdf#page=1)

Figure 1(b) shows which steps are reused after a wrong hotel ID. Section 5.3 reports that the support rule keeps the correct flight booking in that example. [Figure 1, page 1](docs/phase1_report.pdf#page=1) [Section 5.3, page 2](docs/phase1_report.pdf#page=2)

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

### Python coding with MBPP

Both models evaluated the same 10 tasks with seven fault families and 12 fault/stage combinations per task. Faults include bad code, changed comparisons, missing requirements, tester timeouts, instruction hijacks, false test reports, and stale reports. Accidental and adversarial cases are labelled in the [experiment guide](evals/README.md).

False-report and stale-report cases deliberately combine bad code with misleading feedback and include truthful-feedback controls. Each recovery method starts from the same saved faulty run with injection disabled. Monitor and support were evaluated in the Qwen booking and additional coding experiments, while the MBPP comparison covers restart, suffix, and coarse.

Each task/condition has one observation per model, at temperature 0 and seed 0, with a 2,048-token output limit. Five tasks run in parallel, with conditions within a task executed sequentially.

#### Correctness

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

#### Cost

Tokens count model input and output. Means include unsuccessful scored reruns. These saved measurements cover replay and exclude the original faulty run, surrounding cleanup, and final correctness audit. Current recovery timing also includes dependency selection and effect cleanup, so reproducing historical timings requires the original source revision.

| Model | Method | Cost records | Stages redone / 9 | Tokens | Seconds |
|---|---|---:|---:|---:|---:|
| GPT-OSS 120B | Restart | 96 | 9.00 | 3,340 | 31.76 |
| GPT-OSS 120B | Suffix | 96 | 5.35 | 1,845 | 16.94 |
| GPT-OSS 120B | Coarse | 96 | 4.99 | 1,607 | 13.87 |
| Muse Glimmer 30B | Restart | 95 | 9.00 | 4,207 | 72.01 |
| Muse Glimmer 30B | Suffix | 95 | 5.39 | 2,478 | 43.22 |
| Muse Glimmer 30B | Coarse | 94 | 5.00 | 2,323 | 40.37 |

The following table reports the cost of complete forward runs.

| Model | Condition | Cost records | Mean tokens | Mean seconds |
|---|---|---:|---:|---:|
| GPT-OSS 120B | Clean | 10 | 3,297 | 34.82 |
| GPT-OSS 120B | Always-verify, fault cases | 119 | 8,194 | 78.35 |
| Muse Glimmer 30B | Clean | 10 | 4,172 | 69.44 |
| Muse Glimmer 30B | Always-verify, fault cases | 119 | 10,121 | 177.03 |

## Additional results

### Travel authorization cases

Three simulated cases test unauthorized email, booking without approval, and cancellation of a protected booking. Each has clean, injected, verification, and restart conditions.

GPT-OSS passes 2 of 3 clean travel cases. The planning-only task fails with no attack, and restart makes one previously passing case incorrect. Muse passes all 3 cases under clean, no recovery, verification, and restart. Neither model attempts an unauthorized action in these cases.

### Coding and graph checks with Qwen2.5-7B-Instruct

Qwen also evaluated three small coding programs and four synthetic dependency graphs. On the coding examples, the same support rule retained too much work and failed `clamp` and `clip_text` after a requirement was removed.

The fault note also had mixed results. For `clamp` with a missing requirement, monitor used 2,109 tokens and 13.87 seconds, compared with coarse's 1,453 tokens and 8.22 seconds. On the synthetic graphs, monitor produced three incorrect outputs where coarse succeeded. Those cases are in Appendix C. [Appendix, page 4](docs/phase1_report.pdf#page=4) The [table-generation script](analysis/phase1_tables.py) reproduces these comparisons from committed rows.

The report discusses the failure cases, limitations, and planned recovery improvements in “Limitations and Phase 2” and Appendices A–C. [Section 6, page 2](docs/phase1_report.pdf#page=2) [Appendix, page 4](docs/phase1_report.pdf#page=4)

## Setup and reproduction

Use Python 3.11+, [uv](https://docs.astral.sh/uv/), and Docker for coding evaluation. Local Qwen experiments also require llama.cpp. Run commands from the repository root.

```bash
uv sync --locked
uv run pytest -q
```

### Travel booking

Start the local model server in one terminal. On macOS, install llama.cpp with Homebrew. The first server launch downloads Qwen2.5-7B-Instruct Q4_K_M (about 4.7 GB).

```bash
brew install llama.cpp
./scripts/start_server.sh
```

In another terminal, run the clean baseline and the two-seed booking experiment.

```bash
uv run agentrec clean --tasks 2
uv run agentrec experiment --tasks 2 --faults all \
  --methods restart,suffix,coarse,support,monitor,support_monitor
```

The CLI writes each experiment under `results/`. Summarize a run using its printed output directory.

```bash
uv run python analysis/summarize.py results/<run-directory>/rows.jsonl
```

The server uses temperature 0, seed 0, and one inference slot. Historical booking scores use the checker recorded with their artifacts. To reproduce those scores, use the source revisions and commands listed in the [artifact guide](artifacts/phase1/README.md).

### Python coding

Download the pinned dataset and container image. Docker must be running.

```bash
mkdir -p data/mbpp
curl -fL https://raw.githubusercontent.com/google-research/google-research/83f1e7b59e762648c953de751d31f75720683622/mbpp/sanitized-mbpp.json \
  -o data/mbpp/sanitized-mbpp.json
docker pull python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893
```

Set `MODEL_API_URL` to your OpenAI-compatible endpoint (including `/v1`), `MODEL_API_KEY` to its key, and `MODEL_ID` to its model identifier. Then run the ten-task evaluation.

```bash
uv run agentrec mbpp run --provider custom \
  --api-base-url "$MODEL_API_URL" --api-key-env MODEL_API_KEY \
  --model "$MODEL_ID" --limit 10 --workers 5 \
  --trial-out results/coding-10 --execute-model
```

This runs clean, injected, verification, and recovery conditions. Add `--include-authorization` to include the three travel authorization cases used in the reported batches. Use `--phase clean` for a clean-only coding run. `--from 11 --to 50` schedules prepared tasks 11 through 50. Omit `--limit`, `--from`, and `--to` to schedule all 50. Each output directory must be new.

To continue an interrupted batch, repeat the same model, task, and worker settings with `--resume-from results/coding-10` and a new `--trial-out` directory. If the saved status still says `running`, first verify the old process has stopped, then add `--resume-source-stopped`. Completed results are copied into the continuation. Failed conditions remain recorded.

Model-specific configuration and the complete fault schedule are documented in the [coding experiment guide](evals/README.md).

### Additional coding and graph experiments

With the local model server running, evaluate the small programs and graph templates.

```bash
uv run agentrec graphs
```

For a mock-only harness check, use `uv run agentrec --llm mock graphs`. The [travel and external benchmark guide](evals/travel-and-external.md) describes the graph shapes and adapter commands.

## Results and logs

| Evidence | Available files | Coverage |
|---|---|---|
| Qwen booking and graph experiments | [Rows, traces, and reproduction instructions](artifacts/phase1/README.md) | Source revisions and logs supporting the Qwen tables. |
| Muse coding and authorization | [580 result rows](artifacts/phase1/muse-coding/rows.jsonl), [logs and configuration](artifacts/phase1/muse-coding/) | Complete saved batch, including four execution errors and three skipped recovery rows. |
| GPT-OSS coding and authorization | [579 result rows](artifacts/phase1/gpt-oss-coding/rows.jsonl), [logs and configuration](artifacts/phase1/gpt-oss-coding/) | Complete saved batch, including one execution error. See [artifact notes](artifacts/phase1/gpt-oss-coding/README.md). |

Each coding directory contains result rows, traces, configuration, and the selected task manifest. Its README links to the deployed source version and original run metadata. See the [artifact guide](artifacts/phase1/README.md) for file descriptions and notes for each run.

## External benchmarks

Adapters provide additional workloads and failure records. UndoBench exercises a retry before a state-changing action. A2ASecBench checks whether untrusted agent messages lead to blocked actions. TAMAS supplies attack scenarios, and Who&When supplies failure traces for attribution. These adapters are described in the [external benchmark guide](evals/travel-and-external.md). Recovery after completed external actions remains future work.

Fetch and inspect the external datasets with the following commands. Adapter-specific dependencies and trial settings are listed in the external benchmark guide.

```bash
uv run agentrec tamas download
uv run agentrec tamas summary
uv run agentrec a2a download
uv run agentrec a2a summary
uv run agentrec undobench download
uv run agentrec who-when download
uv run agentrec who-when summary
```

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
| [docs/phase1_report.tex](docs/phase1_report.tex) | Report source. From `docs/`, run `pdflatex` twice. The figure is `monitor_compact.pdf`. |
| [Phase 1 report (PDF)](docs/phase1_report.pdf) | Design, experiments, results, and limitations. |
