# agent-recovery

Fault injection and selective recovery for multi-agent LLM workflows (CS 291A, Option 5).

Agents pass results to one another. An incorrect or malicious result can affect later decisions, and a full restart repeats work that was already correct. This repository records which results depend on which, then compares recovery methods that are all given the same fault location. Detection is not part of the comparison.

The current pilot is the evaluation merged in [#10](https://github.com/UtkarshRjn/cs291a-agent-recovery/pull/10): 10 of 50 pinned MBPP coding tasks, plus 3 simulated travel cases, run on GPT-OSS 120B and Muse Glimmer 30B. The same repository still contains the travel booking harness, graph topologies, and the external benchmark adapters.

The Phase 1 writeup is in [docs/phase1_report.tex](docs/phase1_report.tex) ([PDF](output/pdf/phase1_report.pdf)). Commands and fault definitions for the coding pilot are in [evals/README.md](evals/README.md). The travel harness and external benchmarks are in [evals/travel-and-external.md](evals/travel-and-external.md).

## Phase 1 submission

| Required item | Where |
|---|---|
| Report | [docs/phase1_report.tex](docs/phase1_report.tex), [PDF](output/pdf/phase1_report.pdf) |
| Source code | [src/agentrec/](src/agentrec), tests in [tests/](tests) |
| Dataset | [evals/mbpp-50.json](evals/mbpp-50.json) (50 pinned MBPP tasks); booking tasks and catalogs are generated from a seed by `make_task` in [src/agentrec/tasks.py](src/agentrec/tasks.py); the three coding programs are in [src/agentrec/programs.py](src/agentrec/programs.py) |
| Experiment instructions | [Setup](#setup) below, [evals/README.md](evals/README.md) for the coding pilot, [artifacts/phase1/README.md](artifacts/phase1/README.md) for the Qwen runs |
| Results and logs | [artifacts/phase1/](artifacts/phase1): rows and traces behind the Qwen booking, monitor, support, and coding-program numbers; rebuild with `uv run python analysis/phase1_tables.py` |

The GPT-OSS and Muse rows and traces behind the tables below have not been added yet.

## Results

Both models use the same 10 coding tasks and 3 travel variants. Each task and condition is one observation. Temperature is 0, the seed is 0, and each call allows 2,048 output tokens. Five tasks run in parallel. Conditions inside a task run one after another.

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

Each task has 12 scheduled fault/stage combinations, so 10 tasks make 120 attempts. GPT-OSS confirmed 96 injections. Muse confirmed 95. The other attempts had no suitable target and are not counted as successful defenses. Muse also had one faulty run with no saved state, so each recovery method skipped that row. Coarse's Muse score counts one API error as unsuccessful. Its cost row below uses the 94 reruns that finished with a cost record.

Recovery scores include runs that already passed without recovery. A rescue rate still needs those 33 GPT-OSS failures, and the corresponding Muse failures, matched one by one. The two models do not have the same eligible set. Always-verify uses all 120 scheduled cases and is not ranked directly against the recovery fractions.

### Recovery cost

Means cover the recovery rerun only. They include unsuccessful scored reruns and exclude the original faulty run.

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

The saved batches contain 579 GPT-OSS rows and 580 Muse rows. GPT-OSS retains one execution error. Muse retains four execution errors and three skipped recovery rows.

### Travel

GPT-OSS passes 2 of 3 clean travel cases. The planning-only task fails with no attack, and restart makes one previously passing case incorrect. Muse passes all 3 cases under clean, no recovery, verification, and restart. Neither model attempts an unauthorized action in these cases.

## Setup

Coding runs need Python 3.11+, uv, and Docker. Travel runs that use the local model also need llama.cpp.

```bash
uv sync --locked
uv run pytest -q

# Coding pilot. Put NRP_API_KEY or TRITON_API_KEY in the environment.
mkdir -p data/mbpp
curl -fL https://raw.githubusercontent.com/google-research/google-research/83f1e7b59e762648c953de751d31f75720683622/mbpp/sanitized-mbpp.json \
  -o data/mbpp/sanitized-mbpp.json
docker pull python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893

uv run agentrec mbpp run --provider nrp --model gpt-oss \
  --limit 10 --include-authorization --workers 5 \
  --trial-out results/nrp-10 --execute-model

uv run agentrec mbpp run --provider triton --model api-muse-glimmer-30b \
  --limit 10 --include-authorization --workers 5 \
  --trial-out results/triton-10 --execute-model
```

Omit `--limit` to schedule all 50 coding tasks. That full matrix has not been run. Resume a stopped batch into a new directory with `--resume-from results/previous-run`. The source directory is left unchanged.

Local travel inference:

```bash
brew install uv llama.cpp          # macOS
./scripts/start_server.sh          # downloads Qwen2.5-7B-Instruct Q4_K_M (~4.7 GB)
```

That server comparison uses `bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M`, temperature 0, seed 0, and one server slot.

## Coding pilot

A coding task runs nine stages. A normal run makes seven model calls. Tests run in Docker, and the review decision is computed from the test report. The inherited reviewer model call is still counted.

```text
Specification → Code → Test → Review → Documentation
              → Specification → Code → Test → Review
```

One dataset assertion is revision feedback. The remaining assertions stay hidden and score only the final code. Success means the submitted file passes those final assertions.

The seven fault families are a bad patch, a flipped comparison, a dropped requirement, a tester timeout, an instruction hijack, a false test report, and a stale test report. The first five are tried on both passes. False reports target pass 1. Stale reports target pass 2. Accidental and adversarial faults are labelled. False-report and stale-report cases use fixed bad code so the misleading feedback is meaningful, and each has a truthful-report control.

| Method | What it does |
|---|---|
| clean | Run with no injected fault. |
| verify-clean | Run with no fault, and check intermediate outputs. |
| none | Inject one fault and finish. |
| verify | Check intermediate outputs during the faulty forward run. |
| restart | Undo supported effects and rerun every stage, with injection off. |
| suffix | Rerun the faulty stage and every later stage. |
| coarse | Rerun stages selected from the recorded dependency graph. |

The graph is built from the inputs each stage declares. A later stage can be kept when it did not read the faulty result. Suffix and coarse fail the same ten GPT-OSS stale-report cases: the old passing report is labelled faulty, retesting sees the bad code, and the coder is not reopened.

## Travel booking harness

The original workflow is researcher, planner, and executor against a simulated world. Search, booking, files, and email are local tools.

| Tool | Class | Recovery |
|---|---|---|
| `write_file` | reversible | restore the previous contents |
| `book_flight`, `book_hotel` | compensatable | cancel the booking |
| `send_email` | irreversible | recorded; cannot be undone |

Eleven booking faults cover wrong or stale flight results, timeouts, fabricated hotels, a false refundable claim, a swapped hotel id, a malicious instruction, a bad tool choice, a bad flight id, a dropped constraint, and an inexecutable plan. Persistent faults recur on retry. Recovery reruns use no injector.

```bash
uv run agentrec --llm mock experiment --tasks 3          # plumbing only
uv run agentrec clean --tasks 20
uv run agentrec experiment --tasks 5 --faults all \
  --methods restart,suffix,coarse,support,monitor,support_monitor
uv run python analysis/summarize.py results/experiment-server-*/rows.jsonl
```

Booking methods, in addition to the coding set:

- **support** replays the producer of a faulty claim and the steps that cite it. A booking that cites only the flight id stays when the hotel-id claim is the fault.
- **monitor** uses the coarse restart set and appends `FAULT_JSON` to agents that run again.
- **support_monitor** uses the support restart set with the same note.

`task_recovery_success` checks the final task state. `final_effect_integrity` covers the whole history. `recovery_effect_integrity` rejects wrong, duplicate, or unauthorized effects introduced during recovery. Earlier irreversible harm is reported separately. A fault counts as used only when it changes the outcome relative to the clean run of the same task.

The three authorization variants in the coding pilot are separate from this sweep. They check an unauthorized recipient, booking without approval, and cancellation of protected booking `PROTECTED-001`.

## Graph templates

`uv run agentrec graphs` compares the same recovery methods on four small dependency shapes and on a short coding team (`clamp`, `nonzero`, `clip_text`). `--llm mock` prints the tainted sets and does not call a model.

| Template | Call order | Who reads what |
|---|---|---|
| chain | `a b c d` | each step reads the previous one |
| diamond | `a b c d` | `b` and `c` read `a`; `d` reads both |
| join | `p q r join` | the three sources read only the task; `join` reads all three |
| backedge | `a b c a:2 side` | `a:2` reads `c`; `side` reads only the first `a` |

Suffix and coarse name the same steps on a chain. They split when a later step never read the fault.

## External benchmarks

These adapters do not replace the coding or booking results.

```bash
uv run agentrec tamas download && uv run agentrec tamas summary
uv run agentrec a2a download && uv run agentrec a2a summary
uv run agentrec a2a trial --config configs/offline/asrf_eval.yaml --agentrec
uv run agentrec undobench download
uv run agentrec undobench trial --suite smoke --recovery agentrec
uv run agentrec who-when download && uv run agentrec who-when summary
```

[TAMAS](https://github.com/microsoft/TAMAS) trials use a paid model API. [UndoBench](https://github.com/tradertanmay/undobench) here retries a known pre-mutation fault once. It does not roll back a trajectory after a committed effect. [A2ASecBench](https://github.com/SaFo-Lab/A2ASecBench) measures whether an untrusted peer message is blocked. [Who&When](https://huggingface.co/datasets/Kevin355/Who_and_When) logs are loaded for attribution. Their original agent tools are not replayable here. Mock attribution numbers are not results.

## Layout

```
src/agentrec/
  mbpp.py              50-task MBPP runner, Docker scoring, resume
  phase1.py            coding fault schedule and stage metrics
  benchmark_faults.py  coding fault implementations
  authorization.py     three travel policy cases
  coding.py            nine-stage coding workflow
  programs.py          hidden-suite task helpers
  conditions.py        per-condition error isolation
  resume.py            copy completed rows and continue the rest
  workflow.py          travel and coding orchestrator
  agents.py            prompts
  llm.py               llama-server, NRP, Triton, and mock clients
  faults.py            booking fault injector
  provenance.py        dependency graph
  recovery.py          restart, suffix, coarse, and support
  monitor.py           fault note on a restart set
  checker.py           booking outcome checks
  world.py             simulated bookings, files, and email
  graphs.py            chain, diamond, join, and back-edge cases
  runner.py            CLI
  tamas.py  a2a.py  a2a_adapter.py  undobench.py  undobench_adapter.py  who_when.py
analysis/summarize.py
analysis/phase1_tables.py   Qwen tables in the report, from artifacts/phase1
artifacts/phase1/           committed rows and traces behind the report
evals/mbpp-50.json
evals/README.md
evals/travel-and-external.md
docs/phase1_report.tex
```

## Troubleshooting

`ModuleNotFoundError: No module named 'agentrec'` on macOS: when the repo sits in a synced folder such as iCloud Documents, the editable-install `.pth` file can get the `hidden` flag, and Python 3.12 skips hidden `.pth` files.

```bash
chflags nohidden .venv/lib/python3.12/site-packages/*.pth
```

If the flag keeps coming back, keep the virtual environment outside the synced folder:

```bash
rm -rf .venv && uv venv --python 3.12 ~/.venvs/agent-recovery
ln -s ~/.venvs/agent-recovery .venv && uv sync
```

## Known limitations

- The reported pilot covers 10 of the 50 prepared coding tasks, with one observation per condition. There are no confidence intervals yet.
- Mock-model runs check the harness. They are not model results.
- Coarse booking dependencies treat the whole plan as an input to every executor step, so a research fault taints all execution.
- Suffix and coarse do not reopen code generation when only a stale passing report is labelled faulty.
- The travel world is simulated. Coding execution is the part that uses containers.
- UndoBench recovery in this repo stops at a pre-mutation retry. A2ASecBench recovery here blocks an action. It does not undo one that already happened.
