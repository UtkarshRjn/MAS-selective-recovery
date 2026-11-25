# L2 draft: from "errors survive handoffs" to "compression edges decide reliability"

Status: DRAFT for discussion. Not frozen, no spend. Written 22 November 2026 (PT), while L1
stage P was still running. L1 frozen files are not changed by this note.

## 1. What the new literature changes for us

Verify every number in the full papers before citing.

| Work | What it shows | What it means for us |
|---|---|---|
| The Hallucination Snowball (arXiv 2608.14588, ICML 2026 FAGEN workshop) | A four-state Markov model of error propagation in a 4-agent linear pipeline (Researcher, Analyst, Writer, Reviewer), with a product formula for survival. Detectability falls from 72.0% to 50.9% over three transformations. | Our L1 four-state Markov chain is **not novel by itself**. The paper must cite this and claim something it does not do. |
| Hallucination Cascade (arXiv 2606.07937) | In 2- and 3-agent chains, the hallucination score **falls** with depth (amplification factor 0.64 to 0.84). | Chains can attenuate, amplify, or (in our L1 data) stay flat. A law must say **which edge property** decides that. |
| From Spark to Fire (arXiv 2603.04474) | One injected error seed can infect most runs; a genealogy-graph governance layer at the message level blocks most cascades. | A strong fix baseline. Provenance tracking at the message layer is close to our F2. |
| Before Agents Speak / HalluProp (arXiv 2607.26836) and Estimation of Propagated Risk (arXiv 2610.33282) | Pre-hoc risk inference on the agent graph (Noisy-OR propagation), AUROC about 0.85 for locating faulty agents. | "Predict reliability before you build" is already a stated goal. Our edge must be **causal, measured edge parameters**, not a learned risk score. |
| GUARDIAN (NeurIPS 2025) | Temporal graph model to detect and block hallucination spread. | A detection baseline, not a predictive law. |
| MAST (NeurIPS 2025 D&B) | Failure taxonomy. | Context only. |
| Anthropic, multi-agent research system | Subagents return condensed results; writing outputs to storage and passing **lightweight references** avoids loss through the lead agent. | Our F3 (reground by pointer) is Anthropic's recommended pattern. Test it as a real tool, not analytically. |
| Anthropic, context engineering; "when to fold subagents back"; "when to use multi-agent systems" | Context is finite; compaction is a lossy summary; newer guidance prefers fewer subagents; multi-agent costs 3 to 10 times more tokens. | A quantitative rule for **when splitting into subagents is safe** is a direct, practical contribution. |
| Thinking Machines, Defeating Nondeterminism | Endpoint outputs vary with server batch size even at temperature 0. | Temperature 0 does not give one fixed output. Report sample variance per item and treat items as random effects. S0's "fixed point" explanation must be stated with care. |

## 2. What L1 stage C already shows (data, 4,896 jobs)

1. **Relay edges are identity.** Short (about 80 word) handoffs kept their state in 48 of 48
   runs for every model, fix and error. No loss, no recovery.
2. **All change is at the compression edge** (about 1,000 to 1,500 words in, 40 to 120 words out).
   V to S (true value lost, wrong value kept) was 23% for typos with the plain prompt.
3. **Provenance (F2) cuts V to S** to about 6%.
4. **Pinning the plan's value (F1) can make it worse**: 48% on DeepSeek typos.
5. **S is absorbing.** No relay recovered a lost true value (0 of 192 S-input relays).

Point 1 is a design weakness of L1: a law tested only on identity edges is trivial. Points 2
to 5 point to a sharper and more novel claim.

## 3. Improved hypotheses

H1. Compression-ratio law (primary). The rate at which an edge loses the true value of a
conflicting pair, P(V to S), is a monotone function of that edge's compression ratio
c = input tokens / output budget, and does not depend on position in the chain. A relay with
c near 1 is identity; a relay with the same c as the first compression behaves like it.
Prediction: P(V to S) = 1 - exp(-k (c - c0)) for c > c0, with k set by model and prompt.
Why it matters: c is known at design time (input sizes and summary budgets), so the
reliability of a pipeline can be computed before it is built. This explains L1 (flat relays),
the Snowball result (rewriting stages that compress), and the Cascade result (stages that
expand or re-derive) with one variable.

H2. Absorbing silence. Without an external source, S never returns to V or T. Recovery
requires an edge that reads the source by pointer. Prediction: P(S to V or T) = 0 for every
pure-text edge; it rises only with a lookup tool.

