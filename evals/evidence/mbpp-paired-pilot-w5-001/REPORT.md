# Five-task paired MBPP pilot

NRP gpt-oss; five workers; clean plus transient return-None faults at each coder stage. Known fault labels supplied to recovery.

| Fault | Method | Passed | Mean recomputed stages | Mean reused stages | Mean recovery tokens |
|---|---|---|---|---|---|
| none | clean | 5/5 | — | — | — |
| bad_patch@coder | none | 5/5 | — | — | — |
| bad_patch@coder | restart | 5/5 | 9 | 0 | 3044.4 |
| bad_patch@coder | suffix | 5/5 | 8 | 1 | 2661 |
| bad_patch@coder | coarse | 5/5 | 7 | 2 | 2119.8 |
| bad_patch@coder:2 | none | 0/5 | — | — | — |
| bad_patch@coder:2 | restart | 5/5 | 9 | 0 | 3173 |
| bad_patch@coder:2 | suffix | 5/5 | 3 | 6 | 768 |
| bad_patch@coder:2 | coarse | 5/5 | 3 | 6 | 817 |

Batch wall time: 268.803 seconds. Actual model calls: 250. Input/output tokens: 52022/60841.

First-coder faults did not survive the ordinary revision pass. Recovery in that condition is redundant for final code correctness. Final-coder faults failed every final check without recovery; all tested recovery methods restored test success. This is a small plumbing/behavior pilot, not a general recovery guarantee. Fault identity is supplied; no detection metric is evaluated. Recovery token costs exclude the preceding faulty execution; rows also provide total_usage.
