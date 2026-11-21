# Candidate studies, 21 November 2026

These ideas are for a main conference paper (ICML 2027 official abstract 14 January 2027 and paper 22 January 2027, AoE; AAAI-27 is closed; AAAI-28 dates are not official). They build on our A8 and A9 smokes (short handoffs never lost a flag; pipelines over-flagged clean runs; harm sat where no role had a source; call count is a confound), the MAS selective recovery work and the E4 audit ("recovered is not harmless"), and the 21 November sweep.

A "revelation" here means a result that would surprise a reader of Cognition, Anthropic, Li and Wang, Sun, or Zhang et al. 2611.22245, and that would still be true after a compute matched single agent and matched access. Honest scores are reviewer style, not a promise. Flash models first. A frontier arm is discussed only as a later add-on.

## 1. Handoff stress and context compression (recommended)

**Hypothesis.** When a planner's plan and flags are long or then compressed to a short summary, silent irreversible harm rises and flags fail to reach the actor, compared with a short uncompresssed handoff and compared with a compute matched single agent that keeps the same memory access.

**Why surprising.** Our short A9 handoffs never lost a flag, and pipelines asked rather than harmed. Cognition and Anthropic both treat compression as necessary for long work. If compression is the place where safety is lost, then "just add a planner" is safe only for short notes, and the practitioner habit of returning a 1,000 to 2,000 token subagent summary (Anthropic context engineering) is a safety risk, not just a quality risk.

**Mechanism.** The actor never sees MEMORY.md. The only way a noticed conflict survives is through the plan or the flag channel. A long pad or a summary can drop the flag or the constraint (Shah and Kathrotia: compaction drops user constraints; Zhang et al.: conflicts disappear at worker to lead handoffs).

**Falsifier.** Under long or compressed handoffs, silent harm and flag arrival stay within a few points of the short handoff and of the compute matched single agent.

**Minimal manipulation.** After the planner, pad the handoff with many dummy fields, or summarize it to about 80 words, or summarize but keep the original flags. Cross these with a short handoff and with a single agent that first writes a plan, then acts, with memory still in context (compute matched).

**Controls.** Compute matched single agent; matched access (planner has the profile, as in P-full); every cell including U1; identical flag and ask wording.

**Metrics.** Silent harm, irreversible harm, flag reached the actor, C0 ask or hold rate, task completion, tokens, cost.

**Expected effect.** Short handoff: low silent harm, high asking on F3 (A9 pattern). Compressed handoff: higher silent harm, lower flag arrival. Compressed with flags kept: harm stays low if the flag, not the plan text, is the channel. U1: harm in every arm (ceiling).

**Cost on flash.** A workshop grid of 5 to 6 arms, 3 or 4 cells, 2 tasks, 2 flash models, n=30 is likely in the $8 to $14 range at our measured rates, plus screening. Must be re-estimated after the 21 November screen.

**Frontier arm.** Not required for the core paper. Useful later if flash shows a clean compression effect, because Li and Wang found much smaller handoff loss on stronger models (3 to 6%).

**Novelty.** Zhang et al. 2611.22245 is the closest and is days old (detection, document review, compute comparable). Anchan and Sen 2610.06069 model a handoff tax on ledger accuracy. We would be first to pair irreversible silent harm with C0 over-asking and completion under controlled compression, with a compute matched single agent. That is a real opening, not a first-to-show of handoff loss as such.

**Score.** Workshop 6 to 7 if the floor appears under compression and the compute matched control is clean. ICML main 4 to 5 on flash only; 5 to 6 if a later frontier arm replicates. Not a 9.

## 2. Safety and cost frontier: more agents, more flags, less completion (screen with idea 1)

**Hypothesis.** Extra roles raise flags and asks on both damaged and clean runs, so "safer" pipelines complete fewer clean tasks and spend more tokens. After a compute matched single agent, most of the extra caution is extra calls, not role structure.

**Why surprising.** It turns our A9 "pipelines look careful" result into a cost finding, and it tests Anthropic's 15 times token claim as a safety tradeoff rather than a quality win.

**Mechanism.** Each role is another chance to flag or ask (ensemble math). If C0 ask rates track call count, not topology, the safety gain is not a MAS effect.

**Falsifier.** After matching call count, C0 ask rates and silent harm on damaged cells do not differ by architecture.

**Minimal manipulation.** Same setups as idea 1, scored as a pair: silent harm on F3 and U1, plus C0 ask rate, completion, and tokens.

**Controls, metrics, cost.** Same as idea 1. This is a second analysis of the same screen and the same later grid, not a second experiment.

**Expected effect.** Pipelines flag and ask more on C0. Compute matched single agent closes much of the gap.

**Frontier.** Same rule as idea 1.

**Novelty.** Moderate. Huang already shows inspectors recover errors. The new piece is the pre-registered pair of harm and over-asking, which reviewers asked for.

**Score.** Workshop 6 if the pair is pre-registered and powered. Main 4 to 5 alone; stronger as the cost chapter of idea 1.

## 3. Handoff length as the manipulated variable (long horizon, tau-bench style)

**Hypothesis.** There is a crossover depth, as in Anchan and Sen, but for silent harm and over-asking, not only for ledger accuracy. Short plans (our A9) lose no flags. Long or multi-step plans start to lose them, until compression or a fresh context becomes necessary and then costly.

**Why surprising.** It would explain why our first two smokes were null and why lab blogs still worry about context loss, without claiming the A9 null was a mistake.

