# Expanding the paired experiments

Branch: `expand-benchmark-examples`, based on Utkarsh's `origin/coding-suite`
commit `a889670`. The TAMAS checkout and its uncommitted work are separate.

## Workloads

- `mbpp-pilot.json`: five distinct sanitized MBPP problems (IDs 2, 3, 4, 7, 8).
- `mbpp-50.json`: 50 distinct problems selected by ascending ID, restricted to
  reference implementations with one top-level function, stdlib imports, and
  at least three tests. Selection does not depend on model performance.
- `travel-50.json`: 50 generated instances of the existing travel template,
  seeds 0–49. This is an inspectable snapshot; the existing travel CLI still
  calls `make_task(seed)` directly. It is not 50 different workflow types.

MBPP source: Google Research, `mbpp/sanitized-mbpp.json`, revision
`83f1e7b59e762648c953de751d31f75720683622`. Full source URL and SHA-256 are in
both manifests. The original problems, reference implementations, and assertions
are attributed to [MBPP](https://github.com/google-research/google-research/tree/83f1e7b59e762648c953de751d31f75720683622/mbpp).
The manifests include evaluator-private solutions/tests: never pass an entire
manifest to an agent. Only the task request, entry/signature, and file paths are
placed in its task context.

This is a custom subset and test partition, NOT official MBPP or MBPP+ scores.
The first assertion is used during both tester visits; its pass/failure report
is visible for revision. Remaining assertions are final-only. They are never
used by the team, verifier, or recovery scheduling. Two held-out assertions per
problem are a weak correctness check, not exhaustive validation; expanding tests
with an independently pinned EvalPlus adapter remains future work.

## Prepare and validate without model calls

```sh
uv sync
mkdir -p data/mbpp
curl -fL https://raw.githubusercontent.com/google-research/google-research/83f1e7b59e762648c953de751d31f75720683622/mbpp/sanitized-mbpp.json \
  -o data/mbpp/sanitized-mbpp.json
uv run agentrec mbpp prepare
uv run agentrec mbpp prepare --count 50 --manifest evals/mbpp-50.json
docker pull python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893
uv run agentrec mbpp validate --manifest evals/mbpp-50.json \
  --report results/mbpp-50-validation.json
```

The source and manifest are rechecked before execution. Docker evaluates code
with no network, no host mounts or credentials, a read-only root filesystem,
non-root user, resource caps, and a 15-second wall timeout. Docker must be running.
A successful reference check is evidence that the test environment works, not
that an LLM can solve these tasks.

## Model pilot (not yet run)

```sh
uv run agentrec mbpp run --provider nrp --model gpt-oss \
  --phase clean --manifest evals/mbpp-pilot.json \
  --trial-out results/mbpp-clean-pilot --execute-model
```

NRP uses the `NRP_API_KEY` environment variable and the fixed official endpoint.
It does not automatically load `.env`. The key is not saved in configuration.
Local serving remains available via `--provider local` at localhost:8080/v1.
Explicit model name, temperature, seed, token cap, container image ID, and dataset
manifest are saved. Remote reproducibility is best-effort; an alias/seed does not
pin serving weights or guarantee determinism. Model exceptions stop new task scheduling and request cancellation of other workers
before their next model call. In-flight requests may finish or reach the configured
request timeout. Completed rows and per-task `INTERRUPTED.json` files are retained.
The current incomplete workflow is not serialized on exceptions. Inspect each pilot trace
before moving to the 50-task manifest.

The existing workflow is retained:

`spec → coder → tester → reviewer → docs → spec:2 → coder:2 → tester:2 → reviewer:2`

This makes seven model calls and two feedback-test executions per clean workflow.
The reviewer LLM's output is discarded by the inherited implementation; the actual
review decision is derived from the tester report. Its inference cost is still
counted. This is not a new autonomous reviewer baseline.

## Paired runs after the clean pilot

```sh
uv run agentrec mbpp run --provider nrp --model gpt-oss \
  --phase paired --manifest evals/mbpp-pilot.json \
  --methods restart suffix coarse support monitor support_monitor \
  --trial-out results/mbpp-paired-pilot --execute-model
```

Default paired stages: `coder`, `coder:2`; override with `--fault-stages`.
A single transient `bad_patch` replaces the selected function with `return None`.
Only the exact targeted stage is changed and labelled. If the text is already
identical, the fault is not counted as fired. All selected references were checked
to pass, and this mutation was checked to fail final tests. This mutation models
an incorrect intermediate implementation, not a realistic/adaptive adversary.
It does not prove that the original model output was correct.

Clean and faulted runs start fresh with the same task and model settings. They
are paired at the task/configuration level, not identical stochastic trajectories.
`clean_pass_fault_fail` is an observed paired outcome, not proof of causality from
one run. A first-draft fault may be repaired in the normal second lap; report this
rather than discarding it. Add repeated inference seeds in a later evaluation.

The existing provenance and recovery methods are reused. Every recovery method
receives the same faulty-node labels and repaired source assumption. No new LLM
detector is evaluated. `monitor` means coarse recovery plus a fault note;
`support_monitor` is the corresponding support-based variant. `verify` is deliberately
not offered here: the inherited coding workflow does not implement that baseline.
Task-specific requirement deletion is also not generalized to MBPP in this change.

Rows contain final test outcome, clean success for fault/recovery rows, fired fault,
inference usage, workflow/recovery latency, and recovery stage reuse/recomputation.
Traces contain provenance graphs and simulated files. Recovery rows include total
faulted-run plus recovery inference usage. There are no real bookings/emails in
these coding tasks, so they do not validate irreversible-side-effect recovery.
Report coding and travel separately; retain clean failures in the overall results.

## Offline validation completed

- 66 repository tests passed, including test-split routing, private-field boundaries,
  exact-stage fault injection, and paired/restart orchestration tests with fixed
  answers (these are tests, not model performance measurements).
- 50/50 reference solutions passed feedback and final tests in Docker.
- 50/50 `return None` mutations failed final tests.
- `mbpp-50-validation.json` records these environment checks.
- No LLM/API experiment was run for this expansion.

## Parallel task execution

`mbpp run --workers 2` runs at most two task examples concurrently. Default: 1.
Each task has its own model client/usage counters, world, provenance, and recorder.
The immutable program registry is loaded before workers start and read by workers.
Stages, fault conditions, and recovery methods remain sequential within each task.
At most `workers` model requests or Docker evaluations can be active at a time;
containers retain their individual memory/CPU/network limits. The image is reused.
This does not add parallel execution to `mbpp validate` or the travel runner.

```sh
uv run agentrec mbpp run --provider nrp --model gpt-oss \
  --workers 2 --phase clean --manifest evals/mbpp-50.json \
  --trial-out results/mbpp-clean-parallel --execute-model
```

This command is documented, not launched automatically.

Output layout for new runs (including `--workers 1`):

- `tasks/mbpp_<id>/rows.jsonl` and `traces/`: independent per-task results.
- Root `rows.jsonl`: atomic, manifest-ordered merge, refreshed as tasks finish.
  A running task's partial rows are available in its own directory, not yet merged.
- `run_status.json`: worker count, elapsed batch time, and each task's state:
  not_started, running, completed, failed, or cancelled. Completed means execution
  finished; consult the result row for correctness.

A failure stops new submissions; already active work cancels cooperatively before
its next model request. Ctrl-C also requests cancellation and waits for active
requests/evaluations to finish or time out before merging partial results. A hard
process kill cannot perform this cleanup. No automatic retry or resume is added.

Record worker count with results. Parallel per-task wall times include contention;
compare batch throughput separately from sequential latency. Shared inference
servers may yield different outputs even at the same seed.

Concurrency validation: 70 repository tests passed, including bounded overlap,
independent clients/traces, deterministic aggregation, invalid worker counts, and
failure cancellation with partial-result retention. No new model calls were made.

## Additional fault families

The optional five-family suite is documented in [FAULTS.md](FAULTS.md), including
primary sources, precise adaptations, eligibility, threat models, and reporting limits.
Use `--fault-types` to opt in; existing runs retain `bad_patch` as their default.

## Model endpoints and credentials

The MBPP runner supports `--provider local|nrp|triton|custom`.
NRP uses `https://ellm.nrp-nautilus.io/v1` and `NRP_API_KEY`.
Triton uses `https://tritonai-api.ucsd.edu/v1` and `TRITON_API_KEY` (the user's name).
The official Triton docs use `TRITONAI_API_KEY`; select it explicitly with
`--api-key-env TRITONAI_API_KEY` if that is your environment variable.

Override any preset with `--api-base-url URL` (alias `--base-url`, after `mbpp run`).
Select credentials with `--api-key-env VARIABLE_NAME` or `--api-key-file /outside/repo/key`.
A key file contains only the key, not shell assignments. No fallback to personal or
OpenAI credentials occurs. The resolved secret is never placed in `config.json` or
forwarded into the test containers. Configuration records the endpoint and credential
source, not its value. Do not put secrets in URL parameters or CLI arguments.

Example (replace MODEL_ROUTE with an allowed chat model route):

```sh
uv run agentrec mbpp run --provider triton --model MODEL_ROUTE \
  --api-key-env TRITON_API_KEY --omit-seed \
  --token-limit-field max_completion_tokens \
  --manifest evals/mbpp-pilot.json --phase clean --workers 5 \
  --trial-out results/triton-clean-pilot-001 --execute-model
```

Generic endpoint equivalent: `--provider custom --api-base-url https://HOST/v1
--api-key-env PROJECT_MODEL_KEY`. No authentication is sent for custom/local unless
explicitly selected. NRP/Triton fail before Docker startup or inference if their selected
key is missing. Use a project key, not an existing personal key.

Triton documents model-specific `supported_openai_params`. Use `--omit-temperature`
when needed, `--omit-seed` for routes without seed, and `--no-json-mode` for routes
without `response_format`. Disabling JSON mode does not disable JSON parsing: the
role prompts still require JSON and malformed replies still fail. Request options
are explicit and recorded; no silent retries or parameter downgrades are performed.
Compatibility has been tested with mock HTTP requests only, not live Triton models.
Source: https://docs.tritonai.ucsd.edu/llm-api/chat-completions

## Dedicated AWS credentials for provisioning

Configure a separate local AWS CLI profile, outside this repository:

```sh
aws configure --profile agent-recovery
```

Enter the project AWS access key ID, secret access key, region (e.g. `us-east-1`),
and output format `json`. Temporary credentials also require `aws_session_token`
in that profile. Alternatively use the project's SSO profile via `aws configure sso
--profile agent-recovery` and `aws sso login --profile agent-recovery`.

Supply only the profile name and region in chat. Use explicit `--profile agent-recovery`
for provisioning; never fall back to the default/personal profile. An AWS SSH `.pem`
key is distinct from API credentials. The project identity needs EC2 provisioning
permissions for the intended instance, networking, volumes, tags, and access method;
missing permissions must be reported rather than switching identities. Do not copy
provisioning credentials into generated-code containers or instance user-data.
Source: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html

The [extended Phase 1 protocol](PHASE1.md) adds controlled report comparisons,
semantic mutation, timeout, verification, post-hoc propagation audits and an optional
simulated stateful travel pilot. Select `--phase extended --include-travel`.
