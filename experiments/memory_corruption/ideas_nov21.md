# Breakthrough plan: a reliability law for agent teams (21 November 2026, Pacific Time)

This file is design advice only. It does not authorize spend. Nothing has been spent on these ideas. A later screen needs Bijin's own yes and a dollar cap in his words. Frontier models, if ever used, need his own key and cap, and only after a cheap result passes a frozen gate. Push nothing to main. Load any future key only from the env file; never print, log, or copy it.

The goal is a breakthrough in how people build reliable multi-agent systems: a law or a mechanism, not another measured failure rate. A paper is a side quest. v3 is a building block, not the end of the line.

## The engineering identity

The NeurIPS and ICML reviewers wrote the identity that organizes everything below. Silent irreversible harm is the product of three terms:

```
silent harm  =  P(error enters)  x  P(error is not made visible)  x  P(act | not visible)
```

The v3 confirmatory grid (432 of 432 runs, both flash models, fresh seeds 16 to 21) measured the complementary detection term, on a visible two-channel conflict, with asking free:

- Deepseek asked in 141 of 144 primary runs (97.9%), never booked a typo, 3 harness-error "other" runs.
- GLM asked in 134 of 144 (93.1%). The 10 actors all followed the verbatim block. That produced 5 typo bookings when verbatim held the typo, 9 of them on trip_long (ask rate 62.5% there; 100% or 96% on the other five templates).
- Pooled, asking given a visible conflict is about 0.95. That is a ceiling or control arm, not a result to restudy.

So real remaining harm has to come from (1) the error never becoming a visible conflict, or (2) pressure that stops asking even when the conflict is visible, or (3) a correction that arrives after a side effect is already committed, where asking does not help because the agent has no doubt.

Our own traces already hint at (1) and (2). trip_long handoffs were about 3,700 to 4,100 characters, versus about 1,000 for short trip. When GLM did not ask, executors often wrote that they "copied the call arguments exactly." The earlier hedge-chain screen kept values 16 of 16 times but dropped the "unverified" tag 8 of 8 times under compression; hops did not invent values. Natural substitution in flash compressed summaries was 0 of 247 slots, so typo substitution stays an injected threat model and must be labeled as such. Three earlier "large effects" were harness artifacts (JSON-mode empty summaries, missing listings plus a stale-looking lookup, and a no-cue typo ceiling). Any new screen must put those checks first.

## The unified thesis

Anthropic's per-hop transmission coefficient r, and the reviewers' composition law for conflict survival s^k, are the same law, written from two sides (error survival versus disagreement survival). The reviewers' new top idea, redundant handoffs as error detection, and Anthropic's later mechanisms (an effect ledger with compensations, provenance tags) are ways to cut the effective r. The identity then becomes a design rule:

- Measure r (or s) once, per hop type, on cheap single hops.
- Predict whether a chain, tree, or swarm stays reliable, like a link budget or an epidemic threshold (reliable only when r times the branching factor is below 1, or when disagreement survival stays high enough that the actor still sees a conflict).
- If two channels fail independently, a disagreement is almost surely visible, and v3 says the actor then asks about 95% of the time. Residual silent harm is about p1 * p2 * (1 + a correlation term) * P(act | visible).
- If channels fail together, redundancy is a parity bit that does not flip. That is the Knight and Leveson lesson, applied to agent state.
- If r is still too high, a ledger of committed effects (book has cancel) and provenance tags (source and hop on each fact) are structural ways to push r down, where a prompt rule may not.

If this package holds, people can compute a team's reliability before they build it, choose channel diversity by measured correlation, and recover from a late correction without depending on the model being clever. That is the version that would change how these systems are built.

## Closest prior work (opened on official pages this turn)

Citations below were checked on a real arXiv, IEEE, ACM, proceedings, or official site page on 21 November 2026, Pacific Time. 2026 preprints are mostly not peer reviewed; treat their numbers as preprint claims. This is not an exhaustive survey.

### Benchmarks and taxonomies

