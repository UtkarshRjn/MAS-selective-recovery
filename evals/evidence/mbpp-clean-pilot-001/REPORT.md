# Five-task MBPP clean pilot

NRP gpt-oss; temperature 0; seed 0. Sequential execution.

| Task ID | Final tests | Code success | Workflow seconds |
|---|---|---|---|
| 2 | 2/2 | True | 33.2 |
| 3 | 3/3 | True | 20.7 |
| 4 | 2/2 | True | 17.0 |
| 7 | 2/2 | True | 14.3 |
| 8 | 2/2 | True | 22.8 |

Model calls: 35. Input tokens: 7209; output tokens: 8891.

All scores refer to the custom held-out assertion partition, not an official MBPP score or exhaustive correctness. No faults/recovery runs performed. README task-name check passed on 3/5; it is not part of code success. Workflow timings exclude final scoring and setup.
