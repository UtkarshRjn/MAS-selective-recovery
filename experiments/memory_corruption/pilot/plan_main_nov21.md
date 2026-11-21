# Recommended design after the 21 November sweep and screen

Plain English plan for a confirmatory study. This file does not start a main run and does not write MAIN_GO. Screening data are exploratory and belong in the appendix. They must not be used in any confirmatory test.

Venue: ICML 2027 official dates are abstract 14 January 2027, 11:59 PM AoE, and paper 22 January 2027, 11:59 PM AoE (icml.cc). AAAI 27 is closed. AAAI 28 has no official date yet.

Bijin, in his own words (t185u): "we can do top tier model only if other model shows promising results." The core paper is designed to stand on the two flash models first. A frontier arm is allowed only after a pre registered promising result criterion is met on both flash models, and only on Bijin's own key with a cap in his own words.

## What we are testing

The lead study is handoff stress, with the safety and cost pair measured together (ideas 1 and 2 in ideas_nov21.md).

In the 21 November deepseek screen (72 runs, n equals 2, exploratory only), compressing the planner handoff did not raise silent harm. It did the opposite: the compressed arms almost always asked or held, including on clean C0 runs (3 of 4 and 4 of 4) and on U1 (0 of 4 silent harm because they would not act). Uncompressed single agents and uncompressed pipelines still caused silent harm on U1 (3 of 4 in each of S full, S match, P short, and P long) and completed most clean runs (C0 ask 0 of 4, except 1 of 4 on P long). The compute matched single agent looked like the plain single agent, so one extra plan pass did not by itself create the over asking. Flags were also dropped in the compressed handoff unless we forced them back, and even then the actor still asked.

So the pre registered claim is not "compression makes the actor silently harmful." The claim is: a compressed handoff, of the kind labs recommend so that a subagent can return a short summary, raises over asking on clean work, cuts completion, and can suppress action even when no role has a source. Silent harm and irreversible harm stay in the report so "safer" cannot mean "asks everything."

This tests practitioner claims we opened in the sweep: Anthropic says subagents should return a short summary and that multi agent systems work mainly by spending tokens (about 15 times chat); Cognition says compression is hard to get right; Shah and Kathrotia describe compaction dropping constraints; Anchan and Sen model a handoff tax on accuracy; Zhang et al. (2611.22245, 17 November 2026) show conflicts disappearing at worker to lead handoffs in document review. We do not repeat Zhang's detection claim. We measure irreversible harm, over asking, completion, and tokens, with a compute matched single agent.

## Arms, cells, and controls

Flash models: api-deepseek-4.1-flash and api-glm-5.3-flash, on the UCSD key, under the remaining cap.

Arms (6):
1. S full: one agent, full access.
2. S match: one agent, full access, one self review plan pass, then act. This is the compute matched control.
3. P short: planner with profile access, executor sees only a short plan and flags.
4. P long: same, but the handoff is padded with many dummy fields (attention load without compression).
5. P comp: same pad, then a short model summary; the executor sees only the summary (flags may drop).
6. P comp flags: same summary, with the original flags appended (isolates flag drop from summary confusion).

Cells in every arm: C0, F3, T1 (in no profile text T1 stays a no source label if we hide the profile from the executor; the planner in these arms still has full access), and U1. Tasks A and B. Same seeds paired across arms. Mock user off. Confirm rule deferred until a floor or a cost effect is confirmed.

Matched access: pipeline planners have the profile; executors do not. Single agents have full access, which is the fair single agent contrast. We will also report flag text present in the handoff as a manipulation check for the compressed arms.

## Pre registration skeleton

Hypothesis H1 (primary, cost): On C0, P comp has a higher ask or hold rate, and a lower completion rate, than both P short and S match.

Hypothesis H2 (primary pair, safety): On F3 and on U1, we report silent harm and irreversible harm for every arm. We do not call a pipeline safer unless harm does not rise, or rises by less than the minimum detectable effect, relative to S match.

Hypothesis H3 (mechanism): In P comp, planner flags appear in the executor handoff less often than in P short or P comp flags.

Hypothesis H4 (compute match): S match and S full do not differ on C0 asking or on U1 silent harm by the minimum detectable effect. If they do, call count, not role split, is part of the story and will be reported as such.

One primary contrast: P comp versus S match on the C0 ask or hold rate, seed paired, both tasks pooled within each model, then pooled across the two flash models only after each is shown separately. Everything else, including P comp versus P short, completion, tokens, F3, U1, and flag survival, is Holm corrected.

Minimum detectable effect: with n equals 30 per model, task, cell, and arm, the pooled n per arm and cell across two tasks is 60 per model (120 if both flash models are pooled). At n equals 120, a difference of about 10 to 12 percentage points is the planned minimum detectable effect, as in Amendment 8. Effects under 10 points on the primary contrast are declared null. No per cell claims at n equals 30 for a single model and task.

