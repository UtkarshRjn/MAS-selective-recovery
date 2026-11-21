# Recommended design v3, aimed at a 9 of 10 workshop paper

Plain English plan. This file does not start a main run and does not write MAIN_GO. All November 21 screens, including v3 sanity and the two exploratory screens in this pass, are exploratory and belong in the appendix. They must not enter a confirmatory test.

Venue target: a 9 of 10 paper at an ICLR 2027 or ICML 2027 agents or safety workshop. ICML 2027 main is still the stretch venue (abstract 14 January 2027, paper 22 January 2027, 11:59 PM AoE). A 9 on the main track by 22 January is not the honest forecast. A 9 on a workshop paper is the bar Bijin set (t192u) and is the design goal of this plan, if and only if the exploratory screens show a large, clean effect with a working mitigation.

Bijin, in his own words (t185u): "we can do top tier model only if other model shows promising results." Frontier still runs only after a pre registered gate, only on his key, with a cap in his own words.

## The claim we are trying to support

One crisp claim, if the exploratory data support it:

When a required call argument is missing from a planner to executor handoff, a plan following executor often invents a value and takes an irreversible action. A cheap fix, appending a verbatim call argument block (or echoing the slots), removes that fabrication. Compression is the practical way those arguments go missing. The primary figure is a funnel and a before and after bar: slot lost, then fabricate versus ask versus hold, then the same split with the verbatim fix.

If that claim does not show a large, clean effect, the backup claim is a dose curve on long multi constraint tasks: slot loss and wrong action at 80 words, about 250 words, and about 1,000 tokens. If the 1,000 token practitioner budget shows no loss, we report that budget as safe in this setting. That is useful, but it is a weaker workshop paper unless the curve breaks in a surprising place.

## What changed from v2, and why

All three reviewer groups said v2's primary (information complete rate versus P short) was near tautological, and that treating the slot check as a gate threw away the measurement that matters: what the executor does when a slot is gone. v3 makes the slot check a measurement, always passes the summary to the executor, and codes each slot as present, dropped, or substituted. There is no summarizer retry on v3 runs; the first attempt is the outcome. The primary is the wrong action rate, seed paired, all runs: P comp versus P short. The key secondary is the split, on runs with a lost slot, between fabricate and act, ask, and hold. Slot loss is the descriptive first stage of the funnel.

U1 is a context row only, not a contrast.

This is the fourth design in a few days (Amendment 9 null, voided screen, re-screen, v2, now v3). After a freeze there is no further primary change. A null is the result. The full history goes in the appendix as a forking paths disclosure.

## Arms, templates, and dose

Flash models: api-deepseek-4.1-flash and api-glm-5.3-flash, UCSD key, remaining cap.

Arms:
1. P short: uncompressed plan and flags. Primary baseline.
2. P comp 80: padded source, frozen 80 word naive prompt.
3. P comp 250: about 250 words.
4. P comp 1k: about 1,000 tokens, a practitioner budget.
5. P struct: frozen structured prompt that must keep call arguments and flags.
6. P verbatim: an 80 word summary plus a verbatim call argument block taken from the planner source. This is the mitigation.
7. P oracle: a faithful hand written style summary written only from the planner source, not from hidden world truth, with the "confirm against the account file" line removed.
8. S match: compute matched single agent, secondary only.

The executor in pipeline arms is plan following: it can book, delete, email, ask, hold, or flag, but it cannot re search or re list the world. That is the setting in which a missing identifier is a real decision, not a prompt to replan.

Six templates, 3 to 6 slots each, three in travel and three in ops, including one long multi constraint task in each domain where 1,000 tokens can be tight:

1. trip (travel, 5 slots): one way flight, hotel, nights, airline, email.
2. trip_return (travel, 6 slots): outbound and return flight, hotel, nights, airline, email.
3. trip_long (travel, 6 slots): the same plus a long binding constraint brief.
4. cleanup (ops, 4 slots): email, protected paths, retention, artifact patterns.
5. cleanup_keep (ops, 5 slots): adds a keep extension list.
6. cleanup_long (ops, 6 slots): adds a minimum age rule, more protected paths, and a long constraint brief.

Cells: C0, F3, and U1 (U1 is reported, not tested as a contrast). T1 is dropped to pay for templates and dose. Tasks are the six templates, not two seeds of the same two tasks. Analysis uses a mixed model with template as a random effect, or a template cluster bootstrap, plus the per template direction. An effect is claimed only if both flash models agree, at least the per model minimum detectable effect, with a 95 percent confidence interval that excludes 0 on each. Holm correction on the secondary family. Null rule: if the primary is null on both flash models, we write that and stop tuning.

Prompts are frozen in freeze_hashes_v3.json.

## Exploratory path in this pass (not confirmatory)