H3. Authority shift (mechanism for F1). A pinned, harness-owned value makes the summarizer
drop competing values. Test: pin the wrong value versus pin the true value, counterbalanced.
If pinning the truth lowers loss as much as pinning the wrong value raises it, pinning acts as
an authority signal, not as a memory aid. This also links to v3 (the "copy these exactly"
label that GLM followed).

H4. Reliability of a split. Splitting one agent into k subagents that each compress their
context adds k compression edges. Under H1, total P(silent error) is predictable from the
edges' c values. This gives a quantitative version of Anthropic's "when to use multi-agent"
guidance.

H5. Fix ordering. Provenance at the compression edge (F2) and reground by pointer (real F3)
reduce harm; pinning (F1) can increase it. Prediction ordering of harm: F3 < F2 < F0 < F1 for
typos.

## 4. Design changes (L2)

1. Manipulate c directly. Relay inputs at about 80, 300, 800 and 1,500 words, made by adding
   neutral task detail (constraints, listings) around the same V, S, T or N core; output budget
   fixed at 80 words; plus one budget arm (40, 80, 250 words) at fixed input. Fit H1 on a
   calibration set, then predict held-out c values and held-out pipelines.
2. Role edges. Add rewrite edges (Analyst, Writer roles from the Snowball setup) at matched c,
   to separate "compression" from "rewriting".
3. Real reground (F3). The receiver gets a get_trip_selection tool and a pointer line. Measure
   P(S to V or T), the call rate, and the token cost.
4. Counterbalanced pinning (H3): pin wrong, pin truth, no pin.
5. Models: at least three families, plus one frontier model on Bijin's own key. The UCSD
   gateway lists only two chat models (api-deepseek-4.1-flash, api-glm-5.3-flash), so a third
   family needs another provider.
6. Repeated samples (3 per item per edge) and item random effects, after the Thinking Machines
   nondeterminism result.
7. Baselines: the Snowball product formula (constant per-hop survival), a Noisy-OR graph
   risk score (HalluProp style), and our c-dependent law. The claim is that the c-dependent
   law predicts held-out pipelines better than both.
8. Executor at the sink with a real cost of asking (a time penalty or a limit of one question),
   so P(act | V) is measured, not assumed.

## 5. Why this is closer to a main-track paper

- One measurable variable (c) predicts behaviour that earlier papers report as conflicting
  (attenuation, amplification, flat chains).
- The prediction uses only design-time quantities, which neither MAST, GUARDIAN, Snowball nor
  HalluProp provides.
- A fix that looks safe (pinning) is shown to cause harm, with a mechanism test.
- It answers a question practitioners ask, from Anthropic's own guidance: when is it safe to
  split work across agents?

Still needed for main track: more model families, a frontier model, a second domain (for
example code or finance, where the Snowball repo provides FinanceBench cases), and real
outside review of the freeze.

## 5a. Decisions recorded (Bijin, 22 November 2026)

- Size and cap: "Full grid, cap $11" (answer to "Which L2 size and cap do you approve?").
  Full grid = 12 seeds (200 to 211), 16,704 calls, estimated $9.87.
- This raises the project's earlier $19.75 standing stop on this key. The key's own limit is
  $50; key spend before L2 was $13.42, so the most it can reach is about $24.42.
- Enforcement: the ledger holds L1's $4.2249, so PILOT_STOP_AT = 15.22 allows $11.00 new.
- Review: as for L1, Bijin approves the freeze himself; no outside reviewers.
- Models: api-deepseek-4.1-flash and api-glm-5.3-flash (the only chat models on the gateway).
- Implemented scope: H1 (levels 80/300/800/1500, adjacent and spread, budgets 40/80/250),
  H2 (recovery from S on pure-text edges), H3 (pin wrong / pin truth / no pin), and four
  3-hop pipelines scored against a 4-state constant-matrix baseline. Deferred from section 4:
  role (rewrite) edges, a real lookup tool (F3), a third model family, a frontier model,
  and an executor with an asking cost.
- Pre-registered L2 tests: (1) H1 slope b > 0 with an item-bootstrap interval that excludes
  0, for F0 adjacent, on both models and both error types; (2) the observed P(V to S) at the
  held-out 800-word level lies inside the predicted interval in at least 75% of cells;
  (3) the c-dependent law has lower 4-state log loss than the constant-matrix baseline in at
  least 75% of pipeline cells. Kill rule: if (1) fails on both models, H1 is a null.

## 6. Cost sketch (flash only, before frontier)

c-dose relay grid: 2 models x 4 c levels x 4 states x 3 fixes x 2 errors x 24 items x 3
samples, about 13,800 calls. At the observed L1 rate ($0.00045 per call in stage C) this is about
$6. The key has $9.68 spent of its $50 limit, before L1 stage P is added.
