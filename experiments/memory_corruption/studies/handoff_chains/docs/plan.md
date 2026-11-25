# L1 draft plan: a predictive transmission law for errors across agent handoffs

Status: DRAFT. Not frozen, not reviewed, no go file, no spend. Written 21 November 2026 (PT).
Code: `law_model.py`, `law_inputs.py`, `law_run.py`, prompts `law_prompt_*.txt`, tests `test_law.py`.

## Vision

A law that predicts, before a multi-agent system is built, how often a wrong value survives
each handoff and reaches an executor; plus structural fixes that push survival below the point
where errors snowball. A builder could then compute a system's reliability from per-edge
numbers, the way reliability engineers compose component failure rates.

## 1. Shortcomings found (this repo, plus the literature)

From the review of PR #30 and S0:
1. v3's main result is fixed by the design. The "summary" is one templated sentence that
   claims authority, next to a block that says "copy these exactly". Asking is the obvious
   response. Nothing about real compression is measured.
2. Asking is free. No cost, no time pressure, no lookup. A 95% ask rate under free asking is
   not a safety result.
3. Two flash models from one price band. No third family, no frontier model.
4. S0 ran a temperature-0 model on its own output. Hop 2 onward was near 1.0 because the
   chain sat at a fixed point, not because of a property of handoffs.
5. One error type (an edit-distance-1 typo). Typo survival was at ceiling by construction.
6. The S0 "law" was s^k on one scalar. It ignores that a value can be visible, silent, or lost,
   and that a dropped value and a wrong value have opposite safety meanings.
7. No out-of-sample prediction. A law must predict a system it was not fitted on.
8. Fixes were judged only by ask rate, never by what they cost or what they break.
9. Eight designs in one day, with "reviewers" that were simulated personas. Pre-registration
   needs real, independent reviewers to count.

From the literature (verify every claim in the full papers before citing):
- MAST (Cemri et al., NeurIPS 2025) gives a failure taxonomy but no predictive model.
- AgentAsk (arXiv 2510.07593) puts most failures at the handoff edge. That is the unit this
  law uses.
- Handoff Hallucinations (Springer 2026) and Facts Without Rules (arXiv 2608.29028) report
  hedge and boundary-metadata loss under compression. L1 measures that loss as a state
  transition (V to S) rather than as a count.
- Silent Failure in LLM Agent Systems (arXiv 2606.08162) reports more than 30% loss within
  five hops, but from the authors' own setup. L1 tests a per-edge model that should predict
  numbers like that.
- Tool-memory conflict work (arXiv 2601.09760, 2609.05587) shows that how agents resolve
  conflicts depends on the conditions. That supports measuring per-edge matrices by model and
  fix instead of assuming one constant.
- Anthropic's multi-agent research post: briefs need explicit objectives and boundaries, and
  context is lost at each handoff. Its lesson is qualitative. L1 makes it quantitative.
- Databricks / Mosaic Gauntlet calibration: some tasks are noisy and some do not scale with
  model size. L1 reports item-level noise and resamples by item, not by call.
- Context-engineering writing (attributed to Karpathy): keep pointers to files, not copies.
  That is fix F3 (reground by pointer).
- Knight and Leveson 1986, Eckhardt and Lee 1985: N-version channels are rarely independent.
  The diamond topology tests that independence assumption directly.

## 2. Brainstorm (three lenses)

OpenAI-style evaluation lens: the claim must survive more model families, and the metric
must be the deployed harm, not a proxy. So P(harm) is reported next to state probabilities,
and the executor act rates are a parameter, not a constant buried in the code.

Anthropic-style safety lens: a fix that cuts loss can lock in errors. Pinning a value
prevents a drop, but it also makes a wrong value permanent. The plan must measure that trade
for every fix, and say plainly where a fix depends on an authoritative record existing.

Karpathy-style systems lens: treat each agent as a noisy channel and the system as a circuit.
If the per-edge matrices compose, you can compute the reliability of a design on paper. If
they do not compose, the size of the miss is the finding.

Ideas kept: a 4-state edge matrix; edge types (compress, relay, merge); a fan-out
reproduction number R; predictions frozen before held-out topologies are run; three fixes
with an analytic fourth; two error types; temperature above 0; scoring against a 2-state
baseline.
Ideas deferred: an executor with an ask cost, frontier models (they need your key), a
third error type (stale memory), adversarial injection.

## 3. The model

States of the target value in one handoff: V (wrong and true both present, a visible
conflict), S (only the wrong value, silent), T (only the truth), N (dropped).
An edge is a 4x4 matrix P[i][j] = P(out j | in i), fitted per (model, fix, error, edge type).
- Chain: sink = start x P1 x P2 x ... x Pk.
- Merge: branches are independent given their shared parent; a measured merge table maps
  each input-state pair to an output distribution; unmeasured pairs use the union rule, and
  the mass that falls back is reported.
