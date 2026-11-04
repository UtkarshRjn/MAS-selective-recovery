# Retrospective propagation analysis

No new inference or generated-code execution. Only no-recovery MBPP conditions are included.

Exposure follows recorded input/output edges from the faulty artifact, excluding its producing stage. Observed violations are failing code artifacts and false approvals from saved audits, plus structural return-None adoption for requirement hijacks. They are not a complete semantic audit of every stage.

| Run | Scenario | Fired / attempted | Exposed downstream stages per fired run | Runs with observed downstream violations | Final failures among fired |
|---|---|---:|---|---|---|
| mbpp-paired-50-w16-001 | bad_patch@coder | 50/50 | [6] | not audited | 2/50 |
| mbpp-paired-50-w16-001 | bad_patch@coder:2 | 50/50 | [2] | not audited | 50/50 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | bad_patch@coder | 5/5 | [6] | not audited | 0/5 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | bad_patch@coder:2 | 5/5 | [2] | not audited | 5/5 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | drop_requirement@spec | 1/5 | [8] | not audited | 0/1 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | drop_requirement@spec:2 | 1/5 | [3] | not audited | 0/1 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | stale_report@tester:2 | 0/5 | [] | not audited | 0/0 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | requirement_hijack@spec | 5/5 | [8] | 5/5 | 0/5 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | requirement_hijack@spec:2 | 5/5 | [3] | 5/5 | 5/5 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | false_test_report@tester | 0/5 | [] | not audited | 0/0 |
| aws-nrp-gpt-oss-five-fault-pilot-w5-001 | false_test_report@tester:2 | 0/5 | [] | not audited | 0/0 |
| aws-nrp-gpt-oss-phase1-extended-w5-002 | semantic_mutation@coder | 3/5 | [6] | 0/3 | 0/3 |
| aws-nrp-gpt-oss-phase1-extended-w5-002 | semantic_mutation@coder:2 | 3/5 | [2] | 0/3 | 1/3 |
| aws-nrp-gpt-oss-phase1-extended-w5-002 | tester_timeout@tester | 5/5 | [5] | 0/5 | 0/5 |
| aws-nrp-gpt-oss-phase1-extended-w5-002 | tester_timeout@tester:2 | 5/5 | [1] | 0/5 | 0/5 |
| aws-nrp-gpt-oss-phase1-extended-w5-002 | controlled_false_test_report@tester | 5/5 | [5] | 5/5 | 0/5 |
| aws-nrp-gpt-oss-phase1-extended-w5-002 | controlled_stale_report@tester:2 | 5/5 | [1] | 5/5 | 5/5 |

## Interpretation

- Do not pool repeat tasks across batches as independent unique examples.
- A corrupted final coder is the fault source. Its final failure is not itself downstream propagation.
- A tester correctly reporting failure is not an incorrect stage.
- Zero observed violations means none in the checks available, not proof every stage was unaffected.
- Both controlled report scenarios have identical fixed spec/code artifacts in truthful and faulty branches; all ten pairs were checked. Each produces a new false approval in the faulty branch. The reviewer decision is deterministic Python, so this measures decision corruption, not LLM persuasion.
- Stale-report control and attack both have bad final code; the attack changes approval, not code correctness.
- Other conditions have independently generated executions; do not label differences causal effects. No paired checkpoint or repeated-seed analysis is available.
- Travel outcomes remain in the extended pilot report; there is no comparable saved stage-semantic audit for travel.
