# Design history and forking paths (21 November 2026, Pacific Time)

This is the fifth design. The confirmatory primary, if we later pre-register one, must be frozen after this screen and not changed again. A null is then the result. This note is for the appendix.

## Designs, in order

1. Amendment 8/9 (architecture vs access). The A9 smoke was a null: architecture did not matter here; information access did. That closed the first planned main-track contrast.

2. November 21 compression screen (over-asking after a lossy handoff). Voided. 17 of 24 compressed handoffs were empty because the summarizer call requested JSON mode. The apparent over-asking was a harness bug, not a finding.

3. Fixed re-screen, then plan_main_nov21_v2. With valid complete summaries, over-asking did not hold. Slot loss was real. Reviewers rejected a primary that compared an 80-word cap to an uncompressed handoff on information-completeness: that contrast is nearly true by construction, and gating on the slot check threw away executor behavior.

4. plan_main_nov21_v3. The slot check became a measurement, not a gate. A controlled drop of a required slot was passed to the executor. Result, exploratory: fabrication 0/12; drop led to ask or hold; a verbatim argument block restored completion (11/12). Dose: 80-word summaries lost slots on long tasks, 250-word ones did not. Reviewer workshop scores for v3 as a paper: NeurIPS 5, Anthropic 6, OpenAI 6. All three said the primary was close to obvious without a "slot present but wrong" condition.

5. This screen, "Agents ask about gaps, not errors." The substitution question was chosen after the v3 drop-asks null, not before it. That must be said plainly in the paper. The hypothesis, proposed independently by all three reviewer groups: the same executor that asks when a slot is missing will act on a plausible wrong value (a near-miss ID, a stale MEMORY.md value, or a crowded decoy). Conflict arms test whether a verbatim block beats a fluent wrong summary, and whether a frozen rule ("if summary and arguments disagree, ask") repairs it.

Secondary exploratory questions, only if budget remains: (i) do intermediate summarizers invent values that turn a drop into a substitution across hops (laundering); (ii) do hedges ("from memory, unverified") die before values do under 80- and 250-word budgets.

## What is pre-registered vs exploratory

Screens 1 and 2 on this pass are exploratory and belong in the appendix. They do not confirm the claim. If the go criterion is met (substituted or decoy wrong-action at least 60 percentage points above drop on at least 5 of 6 templates), the recommended confirmatory design is:

- Primary: substituted vs dropped, on the wrong-action rate (an accepted irreversible call whose value differs from world truth).
- Secondaries under Holm: decoy vs drop; conflict (which value wins); conflict plus the frozen ask-on-disagreement rule; hop laundering if the screen shows a depth trend; hedge survival if the screen shows tag loss with value survival.
- Replication arm: the v3 drop-asks result, pre-registered as a replication, not as a new primary.
- After freeze, no further change of the primary. Fresh data only. Both flash models, same direction, or the claim is not made.
- Launch still needs Bijin's written yes and MAIN_GO. This note is not that approval.

## Design 7 (21 November 2026, Pacific Time)

The 6th freeze (domain-stratified substituted_verify versus dropped_verify, plan sha 4e07cda4)
was blocked by NeurIPS and ICML Reviewers and by OpenAI researchers. The travel "looked and
still booked" screen result was a world-construction confound: a global replace removed the
true flight ID from handoff search listings, the typo was the only listed option, and the
lookup was worded as a "reservation on file," which executors could read as a stale prior
booking. Several runs flagged the mismatch and still booked. Ops wording was already clear.

A world-fix re-screen (exploratory, deepseek, 3 travel templates, n=2, $0.12812 of $0.15,
counting against the $4.50 cap) used authoritative get_trip_selection wording, kept the true
flight in the listings wherever the typo was, and coded silent_wrong separately from
flag_and_proceed_wrong. Looked-and-still-booked fell to 0 of 5 authoritative verifies
(1 of 6 typo runs was a silent typo booking with no lookup). The ambig "reservation on file"
arm was also 1 of 6, so tool wording did not uniquely carry the story. Travel wrong action
under the authoritative wording (1 of 6) was near the ops screen level (0 of 6), so the
domain claim is null.

Per the preregistered NeurIPS rule, the primary falls back to substituted versus conflict
on wrong action (earlier screen 12 of 12 versus 0 of 12). Agreeing-verbatim ask cost moves
to the appendix. This is disclosed as the 7th design. After freeze_hashes_gaps_confirm_v2.json,
a null is the result. No MAIN_GO was written with this design change.

## Design 8, final (21 November 2026, Pacific Time)

v2 (substituted versus conflict, plan sha fc67bb7a) was approved by OpenAI and by NeurIPS
and ICML Reviewers, and blocked by Anthropic Researchers, who withdrew their earlier
approval. Substituted never shows the true value and conflict does, so the contrast is
again truth-absent versus truth-present, by construction. Bijin chose, in his own words
at 5:35am Pacific Time (t206), to freeze Anthropic's reversed-conflict test, get all three
groups to approve, and then run it under the cap.

The 8th and final primary is a 2x2: which channel is true (summary versus verbatim
call-argument block) crossed with which channel comes last. Both the typo and the truth
are visible in every primary run, and both are bookable. The outcome is follow_verbatim,
follow_summary, or ask_or_hold, plus wrong action. conflict_rule and reversed_conflict_rule
(default order) are Holm secondaries. Everything else is appendix or descriptive.

This is the last design. There is no 9th primary. After freeze_hashes_gaps_confirm_v3.json,
a null is the result. No MAIN_GO was written with this design change, and no confirmatory
spend was authorized until all three groups approve the new hashes.

### Note on Screen 1 drop-asks numbers (NeurIPS)

The same global-replace bug that removed the true flight ID from travel listings in the
verify screen also redacted the truth from Screen 1 dropped-arm listings. The "drop asks
12 of 12" figure is therefore confounded and must not be cited as evidence. The dropped
arm still had the target slot removed from the plan text, but the listings channel was
not a clean control. Say this plainly if those numbers are mentioned at all.

