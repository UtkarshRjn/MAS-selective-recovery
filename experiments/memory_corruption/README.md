# Memory-corruption experiments

AI agents often pass work to each other: one agent plans, another acts. These experiments ask what happens when the handoff between them contains a wrong value, such as a flight number that is off by one digit.

## Folder map

| Folder | What is in it |
|---|---|
| `core/` | Shared code: the mock worlds, how wrong values are made, grading, the model client with its spend limits, the job runner (`harness.py`), handoff texts and the 4-state model. `core/paths.py` holds every file location. |
| `inputs/` | What the code reads: prompts (`inputs/prompts/`), filler text (`inputs/filler/`), and fixed data. |
| `runtime/` | Local run state: the spend ledger, approval files and the key file. Only `run_config.json` is in git. |
| `studies/<name>/` | One folder per study, each with its own README, runner, tests, `docs/` and `results/`. |

## Studies

| Study (older name) | Question | Result |
|---|---|---|
| [`studies/conflict_asking`](studies/conflict_asking/README.md) (v3) | When a handoff shows two different values, does the executor ask or act? | It asks, in more than 90% of runs. |
| [`studies/summary_chain_screen`](studies/summary_chain_screen/README.md) (S0) | Does a wrong value survive repeated summaries? | Yes. The first summary decides whether the true value also survives. |
| [`studies/handoff_chains`](studies/handoff_chains/README.md) (L1) | Can single-handoff measurements predict where a wrong value ends up in a chain or tree? | Partly. Short handoffs copy exactly; all change happens at the first summary. |
| [`studies/compression`](studies/compression/README.md) (L2) | Does shortening a note more make it lose the true value more often? | Yes, but input length alone does not predict pipelines. |
| [`studies/role_vs_truth`](studies/role_vs_truth/README.md) (L3) | When two values conflict, does a summary keep the true one or the plan's one? | The plan's one: it was never dropped, whichever value was true. |

## Run the offline checks

These commands call no model and need no key.

```bash
cd core && python3 -m unittest test_core test_harness && python3 check_gates_c0.py
```

```bash
cd studies/conflict_asking && python3 -m unittest test_conflict_asking test_stats_exact
```

```bash
cd studies/summary_chain_screen && python3 -m unittest test_summary_chain
```

```bash
cd studies/handoff_chains && python3 -m unittest test_handoff_chains
```

```bash
cd studies/compression && python3 -m unittest test_compression
```

```bash
cd studies/role_vs_truth && python3 -m unittest test_role_vs_truth
```

The tests need `httpx` (`pip install httpx`), and `test_stats_exact` also uses `numpy` and `scipy` when they are installed.

## Paid runs

A paid run calls a model API and costs money. It needs three things: the API settings in the environment (or in `runtime/.env`), a spend cap in `PILOT_STOP_AT`, and a non-empty approval file in `runtime/approvals/`. Every study writes a plan and freezes its code before its first paid call. `HANDOVER.md` explains the full protocol.