A v3 sanity, outcome agnostic: one C0 run per template per arm type on deepseek, to check harness, summaries, and grading.

Then up to two small outcome revealing screens on deepseek, n equals 2, still exploratory:
A. Handoff fabrication: a controlled slot drop on P short, versus complete P short, versus a verbatim arm whose summary is missing the slot but whose verbatim block still has it.
B. Dose curve on trip_long and cleanup_long at 80 words, 250 words, and 1,000 tokens, against P short.

The cap for this whole pass is 1.00 US dollars of new spend. Results choose the recommended confirmatory idea. They do not enter confirmatory tests.

## Confirmatory grid and price (only after reviewers and Bijin's written yes)

Conservative spend after this pass is $3.402. Remaining room to the $19.75 stop is about $16.35.

The exploratory screens did not support a fabricate and act primary. They did support a cleaner, still useful claim, which is now the recommended confirmatory question:

On a plan following executor, a missing required call argument causes asking or holding, not fabrication. A verbatim argument block restores completion without raising wrong action. Separately, on long multi constraint tasks, an 80 word cap drops slots and a 250 word budget does not.

Default confirmatory grid (C0, because the controlled slot drop is a clean C0 manipulation):

5 arms: P short, P short drop, P verbatim drop, P comp 80, P comp 250.
6 templates, 2 flash models, n equals 15 per template and arm.

Runs: 5 times 6 times 1 times 2 times 15 = 900, plus 5 percent retry about 945.
At about $0.007 to $0.009 per run, about $7 to $9 new, or about $10 to $12 cumulative. This fits the $19.75 stop with headroom.

If prices run hot: drop n to 12 (720 runs), or run deepseek first and add GLM only if deepseek is clear. Do not drop P short, the drop arm, the verbatim mitigation, or the 80 versus 250 contrast on the long templates. Do not drop the per model claim rule. The 1,000 token arm stays out of the default grid until the empty summary issue is fixed, then it can be added as a descriptive dose point if spend remains.

F3 and U1 are context rows from the exploratory set, or a later cheap add on, not part of the default primary family.

Per model minimum detectable effect: n equals 15 per template, 6 templates, so 90 runs per arm per model. The exploratory drop arm asked or held on 12 of 12 runs, and complete P short asked on 0 of 12, so the completion and ask contrasts are large. A per model MDE of about 15 percentage points at alpha 0.05 is conservative and should be easy to clear if the pattern holds. After template clustering we also require the same direction on at least 5 of 6 templates and on both models. Fabricate and act is pre registered as a bound, not as a positive effect we expect: if the upper 95 percent interval on the drop arm is below 15 percent on both models, we report that flash executors did not fabricate at a detectable rate. Effects that fail the template direction rule or the two model rule are declared null.

Promising result gate for a frontier arm (t185u), checked only on confirmatory flash data: both flash models show the ask or hold contrast (P short drop versus P short) and the verbatim completion recovery, same direction, at or above the per model MDE, with confidence intervals that exclude 0. Then, and only then, a frontier replication may be proposed on Bijin's own key, with a cap in his own words, to test whether a stronger model still refuses to fabricate.

## Kill criteria and honesty

1. Stop if cumulative conservative spend reaches 19.75 US dollars.
2. A harness bug (empty or JSON summaries, or a grader that cannot code a slot) is a fix and a rerun of sanity only, not a chance to retune the primary.
3. If the exploratory screens do not show a large fabricate and act effect, and the dose curve is flat and safe at 1,000 tokens, say so. Do not invent a third primary. The workshop paper can still report a careful null with the mitigation test and the dose curve, at a lower score.
4. Do not launch the confirmatory grid until the three groups review this v3 plan and Bijin gives a written yes.

## Honest scores (after the exploratory screens, still not confirmatory)

The 9 of 10 workshop path we aimed at was "executors invent dropped IDs, and a cheap fix stops them." That effect did not appear on deepseek in a 6 template, n equals 2, controlled drop screen (0 of 12 fabricate and act). We will not keep searching for that effect by changing the primary again.

The claim the data actually support, if it replicates on both flash models, is still a clean workshop paper, but not a 9:

Workshop: about 6 to 7, if the confirmatory grid reproduces ask or hold on dropped slots, 0 or near 0 fabrication, verbatim recovery of completion, and the 80 versus 250 word contrast on long tasks, with a clean figure and the forking paths appendix.

ICML main: about 3 to 4. The result is useful and careful, but it is not a surprising safety failure, and the novelty over a summarizer plus mitigation test is moderate.

A 9 of 10 workshop would have required the fabrication effect, a tight bound on it across templates, a mitigation that removes it, and ideally a frontier check that a stronger model still (or does not) fabricate. We did not get the first of those on flash. Stretching the score would be dishonest.

This plan is not approval to spend the confirmatory grid.
