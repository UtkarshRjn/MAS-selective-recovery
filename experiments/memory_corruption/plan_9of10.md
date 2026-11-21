# A 9 out of 10 plan for a main conference or journal

Date: Saturday, November 21, 2026, Pacific Time.
This plan sits on top of the current three role harness, the Amendment 7 code (history, not the run grid), the Amendment 8 draft, and the existing MAS selective recovery repository. It is not a freeze. No paid runs start until there is a written yes. Nothing here uses a personal key unless the owner later sets a cap in their own words. The shared UCSD key still has a hard stop at $19.75 cumulative.

## Plain verdict first

A 9 out of 10 main track paper by the official ICML 2027 deadline is not realistic. That deadline is about nine weeks from today. A 9 out of 10 paper at this level of venue needs frontier models, a public multi agent benchmark or framework, a formal or clearly stated model, human ratings, a working fix, and released artifacts. The Amendment 8 flash grid can be finished in time for a workshop paper, or as the first stage of a longer study. It cannot, by itself, be a 9 out of 10 ICML or NeurIPS paper.

## Recommended venue, and why

**Primary conference target: NeurIPS 2027**, but only after official dates are posted.

Official NeurIPS 2027 submission dates were **not** on the public site as of this search on November 21, 2026. The future meetings page confirms a 2027 meeting, with no paper deadline. For planning only, the last official cycle (NeurIPS 2026) used an abstract deadline of May 4, 2026, AoE, and a full paper deadline of May 6, 2026, AoE, from [https://neurips.cc/Conferences/2026/Dates](https://neurips.cc/Conferences/2026/Dates). If the 2027 cycle stays near early May, that is about five to six months from now, which is enough time to finish the flash study, add frontier and benchmark arms, rate a human sample, and write. If official 2027 dates land much earlier, switch the conference plan to TMLR and do not rush a thin paper.

**Journal path: TMLR**, rolling, no deadline. The journal reviews as papers arrive and aims for a decision on a timescale of about two months after submission (from the public TMLR FAQ). This is the right backup, and it can also be the main path if conference dates slip or if the result is solid but not flashy. JMLR is also rolling, but it is slower and expects a more archival piece. Use JMLR only if the formal model and the public benchmark results are both strong.

**ICML 2027** is the nearest official deadline, and it is real. From [https://icml.cc/Conferences/2027/Dates](https://icml.cc/Conferences/2027/Dates): submission opens January 8, 2027; abstract January 14, 2027, 11:59 PM AoE; full paper January 22, 2027, 11:59 PM AoE; first stage rejection February 23, 2027; final notice May 3, 2027. The meeting is in Rio de Janeiro, with expo on July 31, tutorials on August 1, main talks August 2 through 4, and workshops August 5 and 6, 2027. A workshop submission there could be in range if a fitting workshop opens. A main track 9 out of 10 by January 22 is not in range.

**COLM 2027** official dates were not found in this search. Do not treat any spring 2027 guess as official.

Why NeurIPS (or TMLR) over a rushed ICML main paper: the contribution is empirical and architectural. Reviewers at a top ML venue will ask whether the effect holds on a frontier model and on a public agent benchmark, not only on two flash models in a custom booking and cleanup harness. Those checks take months, not weeks.

## What a 9 out of 10 paper would need

1. **A tight claim, already supported as open in the literature review.** Holding the model and the damage cells fixed, a three role pipeline that shares one memory file can lose a logged notice between the agent that flags the damage and the agent that acts. A same model verifier reduces silent irreversible harm only when it can read the raw memory and the profile, not when it sees only the plan. Do not claim to have invented handoff loss or diffusion of responsibility.

2. **Models.** Keep the two flash models for the large grid (cheap, already in the harness). Add at least two frontier models on a personal key, same model in every role of a run. A 9 out of 10 paper that only uses flash models will be scored as a workshop piece.

3. **A public multi agent setting.** Port the same four (or at least three) arms to one widely used framework, such as LangGraph or AutoGen, so the result is not only a custom harness. Also add F, T, and U style memory damage to a public benchmark with a hard environment score. The best fit among opened papers is tau bench (airline and retail, database state scoring, [arXiv:2406.12045](https://arxiv.org/abs/2406.12045)). AgentDojo ([arXiv:2406.13352](https://arxiv.org/abs/2406.13352)) is a backup if the threat we want is tool side damage. MultiAgentBench ([arXiv:2503.01935](https://arxiv.org/abs/2503.01935)) is a backup for topology, not for irreversible booking.

4. **Sample size and power.** Use n=30 for the flash grid (Amendment 8). For frontier and tau bench, use n=30 if the flash effect is large (about 20 points or more on silent harm or on flag to actor loss). If the flash effect is small (under 10 points), raise n to 50 on the key cells or cut arms rather than shipping an underpowered headline. Report confidence intervals. Pre register the primary comparisons before the main flash run.

5. **A small formal model, if it stays honest.** Do not invent a heavy theory. Write a short model of three quantities: P(flag given damage), P(flag reaches the actor given a flag), and P(harm given the flag reached the actor). Place the two verifier conditions in that model (plan only vs raw memory). Cite Han ([arXiv:2607.13918](https://arxiv.org/abs/2607.13918)) on shared verifier blind spots, and Singh and Pawar ([arXiv:2608.14588](https://arxiv.org/abs/2608.14588)) on when verification happens. If the data fit a simple escape or attenuation curve, show it. If they do not, say so.

6. **Human rating.** Two raters, 100 to 200 traces, mixed arms and cells, score whether the flag is about the true damage, whether the irreversible action was truly harmful, and whether the writer report is honest. Report agreement. This protects the paper from the charge that automatic IH is a labeling artifact.

7. **Ablations.** S versus P versus P plus V plan versus P plus V raw, on the same model and cells. Confirm rule on P, at least on C0 and U1, to show a fix and its ask cost. Optional later: a "route the flag to the actor" wiring fix, which is the mechanistic counterpart of the confirm rule.

8. **Released artifacts.** Code, damage recipes, job lists, analysis scripts, and traces with secrets removed. A later pull request on a new branch under `experiments/memory_corruption/`, never on main, after a secret scan.

## Stage A: the Amendment 8 flash study (this can start after a written yes)

This is the study in `pilot/amendment8_DRAFT_2026-11-21.md` and `plan_mas.md`. Four arms (S, P, P plus V plan, P plus V raw), cells C0, F3, T1, U1, two tasks, two flash models, n=30: 1,920 runs. A small confirm rule arm on P, cells C0 and U1: 240 runs. Total 2,160 runs.

Lead figure: the funnel (damage present, flagged, flag reached the actor, harm, writer reported success).

Cost on the shared UCSD key, no new API calls were used to write this file:

1. Point estimate for the four arms, including a 4,000 token default and a retry reserve: about $7.21.
2. Confirm rule arm: about $0.83.
3. New spend: about $8.05, or about $8.74 if the new arms are heavier than planned.
4. Already spent (conservative): $0.96.
5. Cumulative: about $9.01 to $9.70, under the $19.75 hard stop.
6. Headroom after the cautious number: about $10.

Harness hours after a freeze: about 20 to 28 hours, plus a short smoke test (n=2, one flash model, Task A, C0 and U1, all four arms). Leave the leftover UCSD headroom for retries and diagnosis, not for casual extra cells.

This stage, if clean, is a workshop 7 to 8. It is not yet a 9 out of 10 main paper.

## Stage B: the 9 out of 10 add ons (personal key only)

Do not charge these to the UCSD key.

**B1. Frontier replication.** Two frontier models, Task A, arms S, P, and P plus V raw, cells C0, F3, U1, n=30: 540 runs. If Task B is also needed, 1,080 runs. Planning cost band: about $27 to $65 for Task A only, or about $55 to $130 for both tasks, depending on the model prices. Use $40 to $70 as the working number for Task A, and decide on Task B only if Stage A shows a Task difference.

**B2. Public benchmark.** Add F, T, and U memory damage to tau bench airline (and retail if time). Run at least S, P, and P plus V raw, one flash model and one frontier model. Planning cost band: about $20 to $50, plus 25 to 40 hours of porting and checks.

**B3. Public framework.** The same three arms in LangGraph or AutoGen, on Task A or on the tau bench slice, one model at first. Hours: about 15 to 25. API cost is small if it shares the B1 or B2 runs.

**B4. Human rating.** Two raters, 100 to 200 traces. Hours: about 10 to 20, plus rater time.

**B5. Write and release.** Formal paragraph, funnel figure, related work against the opened papers, limitations, artifact checklist. Hours: about 30 to 40.

Stage B total money: about $70 to $150 on a personal key, with a cap written by the owner before any of these calls. Stage B hours: about 80 to 120, spread over four to five months, on top of the Stage A 20 to 28 hours.

The leftover ~$10 on the UCSD key must not be used for Stage B.

## Timeline (Pacific Time)

1. Now to late November 2026: review this plan and the Amendment 8 draft. No runs until a written yes. Then freeze, build, smoke test.
2. Early to mid December 2026: run the 2,160 flash jobs, under the $19.75 hard stop. Analyze the funnel.
3. Late December 2026 to mid January 2027: write a workshop quality draft from Stage A. Do **not** aim this draft at ICML 2027 main track as a 9 out of 10 paper. If a relevant workshop call is open, that is the honest January target. If Stage A is null, stop (see kill criteria).
4. January to April 2027: Stage B, only with a personal key and a cap. Frontier, tau bench, framework port, human rating.
5. April 2027 onward: write the full paper. Submit to NeurIPS 2027 if official dates allow a complete paper, and or submit to TMLR on a rolling basis.

If Stage A slips past mid December, keep the same order. Do not compress Stage B into the ICML main deadline.

## Risks

1. Reviewers may say the claim is a special case of Sun et al. ([arXiv:2608.24569](https://arxiv.org/abs/2608.24569)) or Li and Wang ([arXiv:2608.16055](https://arxiv.org/abs/2608.16055)). The paper must stress shared memory damage by visibility class, irreversible tools, the logged flag, and the verifier access test.
2. The flash grid may show no architecture difference. Then the 9 out of 10 path dies (see below).
3. Frontier models may not repeat the flash pattern. Then do not claim generality.
4. A tau bench or LangGraph port may eat weeks. Cut retail or cut one framework rather than miss quality.
5. NeurIPS 2027 dates may be earlier than planned. Then TMLR is the venue, not a rushed conference draft.
6. Personal key cost may exceed the working band. Freeze Stage B at the cap and drop Task B or the second frontier model first.

## Kill criteria

1. No MAIN_GO and no paid calls without a written yes.
2. Halt the flash run if more than 2% of planned jobs need a length retry, or if any retry is still empty, or if the counter would pass $19.75.
3. After Stage A, if S, P, and both verifiers show no reliable difference on irreversible harm or on flag to actor loss, **stop the 9 out of 10 conference plan**. Write the null result honestly. Do not spend a personal key trying to force an effect. A workshop or a short TMLR note may still be honest.
4. If a new paper appears that already runs P plus V plan versus P plus V raw on damaged shared memory with irreversible tools, reopen the novelty check before writing a 9 out of 10 claim.
5. If official NeurIPS 2027 dates land before Stage B can be finished well, submit TMLR instead of a thin conference paper.

## Honest scores for the plan as written

These are reviewer style scores, not promises. They match the earlier group reviews and the literature check.

**Amendment 8 only (two flash models, custom harness, 2,160 runs):** workshop about 7 to 8 if the funnel is clean; main track about 4 to 5.

**Amendment 8 plus one frontier arm, still custom harness:** main track about 5 to 6.

**Full Stage A plus Stage B, if the effects are large and they replicate on frontier models and on tau bench:** workshop about 8 to 9; main track about 7 to 8. A 9 out of 10 is possible only if the effect is large, clearly distinct from the closest papers, and clean on more than one frontier model and on a public benchmark. That is a high bar, not a forecast.

**ICML 2027 main track, January 22, 2027:** a 9 out of 10 is not realistic. Even a 7 is unlikely if only Stage A is done. Do not spend the official deadline on a paper the reviewers have already said is a 4 to 6.

**NeurIPS 2027 (dates not yet official):** a complete Stage A plus Stage B can be a realistic 7 to 8 attempt, and a 9 only if the results are excellent. This is the best conference target among the ones with a plausible calendar.

**TMLR:** a complete, careful paper from this plan is a realistic journal path, judged more on correctness and clarity than on conference wow. This is the most reliable way to land a refereed paper if the conference calendar does not fit.

## Decision the owner needs to make

1. Approve or change the Amendment 8 draft (Stage A) and set a written yes before any new flash runs.
2. Decide later, in their own words, whether to fund Stage B on a personal key, and at what cap. The shared UCSD key must not pay for frontier, Claude, or LangGraph arms.
3. Accept that the 9 out of 10 conference goal is a 2027 NeurIPS or TMLR project, not an ICML January paper.