**Mechanism.** Attention tax in one long context versus handoff tax after a reset or a summary.

**Falsifier.** No depth at which pipeline silent harm exceeds the compute matched single agent by the minimum detectable effect.

**Minimal manipulation.** Vary the number of fields, steps, or a compressed mid-task summary, holding the model and the damage cell fixed.

**Controls and metrics.** Same as idea 1, plus depth as a factor.

**Cost.** Higher: more steps means more tokens. A flash only depth sweep could fit the remaining cap only if n and the number of depths are cut.

**Frontier.** Needed if we want to say the crossover survives scale. Not first.

**Novelty.** Direct empirical test of 2610.06069 in a safety setting. Risk of being read as a follow-up, not a new problem.

**Score.** Workshop 6 to 7, main 5 if the crossover is sharp and includes cost. Heavy for the 22 January deadline.

## 4. Shared scratchpad cascades over many steps

**Hypothesis.** A single memory error, written into a shared scratchpad, spreads and is acted on more often as more roles write and read it, even when an early role flagged it.

**Why surprising.** Xie et al. (Spark to Fire) model message infection. MemoryDojo studies poisoning across sessions. A controlled cascade depth on irreversible tools with a flag that dies in later writes would be a new slice.

**Mechanism.** Later writers overwrite or omit the flag; later actors trust the latest note.

**Falsifier.** Harm does not rise with cascade depth, or a written flag always reaches the last actor.

**Minimal manipulation.** k write and read hops on one MEMORY.md or scratch file, damage injected at hop 1, action at hop k.

**Cost.** Medium to high. New task logic. Screening can use 2 hops only.

**Frontier.** Helpful, not first.

**Novelty.** Moderate. Overlaps MemoryDojo and Xie. Stronger if the metric is irreversible harm after a non-actor flag.

**Score.** Workshop 5 to 6, main 4. Good second paper, tight for January if idea 1 is the lead.

## 5. Recovery methods that re-execute side effects (E4 follow-on)

**Hypothesis.** A recovery method that retries or rolls back across agents can look recovered while leaving a harmful side effect, or can duplicate an effect (Xu: idempotency is not recovery). "Recovered is not harmless" holds when a second agent retries.

**Why surprising.** It extends our E4 audit into a MAS result that practitioners will recognize from crash and retry.

**Mechanism.** The second agent does not see the first effect, or treats an idempotent retry as a fix.

**Falsifier.** After a standard recovery method, oracle-visible side effects match the "recovered" label.

**Minimal manipulation.** Inject a crash or a timeout after an irreversible tool, then run the recovery method on a second role.

**Cost.** Harness work is large. Screening would be a thin slice.

**Frontier.** Useful, not first.

**Novelty.** Good fit with Xu 2611.23353 and E4, but may read as a systems paper more than an ICML main paper unless the behavioral claim is sharp.

**Score.** Workshop 5 to 6, main 3 to 5. Better as a follow-on than as the January lead.

## 6. Orchestrator subagent truncation

**Hypothesis.** A lead agent that receives only a short subagent summary will miss a conflict the subagent saw, and will act or approve a harmful call. Giving the lead the raw evidence restores detection, at a token cost.

**Why surprising.** This is Anthropic's research architecture, tested as a safety failure rather than a quality win. Zhang et al. already show a version of this in document review.

**Mechanism.** Same as idea 1, but worker to lead rather than planner to executor.

**Falsifier.** Lead detection with a summary matches lead detection with raw evidence, or matches a single agent with the same budget.

**Cost and score.** Close to idea 1. Higher overlap with 2611.22245, so I would not lead with this unless we keep irreversible tools and the over-asking pair as the difference. Main 3 to 5 because of that overlap. Workshop 5 to 6.

## 7. Clean context reviewer versus shared traces

**Hypothesis.** A reviewer that does not see the planner's flags or traces (Cognition 2026 review loop) misses more safety conflicts than a reviewer that sees the flags, but may false-veto less on clean runs.

**Why surprising.** It would resolve the tension between "share full traces" (Cognition 2025) and "clean context is smarter" (Cognition 2026) on a safety metric.

**Falsifier.** Veto and harm rates do not differ, or the clean reviewer is better on both harm and false vetoes.

**Cost.** Modest, can reuse the verifier arms.

**Score.** Workshop 5 to 6, main 3 to 4. Nice note, thinner than idea 1.

## 8. Confirm-before-act as a measured fix (keep deferred)

**Hypothesis.** A frozen confirm rule cuts silent harm at a measured ask cost on C0, including in pipelines.

**Why surprising.** Less than the others, and we already planned this in earlier amendments.

**Score.** Workshop 5, main 3. Keep as a secondary arm only after a floor exists.

## Ranking and what to screen

1. Idea 1, handoff stress and compression, plus idea 2 as the pre-registered cost pair (one experiment).
2. Idea 3, depth crossover, if the screen shows room under compression or length, and if the January grid would still fit the cap.
3. Idea 4, short cascade screen only, to see if two hops already move harm.
4. Ideas 5, 6, 7, 8: park, or keep as secondary analyses.

**Screening plan (exploratory, appendix only).** Deepseek only. Six setups: S-full, S-match (self review then act), P short, P long (padded handoff), P compressed, P compressed with flags kept. Cells C0, F3, U1, both tasks, n=2, 72 runs. Look for: a floor or ceiling on silent harm; flag drop under compression; C0 over-asking; whether S-match already explains pipeline caution. If compressed handoffs do not raise silent harm or drop flags, idea 1 has no room and we should not force it.

