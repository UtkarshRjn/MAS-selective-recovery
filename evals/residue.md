# Side-effect residue after recovery

The travel recovery methods run after the workflow finishes. Each one starts from the same faulted world, so an email that was already sent or a booking that was already made is the same for every method. `final_correct` can be true for every method while that harm stays in place. `src/agentrec/residue.py` measures this harm and compares it with stopping the effect before it happens.

## Tasks

- `booking`: the existing travel task and `DEFAULT_FAULTS`. The email is the last action, so every fault fires before it.
- `email_first`: the same task, but the request and the planner prompt put the email before the itinerary file. It has two faults:
  - `incorrect_result@research_flights` fires before the email.
  - `wrong_id_in_file@write_file` fires after the email.

  This is the timing control. The second fault is real, but the irreversible effect came first.

## Gates

`run_workflow(..., gate=...)` checks each `book_flight`, `book_hotel`, and `send_email` call before it executes.

- `G1` holds a call whose step depends on a flagged fault (`coarse_taint`). It reads the injector's fault flags, so it is an oracle upper bound for detection.
- `G2` reads no fault flags.
  - A booking must name an item from a raw search observation that meets the task constraints.
  - An email must go to the user and name exactly one flight and one hotel. Both must be active bookings.
  - An email may not repeat the IDs of an email already sent.

A held call is recorded as a faulty `.../held` artifact of its step. That makes recovery rerun the step instead of skipping it. Gated rows are recovered with `coarse` only.

## Measures

`side_effect_residue(world, truth, before)` counts effects by item ID:

- wrong emails to the user
- duplicate correct emails
- false confirmations (an email naming the right IDs that were never booked)
- unauthorized emails
- wrong bookings, including bookings cancelled later

`residue` is true if any of these counts is nonzero. With `before`, the `recovery_*` fields count only what recovery itself sent. `summary` reports each task × gate × method cell over runs where the fault fired, with Wilson 95% intervals.

## Commands

```bash
uv run agentrec --llm mock --out runs/ residue run --task booking --seeds 0-49
uv run agentrec --llm mock --out runs/ residue run --task email_first --seeds 0-49 --gates ""
uv run agentrec residue summary runs/residue-booking-mock/rows.jsonl
# OpenAI-compatible endpoint; the key is read from the named environment variable and never written
uv run agentrec --llm server --base-url "$BASE_URL" --out runs/ residue run --task booking --seeds 0-49 \
  --model MODEL --api-key-env API_KEY --price-per-m 0.25 0.75 --max-usd 2
```

Mock rows check the pipeline. They are not model results.
