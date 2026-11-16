# Online monitor evaluation

This guide covers the extended evaluation of the online claim-level monitor. It has four parts:

- a mechanism suite with deterministic agents;
- a coding pilot derived from AsynCodeBench;
- a stateful pilot derived from WorkBench;
- two-fault motifs derived from ParaRecover.

Every part is a new experimental condition. No row is an official AsynCodeBench, WorkBench, or ParaRecover result, and each row carries `official_result_eligible: false`.

## What every method receives

All policies run on one runtime and one tool gateway. A fault cell gives every policy the same inputs:

- the same source tools;
- the same checker, which is source-linked or derivational and never a truth table;
- the same corrector, which re-queries sources or re-asks the same agent;
- the same action contracts;
- the same failed-check timing.

Detection timing comes from the case's clean reference timeline or from a named monitor event, not from the policy under test. No policy sees ground-truth outcomes, clean parent values, correct replacements, or hidden evaluator tests. In the pilots, agents see only what the benchmark gives a participant: the task text, their role, their tools or files, and upstream artifacts.

## Policies

`src/agentrec/policies.py` defines each policy as a set of runtime switches.

| Group | Policies |
|---|---|
| Core | `restart`, `suffix`, `coarse` (producer-level), `claim_rerun`, `online`, `verify_before`, `advisory` (CoAgent-style) |
| Extended | `posthoc_repair`, `storm_style`, `atomix_style`, `langgraph_style`, `asyncodebench_manager_style` |
| Ablations | the 2×2 of claim invalidation × external action gate; cancellation on/off with fencing kept; `no_correction_model`, `unversioned_handoff`, `file_lock`, `no_applicability`; boundary controls `online_cas`, `online_no_lock`, `naive_retry`, `no_reconcile` |

A policy named "-style" is our adaptation of a published system's mechanism. Its failures are not failures of the published system.

## Outcomes and statistics

A run is a safe completion only if every goal is verified and its full effect history has no forbidden, duplicate, or uncertain effect. Other runs are classified as correct-but-unsafe, safe-but-incomplete, unsafe-incomplete, timeout, or infrastructure error.

CRSR_safe is computed on the common clean-success population. It counts runs that succeed both clean and under the fault, divided by runs that succeed clean. Unconditional safe success and clean performance are reported alongside it.

`agentrec mechanism compare` gives two paired tests: a cluster bootstrap over task or template clusters, and an exact McNemar test over clean-eligible pairs. Each row also records:

- latency: p50/p95 makespan and verdict-to-restored time;
- cost: all tokens, tokens spent after cancellation, and cancelled requests;
- work: wasted model seconds, the fraction of independent work preserved, and invalidation precision and recall;
- effects: forbidden, duplicate, missing, compensated, and uncertain effects;
- monitor lock wait and hold times;
- the timing ordering each schedule actually realized.

## Mechanism and boundary suites

Families A–F form the mechanism suite. G, H, lost acknowledgment, verified-source revocation, and contention form the boundary suite. Each family has a `dev` template and a held-out `test` template. A run writes `manifest.json`, a configuration hash plus the code identity, and an append-only `rows.jsonl`. Rerunning into the same directory with a different configuration is refused.

```bash
uv run agentrec mechanism run --suite mechanism --seeds 10 --out runs/   # 6 families x 4 schedules x 10 seeds x 7 policies, plus clean twins
uv run agentrec mechanism run --suite boundary  --seeds 10 --out runs/
uv run agentrec mechanism run --suite extended  --seeds 10 --out runs/
uv run agentrec mechanism run --suite ablation  --seeds 10 --out runs/
uv run agentrec mechanism summary runs/mechanism-dev-source_check
uv run agentrec mechanism compare runs/mechanism-dev-source_check online restart
```

Add `--template test` for the held-out templates and `--verifier oracle` for the detection-isolated condition.

## ParaRecover-derived two-fault motifs

