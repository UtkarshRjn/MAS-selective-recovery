# MBPP paired evaluation: 50 tasks, 16 workers

NRP gpt-oss; seed 0, temperature 0. Single trial per task/condition. Both injected faults are transient return-None replacement at the named coder stage. Every recovery method receives the faulty-node label and runs with injection disabled.

## Correctness

| Fault location | Method | Passed / all 50 | Passed / clean-successful 47 | Failed task IDs |
|---|---|---|---|---|
| none | clean | 47/50 | — | [63, 83, 87] |
| bad_patch@coder | none | 48/50 | 47/47 | [83, 87] |
| bad_patch@coder | restart | 44/50 | 44/47 | [4, 7, 63, 83, 84, 87] |
| bad_patch@coder | suffix | 48/50 | 47/47 | [83, 87] |
| bad_patch@coder | coarse | 48/50 | 47/47 | [83, 87] |
| bad_patch@coder:2 | none | 0/50 | 0/47 | [2, 3, 4, 7, 8, 9, 11, 12, 14, 16, 17, 19, 20, 57, 58, 59, 61, 62, 63, 64, 65, 66, 67, 68, 69, 71, 72, 74, 75, 77, 79, 80, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99] |
| bad_patch@coder:2 | restart | 47/50 | 46/47 | [83, 84, 87] |
| bad_patch@coder:2 | suffix | 48/50 | 47/47 | [83, 87] |
| bad_patch@coder:2 | coarse | 47/50 | 47/47 | [63, 83, 87] |

## Recovery costs (means over all 50 tasks)

| Fault | Method | Rerun / reused stages | Model calls | Recovery tokens | Faulted + recovery tokens | Recovery seconds |
|---|---|---|---|---|---|---|
| bad_patch@coder | restart | 9 / 0 | 7 | 3256.9 | 6771.6 | 59.0 |
| bad_patch@coder | suffix | 8 / 1 | 6 | 2807.4 | 6322.1 | 51.96 |
| bad_patch@coder | coarse | 7 / 2 | 5 | 2252.2 | 5766.9 | 39.71 |
| bad_patch@coder:2 | restart | 9 / 0 | 7 | 3240.2 | 6524.1 | 57.56 |
| bad_patch@coder:2 | suffix | 3 / 6 | 2 | 846.0 | 4130.0 | 13.56 |
| bad_patch@coder:2 | coarse | 3 / 6 | 2 | 843.8 | 4127.7 | 14.06 |

## Run accounting

All 450 rows saved; all 50 task workers completed. Batch elapsed: 1463.2 s. Actual model calls: 2500; input tokens: 522872; output tokens: 640300.

The first launch failed during Docker image preflight, before any inference. The pinned image was pulled and the run started successfully. No condition was retried to replace a failed score. Containers were checked after completion.

## Interpretation and limits

- Clean success in this paired batch is 47/50, versus 48/50 in the prior clean-only batch. These are separate model executions; seed/temperature do not guarantee identical outputs on the remote service.
- First-coder corruption can be repaired in the ordinary revision pass. Explicit recovery may add cost or introduce a new error even when final code was already correct.
- Final-coder corruption leaves no further coding pass in the base workflow, so all no-recovery final tests fail. This makes the fault easy and does not establish robustness to subtle faults or malicious agents.
- Conditional columns use clean success in THIS batch. They do not exclude failed cases from the overall score.
- All injected fault/no-recovery rows report fault_fired=true.
- Final test assertions are a small custom MBPP partition, not exhaustive correctness or official MBPP scores. Task 87 has underspecified dictionary conflict precedence; task 83 has a mismatch between wording and expected mapping.
- Recovery time includes concurrent resource/server contention and excludes final held-out scoring; tracking overhead is not separately measured.
- Differences from a single paired run do not isolate causal fault effects from model variability.
- No verification, support, or monitor method was included in this batch.