- Fan-out tree with branching b: R = b x rho(Q), Q = P restricted to {V, S}. R > 1 means the
  error population grows with depth; R < 1 means it dies out. The critical fan-out is 1/rho.
- Harm: P(harm) = sum over states of P(state) x a(state), with a(V) = 0.05, a(S) = 0.95 from
  this repo's earlier grids. R and harm can move in opposite directions: reground (F3) turns
  S into V, which raises R but cuts harm.

## 4. Design

Worlds: the S0 travel worlds, templates trip and trip_long, fresh seeds 100 to 111.
Errors: E1 typo (frozen neighbor, made bookable); E4 plausible wrong (a different real flight).
Fixes: F0 plain summary; F1 pinned identifier block re-attached by the harness; F2 provenance
prompt with CONFLICT lines; F3 reground by pointer (analytic, no calls).
Temperature 0.7, API seed from a hash of the job key, plain text only.

Stage C, calibration (single edges, synthetic inputs in a known state):
compress from V and T sources (E4 from V only, because its truth source lists the wrong flight
too); relay from V, S, T, N; merge for 6 input pairs. 2 samples per item (merge 1).

Predictions: `law_run.py --predict` fits stage C only and writes `law_predictions.json`. Its
sha256 is recorded before stage P starts. Stage P refuses to run without that file.

Stage P, held-out topologies (real LLM outputs feed the next agent, unlike stage C):
chain4 per model; chain_mixed (models alternate); diamond (compress, two relays, merge);
binary tree to depth 3 (15 calls). Fixes F0 and F1.

## 5. Pre-registered tests (to be frozen)

1. Composition: on each chain and diamond, mean absolute error between predicted and
   observed sink distributions, and the log loss, against the 2-state s^k baseline. The law
   passes if it beats the baseline on log loss in at least 75% of topology cells, on every
   model.
2. Snowball threshold: for each tree cell, the sign of R - 1 predicts whether the observed
   count of error nodes grows from depth 1 to depth 3.
3. Fix trade-off: report R, P(harm) and tree harm for F0, F1, F2 and F3 side by side.
   Expected by construction: F1 cuts N and locks S.
4. Independence: the diamond's observed sink against the prediction that assumes branch
   independence given the root. A miss larger than the bootstrap interval measures coupling.
Kill rule: if the law does not beat the baseline on any model, report a null and stop.

## 6. Cost

`law_run.py --jobs-only` at the full grid: about 9,300 calls. The token estimate is about $2.04,
but the observed S0 mean ($0.001 per call) gives about $9.30. Plan with the higher number.
Half grid (`--seeds 6`): about 4,650 calls, about $4.64 at the S0 mean.
Recorded remaining under the $19.75 stop on the UCSD key: $13.01.

## 7. What is honest to claim

On two flash models this is still a workshop-level signal. A main-track claim needs at least
three model families, one frontier model, and a held-out prediction that works. A 9/10 is not
something a design can promise; it depends on what the data show. What the design does
provide is a result that is informative either way: a law that predicts, or a measured gap
between per-edge numbers and system behaviour.

## 8a. Decisions recorded (Bijin, 21 November 2026, answers to the decision card)

- Grid: "Full grid, about $9" (12 seeds per template, both stages).
- Cap: "$10.00 (Recommended)" new spend for stage C plus stage P together. With a fresh
  ledger on this machine, PILOT_STOP_AT=10 enforces it.
- Key: "UCSD key, lab permits". Cumulative on the key after this run is at most
  $6.73675 + $10.00 = $16.74, under the $19.75 standing stop.
- Models: api-deepseek-4.1-flash and api-glm-5.3-flash only (the only models with a price).
- Review: "Freeze, I approve (Recommended)". No outside reviewers. The go file records that.
- Prior: "Add-0.1 (Recommended)". Set in law_run.fit_all.
- Execution: 12 workers, 80 requests per minute, server spend baseline recorded at start,
  ledger saved every 50 jobs, a budget stop keeps finished traces and reports the rest.

## 8. Decisions needed from Bijin before any spend (answered in 8a)

1. Key: the UCSD TritonAI key (if the lab allows), or your own key. Base URL.
2. Cap in your own words for stage C and stage P.
3. Models: the two flash models only, or a third family and a frontier model (your key).
4. Independent reviewers for the freeze (real people, not simulated personas).
5. Full grid or half grid.
6. Smoothing prior for the edge matrices. The default add-0.5 prior is strong at 24 to 48
   rows per state. In the mock dry run it made the law lose to the 2-state baseline on the
   pinned fix (F1), where the true transition is nearly deterministic. Freeze a smaller prior
   (for example 0.1), or fit the prior on stage C by item-level cross-validation, before
   stage P.
