# MBPP 50-task clean baseline

NRP gpt-oss; workers=10, seed=0, temperature=0. Completed 50 tasks.

Code pass rate: 48/50 (96%).
Final assertions: 98/102.
Elapsed batch time: 241.7 seconds.
Model calls: 350; input tokens: 72628; output tokens: 88904.

## Failed tasks

- Task 83: output does not match the expected character mapping; both held-out assertions fail.
- Task 87: implementation gives later dictionaries precedence; held-out tests expect earlier dictionaries to take precedence. The natural-language prompt does not specify conflict precedence.

These are failures against the pinned assertions, not proof of an unambiguous specification violation. No faults were injected and no recovery methods ran. Small test sets limit correctness claims. README task-name checks are separate from code success. Parallel timing includes contention.

| Task | Code success | Final assertions | Workflow seconds |
|---|---|---|---|
| 2 | True | 2/2 | 47.79 |
| 3 | True | 3/3 | 45.43 |
| 4 | True | 2/2 | 47.25 |
| 7 | True | 2/2 | 48.94 |
| 8 | True | 2/2 | 37.96 |
| 9 | True | 2/2 | 72.47 |
| 11 | True | 2/2 | 54.61 |
| 12 | True | 2/2 | 48.72 |
| 14 | True | 2/2 | 51.61 |
| 16 | True | 2/2 | 48.67 |
| 17 | True | 2/2 | 24.08 |
| 19 | True | 2/2 | 31.71 |
| 20 | True | 2/2 | 35.57 |
| 57 | True | 2/2 | 45.40 |
| 58 | True | 3/3 | 34.99 |
| 59 | True | 2/2 | 38.76 |
| 61 | True | 2/2 | 39.04 |
| 62 | True | 2/2 | 36.27 |
| 63 | True | 2/2 | 36.02 |
| 64 | True | 2/2 | 30.89 |
| 65 | True | 2/2 | 44.93 |
| 66 | True | 2/2 | 41.06 |
| 67 | True | 2/2 | 66.60 |
| 68 | True | 2/2 | 57.87 |
| 69 | True | 2/2 | 49.99 |
| 71 | True | 2/2 | 53.43 |
| 72 | True | 2/2 | 66.97 |
| 74 | True | 2/2 | 72.74 |
| 75 | True | 2/2 | 58.37 |
| 77 | True | 2/2 | 42.61 |
| 79 | True | 2/2 | 37.35 |
| 80 | True | 2/2 | 34.03 |
| 82 | True | 2/2 | 42.37 |
| 83 | False | 0/2 | 69.94 |
| 84 | True | 2/2 | 63.01 |
| 85 | True | 2/2 | 48.25 |
| 86 | True | 2/2 | 54.02 |
| 87 | False | 0/2 | 56.15 |
| 88 | True | 2/2 | 40.74 |
| 89 | True | 2/2 | 52.10 |
| 90 | True | 2/2 | 50.51 |
| 91 | True | 2/2 | 55.21 |
| 92 | True | 2/2 | 46.15 |
| 93 | True | 2/2 | 32.71 |
| 94 | True | 2/2 | 37.13 |
| 95 | True | 2/2 | 34.18 |
| 96 | True | 2/2 | 33.93 |
| 97 | True | 2/2 | 29.82 |
| 98 | True | 2/2 | 31.04 |
| 99 | True | 2/2 | 33.14 |
