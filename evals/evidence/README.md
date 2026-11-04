# Evaluation evidence for review

These are compact copies of completed experiment reports and structured summaries.
They are snapshots, not independently regenerated results. Raw per-condition rows,
model exchanges, and traces remain in ignored `results/<run>/` on the experiment hosts
and the author's local worktree. A fresh clone therefore cannot run the retrospective
analysis without those raw artifacts; `scripts/analysis/propagation.py` documents the
expected run paths. No API keys or credentials are part of this evidence bundle.

| Run | Scope | Key result |
|---|---|---|
| [Initial clean pilot](mbpp-clean-pilot-001/REPORT.md) | 5 coding tasks | 5/5 |
| [Clean expansion](mbpp-clean-50-w10-001/REPORT.md) | 50 coding tasks | 48/50 |
| [Paired pilot](mbpp-paired-pilot-w5-001/REPORT.md) | 5 tasks, early/late bad patch | All recovery conditions passed |
| [Paired expansion](mbpp-paired-50-w16-001/REPORT.md) | 450 conditions, 50 tasks | Late bad patch 0/50; recovery 47–48/50 |
| [AWS clean](aws-nrp-gpt-oss-clean-pilot-w5-001/REPORT.md) | 5 coding tasks | 5/5 |
| [Five-family pilot](aws-nrp-gpt-oss-five-fault-pilot-w5-001/REPORT.md) | 116 conditions | Natural false/stale report faults never fired |
| [Extended pilot](aws-nrp-gpt-oss-phase1-extended-w5-002/REPORT.md) | 113 conditions, 5 coding + original travel | Controlled reports caused 5/5 false approvals each |
| [Retrospective propagation](propagation-analysis/REPORT.md) | 175 no-recovery conditions from three batches | Exposure distinguished from observed downstream violations |

All completed model batches above used NRP gpt-oss. Repeated pilot examples overlap
with the 50-task subset; these are not independent new datasets. All are single-trial
custom MBPP partitions, not official benchmark scores. Reports describe denominator,
fixture, scoring-correction, and timing limitations. Interrupted attempts are excluded.

## Current two-provider follow-up (not completed evidence)

The full protocol uses the pinned 10-task manifest and three travel authorization
variants, five workers, identical CPU host types and source snapshots. GPT-OSS is
running on NRP. Triton api-glm-5.3-flash stopped with HTTPStatusError after seven
completed conditions; all five recorded clean tasks passed. A subsequent small API
probe succeeded, so the original HTTP failure's cause remains unknown. It must not be
reported as a model task failure or silently replaced with a successful retry. The
original runner stored only the exception class; this branch now records HTTP status
codes for future failures without saving provider error bodies or credentials.

Those follow-up runs are not included in the completed-results tables. No result from
the new travel authorization cases is yet claimed as live-model evidence. Their
scripted unit tests validate instrumentation, not attack success or model robustness.