- Cemri, Pan, Yang, Agrawal, et al. Why Do Multi-Agent LLM Systems Fail? [arXiv:2503.13657](https://arxiv.org/abs/2503.13657), site [sites.google.com/berkeley.edu/mast](https://sites.google.com/berkeley.edu/mast). MAST: 14 failure modes, 1,600+ traces, 7 frameworks. Taxonomy, not a composition law, and not irreversible tools under controlled injection.
- Yao, Shinn, Razavi, Narasimhan. tau-bench. [arXiv:2406.12045](https://arxiv.org/abs/2406.12045). Tool-agent-user tasks, pass^k reliability. Barres et al. tau^2-bench. [arXiv:2506.07982](https://arxiv.org/abs/2506.07982). Dual-control telecom. These score task success, not inter-agent state correlation or per-hop transmission.
- Debenedetti, Zhang, et al. AgentDojo. [arXiv:2406.13352](https://arxiv.org/abs/2406.13352). Prompt-injection robustness over tools. Not handoff composition.

### Memory poisoning (enters the first term, P(error enters))

- Chen, Xiang, Xiao, Song, Li. AgentPoison. NeurIPS 2024. [arXiv:2407.12784](https://arxiv.org/abs/2407.12784), [proceedings](https://proceedings.neurips.cc/paper_files/paper/2024/hash/eb113910e9c3f6242541c1652e30dfd6-Abstract-Conference.html). Backdoor triggers in agent memory or RAG, attack success at or above 80% with poison rate under 0.1%.
- Dong, Xu, He, et al. MINJA. NeurIPS 2025. [arXiv:2503.03704](https://arxiv.org/abs/2503.03704). Query-only memory injection, no direct write to the bank.

### Handoff erasure (the second term, P(not made visible))

These papers show that erasure exists. They do not, on the pages opened here, pre-register a single-hop coefficient and then test whether it predicts a new topology.

- Zhang, Yu, Ding, et al. Multi-Agent Memory Inconsistency: Where Factual Conflicts Disappear in LLM Agents. [arXiv:2611.22245](https://arxiv.org/abs/2611.22245). Required delegation raises source-pair recall from 50.2% to 68.7%, but both claims appear in only 35.2% of worker reports; detection can fall. The hole is at the worker-to-lead handoff. Document-review conflicts, not typo state passed to irreversible tools.
- Shah and Kathrotia. Securing and Auditing Long-Horizon LLM Agents. [arXiv:2611.23346](https://arxiv.org/abs/2611.23346). Compaction and other state transfers drop constraints (they cite compactors keeping about 17% of user constraints on ConPACT). They propose harness audit points, pinning constraints, and transactional recovery. An audit architecture, not a measured s^k law on our kind of conflict.
- Facts Without Rules: Boundary Metadata Collapse in Multi-Agent LLM Handoffs. [arXiv:2608.29028](https://arxiv.org/abs/2608.29028). Short budgets keep operational facts near ceiling and drop boundary markers (example: 25-word budget, boundary survival about 0.57).
- When "Must" Becomes "Maybe": Erosion of Workflow Constraints in LLM Agent Handoffs. [arXiv:2608.24569](https://arxiv.org/abs/2608.24569). The role of a blocker changes under compression or deferral; semantic presence is not operational preservation. Multihop compression deactivates blockers in 97.2% of their cases.
- Agents Cannot Hand Over What They Have Not Yet Understood. [arXiv:2611.18642](https://arxiv.org/abs/2611.18642). Repeated 300-character notes lose more than one compression; rewriting from the full record recovers much of the loss.
- State Compression in Two-Agent LLM Relays. [arXiv:2607.18265](https://arxiv.org/abs/2607.18265). Narrative summary feasibility 0.48 versus JSON extraction 0.96 in a closed-world travel relay.
- When Do Summaries Become Instructions? [arXiv:2611.06545](https://arxiv.org/abs/2611.06545). Unsupported policy can appear in self-written summaries.

Our earlier hops and hedge screens sit next to this line: values often survive, hedges and doubt tags often do not. That is why the next measurement must code disagreement survival separately from typo survival.

### Epidemic and composition models (Idea A / reviewers' B). This is the sharpest novelty risk.

Anthropic's claim that "prior work does not measure per-hop transfer or test composition on a new topology" is too strong. Several 2026 preprints already write transmission and threshold models:

- Xie et al. From Spark to Fire. [arXiv:2603.04474](https://arxiv.org/abs/2603.04474). Error adoption on a directed dependency graph; early amplification when beta * rho(A) > delta. Six frameworks, cascade amplification, a governance plugin.
- Collective Loss of Control in LLM Agent Systems: An Epidemic Account. [arXiv:2609.18460](https://arxiv.org/abs/2609.18460). Next-generation matrix K, R0 = rho(K); in a homogeneous system R0 = d * q with fan-out d. R0 < 1 bounds expected cascade size.
- Reliability-Contagion Feasibility in LLM Multi-Agent Networks. [arXiv:2607.21912](https://arxiv.org/abs/2607.21912). Correction-aware SEIR-style model, invasion condition, a small grok-4.3 topology experiment: mean first-generation offspring rose with degree (0.667 to 1.333 to 1.667) while adoption among exposed neighbors stayed 0.333.
- Hallucination Cascade. [arXiv:2606.07937](https://arxiv.org/abs/2606.07937). Empirical propagation of unsupported claims.

The remaining gap, if we are honest, is not "the first transmission model." It is a pre-registered composition test: measure a single-hop r or s on a frozen, irreversible-tool handoff, predict a new chain depth and (if budget allows) a small tree before those runs are seen, and kill the simple law if the prediction misses. That is still worth doing, and it is the law-shaped claim. If it fails, "hops interact" is itself a result, and it would qualify the epidemic papers above.

### N-version independence (reviewers' A)

- Knight and Leveson. An experimental evaluation of the assumption of independence in multiversion programming. IEEE Transactions on Software Engineering, SE-12:96-109, 1986. [doi:10.1109/TSE.1986.6312924](https://doi.org/10.1109/tse.1986.6312924), [ACM copy](https://dl.acm.org/doi/10.1109/TSE.1986.6312924), [IEEE Xplore](https://ieeexplore.ieee.org/document/6312924). 27 independently written Pascal versions, one million tests: versions were highly reliable alone, but coincident failures were substantially more common than independence predicts. N-version reliability models that assume independent failures were rejected at high confidence in that experiment.

Self-consistency and voting exist for final answers. The opened pages do not show a measurement of inter-channel error correlation for STATE passed between agents (verbatim arguments versus a summary, same prompt versus a different prompt, same model versus a different model), with a clean-run false-alarm rate, on irreversible tools. That is the opening. If same-model channels are highly correlated and cross-model channels are not, builders get a design law, not a slogan about redundancy.

### Sagas, transactions, and recovery without undo (Anthropic B)

The saga pattern has already been carried into agent work. Anthropic's "has not been carried over" is too strong. Closest opened work:

- Li, Wang, et al. MemTX. [arXiv:2607.23929](https://arxiv.org/abs/2607.23929). Belief-commit lifecycle, irreversible calls gated on uncommitted beliefs, cascading repair, zero downstream harm on five backbones in their protocol tests.
- SagaLLM. [arXiv:2503.11951](https://arxiv.org/abs/2503.11951). Saga pattern, compensation, validation agents, for multi-agent planning.
- Atomix. [arXiv:2602.14849](https://arxiv.org/abs/2602.14849). Progress-aware transactions; reversible effects compensate on abort; irreversible-gated effects fire only at commit.
- Revisable by Design. [arXiv:2604.23283](https://arxiv.org/abs/2604.23283). Reversibility classes (idempotent, reversible, compensable, irreversible) for agent actions, saga-style compensations, earliest-conflict rollback.

The gap left for us is empirical and narrow: after a late correction in a handoff chain, does the wrong committed effect stay live (double book, no cancel), does that get worse with depth or with a different agent receiving the correction, and does a typed effect ledger beat a one-line "reconcile state" prompt by a large margin? MemTX and Atomix are protocol papers. They do not, on the opened pages, report that depth curve in a typo-correction booking world like ours. This is a strong second-wave mechanism, not the first screen.

### Provenance and surgical repair (Anthropic C)

- BRIDGE. [arXiv:2610.17805](https://arxiv.org/abs/2610.17805). Provenance graph, selective handoff repair, gains on SILO-BENCH and TeamBench, 69.8% F1 on 562 MAST handoff traces.
- BLADE. [arXiv:2610.18780](https://arxiv.org/abs/2610.18780). Audits source-decision handoffs for error introduction versus faithful propagation.
- TRACE-MAS. [arXiv:2611.23362](https://arxiv.org/abs/2611.23362). Diagnoses collaboration failures and reports a 60.2% improvement in root-cause localization in their experiments.
- From Agent Traces to Trust (survey). [arXiv:2606.04990](https://arxiv.org/abs/2606.04990). Evidence tracing and execution provenance.

Provenance for agents is no longer empty. The gap is whether cheap, per-fact source-and-hop tags in a short handoff let a recovery agent localize a single injected typo hop, and recompute only dependents, on our irreversible-tool world, with a frozen 25-point bar against a plain-text baseline. Second-wave, after the law screen.

### Asking and clarification (reviewers' pressure idea)

- AskToAct. [arXiv:2503.01940](https://arxiv.org/abs/2503.01940). Clarification from missing tool parameters.
- Learning to Ask: When LLM Agents Meet Unclear Instruction. [ACL Anthology 2025.emnlp-main.1104](https://aclanthology.org/2025.emnlp-main.1104/). Ask-when-Needed on noisy user instructions.
- Structured Uncertainty guided Clarification. [arXiv:2511.08798](https://arxiv.org/abs/2511.08798).
- Uncertainty-Aware Clarification with Information Gain. [arXiv:2606.03135](https://arxiv.org/abs/2606.03135). On a clarification-enhanced tau-bench.
- Clarify the User or Verify the World? [arXiv:2609.32255](https://arxiv.org/abs/2609.32255). Routes uncertainty to user versus world.

These study ambiguous or incomplete user requests, not conflicting inter-agent state under deployment pressure (autonomy, ask cost, rebuff, authority framing). Pressure remains a real follow-on, with medium breakthrough value: a design rule (every autonomy prompt needs a hold path that does not need the user), not a law. v3 already supplies the no-pressure ceiling.

## Ranked candidates

Ranked by breakthrough potential, then feasibility. Costs use a conservative planning rate of about $0.005 per short run and about $0.007 to $0.008 per deeper chain, above the v3 realized mean of about $0.0033, because chains are longer. These are estimates only.

### 1. A composition law for transmission, plus a channel-correlation probe (recommended first)

This is Anthropic's law and the reviewers' A and B, combined. Full protocol is in the next section.

- Hypothesis: (i) disagreement or typo survival after k hops is predictable from a single-hop s (simple form s^k, backup form s_inf + (1 - s_inf) a^k). (ii) LLM handoff channels do not fail independently; P(both wrong the same way) exceeds p1 * p2, more so for same-model same-prompt pairs than for cross-model pairs, in the Knight and Leveson sense.
- What it unlocks if true: reliability engineering. Measure hops and channel pairs once, predict a new topology, and choose diversity to drive residual silent harm down, using v3's 0.95 ask rate once a conflict is visible.
- Why no one has done this exact test: epidemic MAS papers already write R0 models, but the opened work does not pre-register a single-hop coefficient on a frozen irreversible-tool handoff and then predict a new depth or tree. N-version independence has not been measured for agent state channels.
- Novelty risk: medium for the law (Spark to Fire and the contagion papers are real neighbors; we must cite them and claim composition-on-a-new-topology, not priority on the metaphor). Lower for the correlation probe, if framed as Knight and Leveson for handoff state.
- Kill and cost: see the frozen screen below, about $1.80 expected, hard cap $2.80, never above $3.00.

### 2. An effect ledger with compensations (saga), after the law screen

- Hypothesis: once a typo booking is committed, a later correction often produces a second correct booking without cancelling the first, especially when a different agent receives the correction or the handoff omits the earlier commit. A typed ledger (each effect carries its compensation) makes final-state recovery nearly independent of depth and of who sees the correction, and beats a "reconcile state" prompt by a large margin.
- What it unlocks if true: rollback for agent teams, so a late fix does not leave the damage live. This is state ownership, not more reasoning. Asking cannot hide it, because the agent may have no doubt.
- Why it is not already settled: SagaLLM, MemTX, Atomix, and Revisable by Design exist. They have not, in the opened pages, published this depth-and-ownership curve in a booking world with cancel and list_bookings.
- First falsifiable step after S0: 2 models, arms (same agent gets the correction; different agent; summary omits the commit; ledger versus prompt rule), about 96 to 120 runs, about $0.80.
- Kill: no-ledger recovery already over 90% in the hard arms on both models, or the ledger beats the best prompt rule by under 20 points.
- Breakthrough potential: high as a mechanism that cuts r after a side effect. Feasibility: high, once book/cancel/list exist. Do not run it first; the law screen is cheaper to falsify and more central.

### 3. Provenance tags for localization and surgical repair

- Hypothesis: per-fact source and hop tags raise the rate at which a recovery agent names the faulty hop in a 4-hop chain by at least 25 points over plain text, unless the baseline is already over 85%.
- What it unlocks if true: blame assignment and repair cost that scales with the damage, not the swarm. Also makes stale writes visible (lost updates).
- Why it is not already settled: BRIDGE, BLADE, and TRACE-MAS already do provenance graphs and attribution. The cheap, frozen test on our typo hops is still useful as a mechanism to cut r, not as a first claim of "provenance for agents."
- First step: about 120 deepseek runs, about $0.80. Kill as Anthropic stated (under 25 points, or baseline over 85%).
- Breakthrough potential: medium-high as part of the package, medium alone. Second-wave.

### 4. Pressure flips detection into a shortcut (medium)

- Hypothesis: under autonomy, no-ask-tool (with hold still available), ask cost, or rebuff, asking falls and silent typo acts rise, mostly verbatim-following, especially on long context (our trip_long hint).
- What it unlocks: a design rule that every autonomy mode needs a hold-or-escalate path that does not need the user.
- Why not first: it is an important safety fact, not a law. v3 is the control. Clarification papers do not cover this exact pressure on inter-agent state.
- First step: about $1, 2 templates, n=3, autonomy plus hold-available plus rebuff, against the v3 ceiling. Kill if asking stays at least 80% and silent wrong stays under 5% in every pressure cell on both models (reviewers' first kill; later they also stated a silent-wrong-under-5% kill for the breakthrough ranking). Use the tighter combined kill: asking stays at least 80% AND silent wrong stays under 5% everywhere.
- Breakthrough potential: medium. Run only if S0 passes or if S0 finds no erasure and we still need a path to harm.

### 5. Stakes-blind verification (demoted)

Single-channel typo, verify-tool cost, reversible versus irreversible. Neighbors in calibration and clarification. Natural substitution was 0 of 247, so the threat stays injected. Kill if verification is at ceiling or floor everywhere. About $0.50 to $3 depending on grid size. Do not start here.

## Recommended first screen (S0), frozen before any data

One combined screen, under $3, no spend until Bijin approves in his own words. It measures the law and the independence assumption together. v3 is not rerun; it is the P(ask | visible) constant, about 0.95.

Cost basis: conservative $0.005 short, $0.0075 chain. Expected about $1.80. Soft cap $2.80. Hard stop $3.00 new and the standing $19.75 cumulative stop. If the cap is hit, stop and report a partial grid. No frontier models. No MAIN_GO in this file.

### Part 1. Composition law (r or s)

Setup. Reuse the v3 world (trip and trip_long; typo neighbor, edit distance 1, injected after the planner so both IDs are bookable). One summarizer hop is a compression of the current handoff, plain text only, 4,000-token cap, json_object off (do not repeat the empty-summary artifact). Code, separately: (i) typo survival, (ii) disagreement survival if two values were present, (iii) hedge or doubt-tag survival if present. Empty or invalid summaries are invalid runs, not drops.

Design. Two flash models, two templates, depths 1, 2, and 4, n=12 fresh seeds. That is 144 chain runs. Optional, only if Part 1 is under budget after depth 1 is in: a fan-out-3 tree, one model, one template, n=16, predicted from the same s1.

Frozen predictions, written before depth 2 and 4 are scored (depth 1 may be measured first, then locked):

- Primary: s_k = s_1^k for typo survival and, separately, for disagreement survival.
- Backup, also frozen: s_k = s_inf + (1 - s_inf) * a^k, with s_inf and a fit only on depth 1 and 2 if needed, then tested at depth 4. Do not peek at depth 4 to choose the family.

Kill the simple law if, on both models, the predicted rate at depth 2 or 4 misses the observed rate by more than 2x or falls outside its template-aware bootstrap 95% CI. Report "hops interact" and stop claiming independence of hops. Kill the phenomenon if typo survival and disagreement survival are both above 0.9 at depth 4 on both models (nothing to model; write the robustness note). A backup-family fit that then hits depth 4 inside its CI is a qualified law, not a fishing win, because the family was named in advance.

### Part 2. Channel correlation (N-version probe)

Setup. Same typo injection in the planner source. Channels: (V) verbatim argument block, extracted deterministically; (S_same) a short summary, same model, same frozen prompt, two independent samples; (S_alt) same model, a second frozen prompt; (S_cross) the other flash model, the first prompt.

Pairs, scored on whether the target slot is typo, truth, dropped, or other:

- same model, same prompt (two i.i.d. summaries)
- same model, different prompt
- cross model
- verbatim versus each summary type (verbatim is a different encoding, not a second sample of the same generator)

For each pair, compare P(both wrong in the same way) to p1 * p2. Also run clean (no typo) sources through the same channel pairs and, if a disagreement appears, optionally pass them to a short executor check to estimate the false-alarm ask rate. v3 already says visible disagreements are asked about; the new number that can kill the mechanism is the clean false-alarm rate.

Volume. 2 templates, 20 typo seeds, 12 clean seeds, 3 summaries per item (deepseek prompt 1, deepseek prompt 2, glm prompt 1). That is (20 + 12) * 2 * 3 = 192 short summarizer runs, plus verbatim at no model cost.

Kill the mechanism if the clean-run false-alarm disagreement (or ask) rate is above 20%. Kill the "correlation law" if, for every pair type, P(both wrong the same way) is consistent with independence (near p1 * p2, correlation near zero). In that case redundancy works and there is no Knight-and-Leveson-style law to report, only a known trick. The interesting outcome, and the one we expect, is high same-model correlation and lower cross-model correlation.

### Harness checks, required before scoring

- Summaries are plain text and non-empty. Invalid summaries are not coded as typo drops.
- The true ID and the typo are both in the live catalog and bookable after inject (unit test, as in the v3 world-fix tests).
- Report the clean C0 false-positive disagreement rate.
- Disclose all eight prior designs in the forking-paths file if this is ever written up.
- Grade typo survival, disagreement survival, and action harm separately. Silent wrong versus flag-and-proceed stays in the codebook for any executor arm.

### What would count as a breakthrough signal, later

Flash-only results are a signal, not a law. A law claim would later need at least three model families, including one frontier model on Bijin's own key and cap, and a prediction made before the test that the new topology then matches. A large-effect ledger or provenance fix, and a released harness, would be needed before anyone should talk about an 8 or 9 workshop score. None of that is authorized by this file.

### What not to do in S0

Do not remove the ask tool in this screen. Do not add autonomy pressure. Do not restudy v3's 2x2. Do not start the ledger or provenance grids until S0 is scored against the kill rules above. If S0 is a null on both parts, stop this line or write a short robustness note; do not invent a ninth primary.

## Honest bottom line

The breakthrough-shaped thesis is: errors in agent teams spread by a measurable, composable transmission coefficient, and the residual silent harm of a redundant handoff is set by measured channel correlation, after which a visible conflict is usually asked about (v3). Ledgers and provenance are the structural tools that push the coefficient down.

The closest papers already contain epidemic models, sagas, and provenance graphs. The honest opening is a frozen prediction on a new topology, plus an N-version correlation measurement for handoff state. That is still the strongest cheap test we have. It is not guaranteed to work. The kill rules are there so a miss is a result, not a redesign.

Recommended action: ask Bijin whether to run S0 under a cap of $2.80 new (hard stop $3.00), on the shared key, flash models only. No hashes, no runner, and no API calls until he says yes in his own words.
