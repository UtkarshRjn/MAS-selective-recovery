# Study: handoff chains

Called **L1** in the plan and in older notes.

**Question.** One agent passes a note to the next, and that agent passes it on. If the note holds a wrong value, can we predict where it ends up after several handoffs, from measurements of single handoffs?

**Short answer.** Partly. Short handoffs copied the note exactly: nothing was lost and nothing was fixed. All change happened when a long note was first summarized. The pre-registered prediction test failed, because the wrong value reached the end in every run, so the test had nothing to tell apart.

## The model

The target value in a note is in one of four states:

| State | Meaning |
|---|---|
| V | wrong value and true value both present (a visible conflict) |
| S | only the wrong value (a silent error) |
| T | only the true value |
| N | the value was dropped |

One handoff is a 4 × 4 table of probabilities, from the state it reads to the state it writes (`core/state_model.py`). A chain is a product of these tables. A tree, where each agent passes to b others, grows the number of wrong notes by R = b × (largest eigenvalue of the V/S block).

## Design

- Stage C measures single handoffs: compress (long note to short), relay (short to short), merge (two notes to one).
- Predictions for unseen systems are written from stage C only, and hashed before stage P.
- Stage P runs those systems: 4-step chains, mixed-model chains, a diamond (split then merge), and binary trees.
- Fixes compared: plain summary (F0), pinned values (F1), source tags (F2).
- 4,896 + 4,416 calls, two models, $4.22.

## Result

| Test | Result |
|---|---|
| Law beats a simple survival baseline | Failed (12 of 12 cells): no variance in the outcome |
| Snowball rule (R > 1 means errors spread) | Passed, trivially: relays copied exactly, so R ≈ 2 |
| Fix ranking | Source tags best; pinned values worst on one model |

Full write-up: `results/results.md`.

## Files

| File | Role |
|---|---|
| `run_handoff_chains.py` | Grid, single-edge runs, predictions, system runs, scoring |
| `test_handoff_chains.py` | Unit tests for the model, the texts and the grid |
| `docs/plan.md`, `docs/freeze_hashes_law_l1.json` | The frozen plan and its hashes |
| `results/` | Predictions (and their hash), scores, write-up |

## Run it (offline)

```bash
python3 -m unittest test_handoff_chains
```

```bash
python3 run_handoff_chains.py --dry-run
```
