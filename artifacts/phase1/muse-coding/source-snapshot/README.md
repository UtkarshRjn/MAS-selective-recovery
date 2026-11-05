# Multi-agent failure propagation and recovery

Phase 1 evaluates a fixed coding workflow on 50 pinned MBPP examples, with seven
fault families and three simulated travel authorization cases. The current pilot
selects 10 coding examples using `--limit 10`.

See [experiment setup, commands, and metrics](evals/README.md).

## Code map

- `mbpp.py`: dataset loading, Docker scoring, parallel task execution, and CLI.
- `phase1.py`: coding fault schedule, comparisons, and stage metrics.
- `coding.py`, `benchmark_faults.py`: coding stages and injected artifact changes.
- `authorization.py`: three travel policy cases and authorization measurements.
- `workflow.py`, `agents.py`, `tasks.py`, `world.py`, `checker.py`, `faults.py`: travel
  stages, prompts, simulated tools, task generation, scoring, and faults.
- `provenance.py`, `recovery.py`: dependencies and recovery.
- `conditions.py`, `resume.py`: condition-level error isolation and explicit resume.
- `llm.py`, `runner.py`, `programs.py`: model client, shared recording, and code task helpers.

Run `uv run pytest -q` for offline tests. Mock-model results only validate the harness;
they are not model accuracy or attack-success measurements. Real model runs require
`--execute-model`. Generated code is evaluated in constrained Docker containers;
travel tools never contact real booking or email services.

Historical reports, deployment helpers, downloaded data, and raw run artifacts are
ignored. Removed TAMAS, A2A, Who&When, topology, and monitoring utilities remain
available in Git history. Existing EC2 jobs use their own saved source snapshots.