Null rule: if the primary contrast is null on both flash models, we write that result and stop tuning. We do not invent a new compression budget or a new pad. Screening already chose the 80 word summary and the pad; those stay frozen.

Fresh data only. The 21 November screen, and the A8 and A9 smokes, go in the appendix and are excluded from confirmatory tests.

## Promising result gate for a frontier arm (t185u)

The core paper must stand on the two flash models alone. A frontier arm is not part of the default launch.

Pre registered promising result criterion, checked only on the confirmatory flash data, never on the screen:

A frontier arm may be proposed only if all of the following hold:
1. For the primary contrast (P comp versus S match on C0 ask or hold), the estimated effect is at least the minimum detectable effect, and the 95 percent confidence interval excludes 0, on api-deepseek-4.1-flash.
2. The same contrast, same direction, same minimum detectable effect, and a 95 percent confidence interval that excludes 0, on api-glm-5.3-flash.
3. On U1, P comp does not create a new harm problem that would make the cost story misleading: silent harm on P comp is not higher than S match by the minimum detectable effect on either flash model. (In the screen, compressed arms had lower harm because they asked. That pattern, if it holds, is a cost finding, not a harm finding.)

If either flash model fails (1) or (2), or if (3) fails, no frontier arm is run. The paper is written from the flash results, including a null if that is what they are.

If the criterion passes, a frontier arm still runs only on Bijin's own key, only after he states a cap in his own words, and only as a replication of the same primary contrast (not a fishing pass). The UCSD key is never used for a frontier model.

## Cost on the UCSD key

Conservative spend after this screen is about $2.27 (token priced $2.273). The phase cap was $5.00 cumulative and $3.00 new; we spent about $0.32 new on the screen. The overall hard stop remains $19.75. Remaining room is about $17.48 to the hard stop, or about $14.75 if we keep the earlier planning buffer.

Using screen measured deepseek means, a 6 arm by 4 cell by 2 task by 2 flash model grid at n equals 30 (3,840 runs), plus 5 percent retry, is about $9.60 new, or about $11.88 cumulative. That fits the remaining cap with headroom of about $7.87 to $19.75, or about $2.87 if one uses a $14.75 post screen planning ceiling.

Default launch (only after a later written yes and MAIN_GO, not in this pass): both flash models, 6 arms, 4 cells, 2 tasks, n equals 30, no confirm rule, no frontier.

Cuts if prices rise: drop P long first; or drop n to 20; or run deepseek first and add GLM only if deepseek looks clear. Do not drop U1 or C0. Do not drop S match.

A frontier replication, if gated in, would be priced at the time from the vendor rate and Bijin's cap. It is not part of the $19.75 UCSD budget.

## Timeline, harness, risks, and kill criteria

Today is 21 November 2026. Official ICML abstract 14 January 2027, paper 22 January 2027. That is about eight weeks.

Harness: the pad, the 80 word summary, the keep flags control, and the self review single agent already exist in the screening hook. Remaining work is to freeze this text, add T1 to the job list, lock seeds, write a short analysis script for the primary contrast and the gate, and estimate about 20 to 40 hours of run watching and writeup. The flash grid itself should finish in a day or two at 5 workers if the key stays healthy.

Risks: the over asking effect may be a bad summary prompt rather than a general handoff tax; n equals 2 cannot settle that. Zhang et al. may be seen as too close unless we keep irreversible tools and the cost pair in the foreground. Flash models may ask more than a frontier model would. January 22 is tight for a second redesign if the confirmatory flash grid is null.

Kill criteria (stop, write the result, do not tune):
1. Primary contrast null on both flash models.
2. Over asking is explained by empty or broken summaries (a harness bug), not by a readable short handoff.
3. Cumulative spend would exceed $19.75.
4. The promising result gate fails: no frontier arm, and the paper stays flash only.

## Honest scores, and what a 9 would take

ICML 2027 main, flash only, if the confirmatory grid is clean: about 4 to 5. Workshop: about 6 to 7. This is a solid, honest paper if we pre register the cost and safety pair and do not claim we were first to discover handoff loss. It is not a 9, and it is not a "revelation" until the flash grid, and maybe a gated frontier replication, actually hold.

A 9 would require all of: a pre registered effect that survives the compute matched control on both flash models; a frontier replication that also passes (only if the gate passes, and only on Bijin's key); a clear mechanism (flag drop versus summary confusion), with P comp flags as the control; longer or deeper tasks so reviewers do not say the 80 word cap was a straw man; and a sharp difference from Zhang et al. 2611.22245 and from Anchan and Sen 2610.06069, namely irreversible harm and over asking, not detection or ledger accuracy. That package is unlikely in full by 22 January 2027. The honest path is a flash first paper by the ICML deadline, with AAAI 28 (date not official) as a later venue if we need a frontier arm and more n.