`src/agentrec/pararecover/motifs.json` is a frozen, MIT-attributed extract of [ParaRecover](https://github.com/gbw206/ParaRecover)'s `LEVEL-2_real_rollout.jsonl`, pinned to commit 24eadb9. The selection rule lives in `pararecover/extract.py`:

- keep records with at least two coexisting errors, each of which corrupts a named node's output;
- the error analysis must name two tool nodes as fault sites;
- group the two sites' cones as nested, overlapping, or disjoint, and take the first four records in each group.

Two records per group are `dev` and two are held-out `test`.

Each plan runs as a workflow:

- Each node step returns the recorded rollout result when there is one, and a deterministic stand-in otherwise.
- A node whose tool changes state proposes its effect through the gateway. `send_*`-style tools are irreversible; `schedule_*`-style tools are reversible.
- The response is delivered by an irreversible `respond`.
- Two faults hit the named sites. Their schedules are `simultaneous`, `staggered`, and `during_activation`, where the second fault is released while the first repair activates.

```bash
uv run agentrec mechanism run --suite pararecover --seeds 10 --out runs/
uv run python -m agentrec.pararecover.extract /path/to/ParaRecover   # re-derive the extract; tests check it is unchanged
```

ParaRecover scores re-planning over recorded context. These cases instead execute plans under the monitor, so they measure something different and must not be reported as a ParaRecover score.

## Coding pilot (AsynCodeBench v0.4)

Protocol: `agentrec-versioned-artifacts-v1`. `codepilot/protocol.py` declares how it differs from the release's `async_private` protocol.

Setup:

- Each specialist writes a patch artifact whose digest covers paths and contents.
- A checker per specialist runs that specialist's dependency probes on an integrated checkpoint. Its report is bound to the patch, the checkpoint's other patches, the environment, and the probe suite.
- Integration merges in a frozen handoff order and proposes `merge` through the gate. The merge contract requires an applicable passing report for every merged patch.
- An intervention reverts one frozen-eligible producer function to its Commit0 stub. The failed check is released after the consumer starts (`inflight`) or after the merge is proposed (`pre_merge`).

Freeze `pilot.json` once. It records:

- the release commit and file hashes;
- the selection rule and the selected tasks: cachetools and marshmallow, which the plan fixes, plus simpy and requests, chosen by the rule over executable dependency manifests;
- every eligible fault site and the chosen one;
- the handoff order and checker scopes;
- policies, seeds, models, token, step, and wall-clock limits, and the termination rule.

```bash
uv run agentrec codepilot verify-registry --registry /path/to/AsynCodeBench
uv run agentrec codepilot freeze --registry /path/to/AsynCodeBench --out runs/codepilot \
    --model a=MODEL_A@http://host:port/v1 --model b=MODEL_B@http://host:port/v1
uv run agentrec codepilot run runs/codepilot --reference    # pipeline validation only
uv run agentrec codepilot run runs/codepilot                # 4 tasks x 3 conditions x 3 seeds x 5 policies x 2 models = 360 runs
uv run agentrec codepilot summary runs/codepilot/rows
uv run agentrec codepilot official --registry /path/to/AsynCodeBench --task cachetools \
    --env-file .env --model-tag a --run-version v1                 # the release's own Async-Manager clean run
```

Pilot policies: `restart`, `suffix`, `claim_rerun`, `online`, and `asyncodebench_manager_style`. Model agents read the endpoint key from `AGENTREC_API_KEY` or `OPENAI_API_KEY`.

## Stateful pilot (WorkBench)

Protocol: `agentrec-workbench-decomposed-v1`. Four WorkBench multi-domain templates are decomposed in `workbench/templates.py`:

- If a person has overdue tasks, book a meeting and send an email; otherwise send a different email.
- If a customer has gone a fortnight without contact, book a meeting.
- If a metric crossed a threshold, send an email.
- If a metric crossed a threshold, create a front-end task; otherwise send an email.

In each case:

- Each lookup is an agent answering from WorkBench's own read tools on the pristine sandbox.
- `decide` writes the action plan, and `act` proposes each write through the gateway. `email.send_email` is irreversible; `calendar.create_event` and `project_management.create_task` are revertible.
- Checks re-derive a lookup from WorkBench records and re-derive the plan from the lookup versions it cites. Failure reasons never carry the true value.
- The fault corrupts the branch-deciding lookup. It is released after `decide` starts (`inflight`) or after `act` starts (`pre_effect`).

Outcomes are scored twice:

- with WorkBench's own `is_correct` and `has_side_effects` on the final action list, which is applied effects minus reverted ones;
- with the full effect history against the ground-truth multiset.

The selection rule takes, per template, the first three rows in file order whose deterministic reference reproduces the ground truth. All 12 qualify with no exclusions: rows 0–2, 90–92, 150–152, and 160–162. The plan's pilot is 12 instances × 3 conditions × 3 seeds × 5 policies × 2 models = 1,080 runs. The policies are `restart`, `suffix`, `claim_rerun`, `online`, and `atomix_style`.

```bash
git clone https://github.com/olly-styles/WorkBench && git -C WorkBench checkout 49c7dfd00c03d384ec59ea57374f50b766aa5613
uv sync --extra workbench
uv run agentrec workbench freeze --root WorkBench --commit 49c7dfd00c03d384ec59ea57374f50b766aa5613 --out runs/workbench \
    --model a=MODEL_A@http://host:port/v1 --model b=MODEL_B@http://host:port/v1
uv run agentrec workbench run runs/workbench --reference    # pipeline validation only
uv run agentrec workbench run runs/workbench
uv run agentrec workbench summary runs/workbench/rows
uv run agentrec workbench official runs/workbench --model-name gpt-4o   # WorkBench's own inference on the same rows
```

Tests that need a checkout read `AGENTREC_WORKBENCH_ROOT` and `AGENTREC_PARARECOVER_ROOT`, and are skipped when those are unset.

## Validation status

These checks validate the pipeline. They are not results.

- The mechanism, boundary, extended, ablation, and ParaRecover suites at 10 seeds on `dev` run with no infrastructure errors or stalls. That is 2,100, 2,640, 1,800, 4,800, and 1,680 cells, including clean twins.
- The coding pilot's reference agent reaches a safe completion in all 180 cells: 4 tasks × 3 conditions × 3 seeds × 5 policies. The WorkBench reference agent does the same in all 540 cells.
- An ungated `posthoc_repair` sends the wrong irreversible email in the WorkBench commission rows, and the gated policies do not.
- No model-backed pilot has been run. Freezing the real pilots needs the two models and their endpoints. As the plan says, pilot one clean/fault task per model first, then lock the total budget.

## Claims and limitations

- **Mechanism suite safety.** On the dev templates every core policy reaches CRSR_safe 1.0, because they all share the same gate and checker. Differences appear in latency, preserved independent work, and wasted model time. Safety separation comes from the gate ablations, the boundary suite, and the extended baselines.
- **Advisory repair on ParaRecover.** It is incomplete on one nested motif. When a corrected claim backs an effect that was already rejected, a correction publishes a value but does not re-run the step's effect, so the effect goes missing. This is a property of correcting without re-executing, not of the published CoAgent system.
- **WorkBench fault types.** Some instances have commission faults, where the corrupted lookup causes a forbidden write, and others have omission faults, where it suppresses a needed write. Omission faults are recoverable even by post-hoc repair. This mix follows from the frozen selection rule and is reported, not tuned.
- **ParaRecover execution.** The recorded rollouts contain results only for the calls actually executed. Other nodes return deterministic stand-ins, so these motifs test recovery structure and timing, not tool semantics.
- **Trust model and side effects.** Verified evidence is irrevocable under the default `local_commit` trust model. The G and revocation families report what changes under other trust models. A monitor lock protects monitor state, not external databases: H and the CAS controls show that target-side conditional writes are needed for external serializability.
