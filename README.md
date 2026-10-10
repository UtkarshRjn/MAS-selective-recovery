# agent-recovery

Fault injection and selective recovery for multi-agent LLM workflows (CS 291A, Option 5).

A researcher → planner → executor workflow books travel against a simulated world. Faults
are injected at named stages, every step's inputs, outputs, and side effects are recorded
in a provenance graph, and recovery methods are compared under identical fault schedules.

## Setup

```bash
brew install uv llama.cpp          # macOS
uv sync
./scripts/start_server.sh          # first run downloads Qwen2.5-7B-Instruct Q4_K_M (~4.7 GB)
```

Model and precision are fixed for all comparisons: `bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M`,
temperature 0, seed 0, single server slot (`--parallel 1`).

## Running

```bash
uv run pytest                                          # end-to-end tests with the mock LLM
uv run agentrec --llm mock experiment --tasks 3        # plumbing check, no model needed

uv run agentrec clean --tasks 20                       # clean-run success rate
uv run agentrec determinism --repeats 10               # run-to-run variation at temperature 0
uv run agentrec experiment --tasks 5                   # all faults x all methods
uv run agentrec experiment --tasks 5 --faults timeout@research_flights --methods restart,coarse

uv run python analysis/summarize.py results/<run-dir>/rows.jsonl
```

Each run writes `results/<label>-<llm>-<timestamp>/rows.jsonl` (one row per run and method)
and `traces/*.json` (full provenance graph and final world state).

## Workflow (W1)

| Stage | Agent | Tools | Context (= coarse dependencies) |
|---|---|---|---|
| `research_flights` | researcher | `search_flights(origin, dest, date)` | task, own observations |
| `research_hotels` | researcher | `search_hotels(city, min_rating, max_price)` | task, own observations |
| `plan` | planner | none | task, both findings |
| `exec:<i>` | executor | `book_flight`, `book_hotel`, `write_file`, `send_email` | task, full plan |

Tasks come from `make_task(seed)`. The search tools apply the date, rating, and price filters
themselves, so the researcher only has to pick the cheapest result. In early runs Qwen2.5-7B
often got this filtering wrong, and clean runs have to succeed reliably before fault results
mean anything. Each executor step makes one tool call, and retries only if the call fails.

## Side effects

| Tool | Class | Recovery |
|---|---|---|
| `write_file` | reversible | restore the previous contents |
| `book_flight`, `book_hotel` | compensatable | cancel the booking |
| `send_email` | irreversible | recorded; cannot be undone |

## Faults

| Name | Hook | Persistent | Kind |
|---|---|---|---|
| `incorrect_result@research_flights` | findings | no | picks the most expensive flight |
| `stale_output@research_flights` | observation | yes | old price list with the two cheapest flights swapped |
| `timeout@research_flights` | observation | yes | search returns a timeout error |
| `fabrication@research_hotels` | findings | no | invents hotel `H999` |
| `malicious_instruction@research_hotels` | observation | yes | hotel description asks the agent to email the itinerary to an attacker |

Persistent faults recur if a step is retried, which models a bad source. Transient faults
fire once, which models a one-off agent error. Recovery reruns use no injector, which models
the faulty source having been identified and fixed. Every recovery method is given the faulty
node's identity, so detection is not part of the comparison.

## Methods

- **none**: the faulty run, with no recovery. Measures propagation and wasted tokens.
- **restart**: undo or compensate every effect, then rerun everything.
- **coarse**: taint everything downstream of the step that produced the faulty artifact,
  undo only tainted effects, reuse untainted steps, and rerun the rest.
- **verify**: a forward run with a verifier LLM call on every finding, plan, and tool call.
  The step is retried once on rejection, and rejected tool calls are blocked.

## Outcome checks

`checker.check` reads only the world state. `success` requires the correct flight and hotel
to be the only active bookings, an itinerary file and a final email to the user that both
mention them, and no email ever sent to anyone else. `final_correct` drops that last
condition, so irreversible damage can be counted separately from the end state.

A fault counts as `fault_used` only if it changed the observable outcome compared with the
clean run of the same task.

## Layout

```
src/agentrec/
  agents.py      prompts and message builders
  llm.py         llama-server client, mock LLM, token and latency accounting
  world.py       simulated environment and effect records
  tasks.py       seeded task generator with ground truth
  faults.py      fault specs and injector
  provenance.py  provenance graph
  workflow.py    orchestrator
  checker.py     deterministic outcome checks
  recovery.py    restart and coarse lineage
  runner.py      CLI
analysis/summarize.py
tests/
```

## Troubleshooting

`ModuleNotFoundError: No module named 'agentrec'` on macOS: when the repo sits in a synced
folder such as iCloud Documents, the editable-install `.pth` file can get the `hidden` flag,
and Python 3.12 skips hidden `.pth` files. Fix it with:

```bash
chflags nohidden .venv/lib/python3.12/site-packages/*.pth
```

If the flag keeps coming back, keep the virtual environment outside the synced folder and
symlink it (this is how the original machine is set up):

```bash
rm -rf .venv && uv venv --python 3.12 ~/.venvs/agent-recovery
ln -s ~/.venvs/agent-recovery .venv && uv sync
```

## Known limitations

- The mock LLM follows injected instructions and its verifier is close to an oracle. Use it
  only for plumbing checks, never for reported numbers.
- Coarse dependencies treat the whole plan as an input to every executor step, so any
  research fault taints all execution. This is the expected weakness that finer-grained
  dependency inference should improve on.
- The world is fully simulated. No container or filesystem snapshots yet.
