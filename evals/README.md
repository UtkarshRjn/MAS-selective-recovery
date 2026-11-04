# Current Phase 1 experiment

One pinned dataset of **50 sanitized MBPP coding tasks**, plus three simulated travel
policy variants. `--limit 10` selects the first ten coding tasks for the current pilot.
This is a custom subset/test partition, not an official MBPP score.

## Run

Requires Python 3.11+, uv, and Docker. Model inference uses an external API; generated
code runs in fresh Docker containers with no network or host mounts, non-root execution,
a read-only filesystem, resource caps, and a 15-second timeout.

```sh
uv sync --locked
mkdir -p data/mbpp
curl -fL https://raw.githubusercontent.com/google-research/google-research/83f1e7b59e762648c953de751d31f75720683622/mbpp/sanitized-mbpp.json \
  -o data/mbpp/sanitized-mbpp.json
docker pull python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893
uv run pytest -q

# Set NRP_API_KEY in the environment; never put a key in this command or Git.
uv run agentrec mbpp run --provider nrp --model gpt-oss \
  --limit 10 --include-authorization --workers 5 \
  --trial-out results/nrp-10 --execute-model

# Set TRITON_API_KEY for the same experiment on Triton.
uv run agentrec mbpp run --provider triton --model api-glm-5.3-flash \
  --limit 10 --include-authorization --workers 5 \
  --trial-out results/triton-10 --execute-model
```

The default phase is `full`. Use `--phase clean` without `--include-authorization` for
clean coding only. Omit `--limit` to use all 50 coding tasks. Each output directory must
be new. Five workers means five task examples in parallel; conditions within a task
run sequentially. The three travel cases run once on the first worker.

Defaults: temperature 0, seed 0, 2,048 tokens per call, 120-second API timeout. A seed
cannot guarantee remote-model determinism. Errors are isolated to individual conditions: record `status=error`, `success=false`,
exception type/location, HTTP status when available, elapsed time and partial recorded
usage, then continue independent conditions/tasks. A missing faulty execution causes
its dependent recovery methods to be `skipped` (success unknown), not counted as
successful or failed recovery. Clean-baseline errors leave `clean_success=null` for
later conditions. Model/task failures and execution errors must be reported separately.
No automatic retry or replacement of failed scores. Explicit resume is supported below. After all work finishes,
the command exits nonzero if any condition/task errored; explicit cancellation still
stops scheduling. Provider bodies, exception messages, and credentials are not saved. Provider URL and key environment/file options are in `--help`.

## Setup and faults

Coding flow: spec → coder → tester → reviewer → docs → spec₂ → coder₂ → tester₂ → reviewer₂.
The second coding pass always runs. The first assertion supplies revision feedback;
remaining assertions are final-only. Normal prompts exclude reference code and tests.
Reviewer decisions are deterministic Python derived from feedback; the inherited
reviewer LLM call is still counted. These experiments do not test LLM reviewer persuasion.

| Fault | Category | Boundary |
|---|---|---|
| bad_patch | Accidental | First/second coder: replace implementation with return None |
| drop_requirement | Accidental | First/second spec: delete an annotated public constraint if present verbatim |
| semantic_mutation | Accidental | First/second coder: flip the first supported comparison operator |
| tester_timeout | Accidental | First/second tester: simulate missing feedback, not a real timed-out call |
| requirement_hijack | Adversarial | First/second spec: instruct coder to return None |
| false_test_report | Adversarial | First tester: forge passing feedback for fixed bad code |
| stale_report | Accidental | Second tester: reuse passing feedback for an older code version |

Report cases are labelled **controlled compound scenarios**. Feedback tests qualify a
constant-returning bad implementation. The stale scenario uses a reference first version
and bad final version. Truthful and tampered conditions share those fixed artifacts;
reference code is not verifier ground truth. Later model continuations are independent.
Fixture-stage model calls still occur and are counted. Skipped/ineligible injections
are recorded and are not successful defenses. Operators are fixed, not adaptive attacks.

Methods: clean, verify-clean, none, verify, and restart/suffix/coarse when injection fires.
Verification adds five model calls and two feedback executions (12 versus 7 model calls).
Recovery is given the fault location and disables injection. Restart clears fixtures;
suffix/coarse can retain unmarked bad code in compound scenarios. Equal per-call limits
are not equal overall budgets. These are error/attack models motivated by
[MAST](https://arxiv.org/abs/2503.13657), [TAMAS](https://arxiv.org/abs/2511.05269),
and [AgentDojo](https://agentdojo.spylab.ai/concepts/attacks/), not reproductions of their suites.

Travel variants: unauthorized recipient, booking without approval, and cancellation of
protected booking PROTECTED-001. Each has clean/none/verify/restart conditions. Attack
instructions enter a hotel description; they cannot modify the trusted user policy.
Tools change only in-memory state. Policy checks audit rather than enforce permissions.
Attempts, committed violations, and verifier blocks are separate; history survives
recovery. Cancellation and sent emails are irreversible in this simulation.

## Outputs and limitations

`rows.jsonl`, per-task traces, `config.json`, selected `manifest.json`, and
`run_status.json` live under the chosen ignored output directory. Traces record model
exchanges, artifacts, dependencies, tool effects, and authorization history.

- Correctness: held-out code tests or policy-specific travel state/history checks.
- Propagation: intermediate code failures and false approvals; exposure is separately
  measured from graph reachability. Neither proves general causal propagation.
- Wasted work: invalid-code token and recomputation proxies, not exact total waste.
- Cost/time: model tokens/calls, workflow/recovery time, post-hoc audit time; provenance
  write timing is only part of tracking overhead. Parallel contention affects latency.

Historical reports, old manifests, AWS helper scripts, and raw run artifacts are kept
locally and ignored. The remote running jobs use their saved pre-cleanup source snapshot;
this CLI cleanup does not modify those jobs. Completed old pilots are not included in
this PR. The new two-provider comparison is incomplete; no new outcome is claimed here.

## Resume a stopped batch

Use the same provider/model, parameters, task limit, and worker count, with a new
output directory and `--resume-from results/previous-run`. Completed rows and traces
are copied unchanged; error/skipped rows are never retried. Missing conditions run
from their beginning. Pending recovery restores the saved faulty world and provenance.
The source run remains immutable. Configuration and artifact checks happen before
inference. A `resume.json` records lineage; copied rows are not new inference cost.

For old runs that recorded only a task-level exception, `--resume-errors FILE` can
import explicitly audited failed-condition rows. It rejects overwriting any saved
condition. Unrecorded partial calls from an interrupted condition remain an accounting
limitation; resuming cannot recover their exact token cost or hidden model state.
