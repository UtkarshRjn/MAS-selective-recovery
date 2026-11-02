# Recovery evaluation

## Question

The system knows which fault occurred and where it occurred. Can it recover correctly with less work than a full restart?

Fault detection and fault attribution are out of scope.

## Main comparison

Run each method from the same faulty execution.

- `restart` undoes all possible effects and reruns the full workflow.
- `suffix` reruns the faulty step and every later step.
- `coarse`, `support`, and monitor variants use recorded provenance to select work.
- `none` shows fault impact.
- `verify` is a prevention baseline. It is not a recovery method.

The harness has 10 injected faults. They cover research results, plans, tool selection, tool arguments, and malicious instructions.

## Five metrics

1. **Task recovery success**: The final task output is correct.
2. **Side-effect integrity**: Required effects exist. No active wrong effect, duplicate effect, or unauthorized effect remains.
3. **Recovery token ratio**: Recovery tokens divided by full-restart recovery tokens. Lower values are better.
4. **Recovery latency ratio**: Recovery time divided by full-restart recovery time. Lower values are better.
5. **Valid-work preservation**: Fraction of valid steps from the faulty run that recovery reuses.

The benchmark defines valid steps from fault semantics. Recovery methods do not define this oracle.

`recovery_success` requires task recovery success and side-effect integrity.

## Benchmark set

The external commands run native benchmarks. They do not yet run this project's recovery methods.

### Booking fault-injection harness

Use this as the main benchmark. It supplies known fault locations, deterministic state checks, and paired recovery runs.

```bash
uv run agentrec --llm mock experiment --tasks 30 --faults all --methods restart,suffix,coarse,support,monitor,support_monitor
uv run python analysis/summarize.py results/experiment-mock-*/rows.jsonl
```

### TAMAS

TAMAS supplies adversarial multi-agent tasks. It tests recovery after malicious instructions, compromised agents, and conflicting agents.

```bash
uv run agentrec tamas download
uv run agentrec tamas summary
uv run agentrec tamas trial --attack Byzantine --scenario news --index 0 --model gpt-4o
```

The trial command uses the official TAMAS runner. Follow its setup guide and set the required model API key first.

### UndoBench

UndoBench supplies operational tasks with state and side-effect oracles. It tests recovery for reversible, compensatable, and irreversible actions.

```bash
uv run agentrec undobench download
uv pip install -e data/undobench-upstream
uv run agentrec undobench smoke
uv run agentrec undobench trial --suite smoke --recovery naive --dry-run
```

The trial command uses the official UndoBench runner. Use its agent adapter to evaluate this project on the full benchmark.

### A2ASecBench

A2ASecBench supplies malicious agent-to-agent protocol cases and benign controls. It tests whether recovery handles untrusted peer messages.

```bash
uv run agentrec a2a download
uv pip install -e 'data/a2asec-upstream[dev]'
uv run agentrec a2a summary
uv run agentrec a2a trial --config configs/offline/asrf_eval.yaml --trials 1
```

## Protocol

1. Check clean-run reliability.
2. Use the same task, seed, fault, and faulty trace for each recovery method.
3. Charge each method for cleanup and compensation.
4. Do not give full restart a free world reset.
5. Include failed recovery attempts, timeouts, and their costs.
6. Use several task seeds and repeated paired runs.
7. Report confidence intervals across task seeds.
8. Keep final task seeds and fault payloads held out during development.

The current CLI varies task seeds. Repeat orchestration and confidence-interval analysis are final-report work.

## Claim boundary

The internal harness reports the five shared recovery metrics. External trial commands report native benchmark metrics.

Do not claim an official external recovery score until an adapter runs this recovery system inside that benchmark environment.
