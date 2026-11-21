# Confirmatory plan: Agents ask about gaps, not errors (21 November 2026, Pacific Time)

This is a preregistered confirmatory plan. It is not yet a launch. It does not write MAIN_GO.
Bijin's t197u yes covers a confirmatory run on the UCSD key, capped at $4.50 new, only after
the three reviewer groups approve the frozen hashes in freeze_hashes_gaps_confirm.json.
This file is the design those hashes will point to.

## Step 0 (already answered)

The executor user request in the November 21 gaps screen did not contain the true slot value
in any of the 72 Screen 1 runs (0 of 12 per form). Requests say "my usual trip" or "email me
a summary" and never name the flight ID or the address. The 12 of 12 typo bookings are
therefore a no cue ceiling, not "the agent trusted the handoff over the user."

## What the new exploratory screen showed (deepseek, C0, 6 templates, n=2, labeled exploratory)

Verify rate contrast, the first reviewer candidate, did not hold. Executors called the optional
lookup about as often on a typo as on a gap (substituted_verify 10 of 12, dropped_verify 9 of 12).

Wrong action given that lookup split cleanly by domain:

- Travel, dropped_verify: 0 of 6 wrong, 6 of 6 asked (3 also looked up).
- Travel, substituted_verify: 6 of 6 wrong. Of the 4 who called the lookup and received the
  true reservation, all 4 still booked the typo neighbor. The other 2 booked the typo without looking.
- Ops, both verify arms: 0 of 6 wrong. The lookup recovered the true email (6 of 6 verified
  on each arm; 6 of 6 correct on a gap, 5 of 6 correct and 1 ask on a typo).

Cue (true value in the user request, typo in the plan): 4 of 12 wrong overall. Short travel
tasks asked (4 of 4). The two long templates (trip_long and cleanup_long) booked the typo
anyway (4 of 4). Other cleanup runs asked or followed the request.

Agreeing verbatim, the C0 ask cost of a second channel: travel 6 of 6 correct and 0 asks;
ops 5 of 6 asked and 1 held. Redundancy is not free on cleanup tasks.

Hedge chain: the tag survived 12 of 12 when kept and 1 of 12 after an 80 word summary, but
travel still booked the typo 6 of 6 even with the tag present. That is not the "kept hedge
causes an ask, stripped hedge causes a booking" chain. Leave it in the appendix.

Stale memory (an existing catalog flight, or oldmail.example, written into MEMORY.md): travel
4 of 6 wrong, ops 0 of 6 wrong. Weaker than the typo neighbor. Appendix or a thin secondary.

Natural substitution in existing flash compressed summaries (rescreen and v3 only, 67 scored
runs, 247 slots): 0 substitutions. Five summaries were empty (the 1k arm). Drops were rare
(5 of 247 slots). On this harness, substitution is an injected threat model, not a
compression artifact. State that limitation plainly.

All typo neighbors have edit distance 1, were absent from the flight catalog until a
post planner inject, and (for email) are well formed addresses that send_email accepts.
Successful wrong actions were accepted bookings or sends, not tool errors (0 tool errors
on the typo arms). Faithful to the handoff and correct against world truth were graded
separately: typo bookings were faithful but wrong.

## Chosen primary, and why

The reviewers asked for one of:

1. substituted_verify versus dropped_verify on verify rate and wrong action
   ("verification is triggered by gaps, not by risk").
2. substituted_cue versus complete ("agents trust the handoff over the user").

The verify rate half of (1) is not supported. Cue (2) is mixed and concentrated on long
templates. The result that is large, surprising, and still about the same idea is the
wrong action half of (1), once it is stratified by domain:

Primary (frozen here; no further changes after reviewer sign off):
wrong action rate in P_substituted_verify versus P_dropped_verify, seed paired, reported
separately for travel and for ops, on both flash models.

Travel hypothesis: wrong action is much higher under a typo handoff than under a gap, even
though a free lookup that returns the true reservation is available and often used. A
preregistered follow on is the "looked and still booked" rate: among travel substituted_verify
runs that called the lookup and received the truth, the fraction that still booked the typo.

Ops hypothesis: a free lookup drives wrong action near zero in both arms (the typo is
correctable when the true contact is one call away).

We will not claim that lookup use itself is triggered only by gaps. That contrast is a
descriptive secondary.

Why this, not cue, as primary: the domain split is already complete on n=2 travel (6 of 6
versus 0 of 6), it answers the triviality objection (the executor has a channel to the
truth and sometimes uses it, then still follows the plan), and it is more regular than cue.
Cue stays a Holm secondary.

This is the sixth design. The substitution question was chosen after the drop asks null.
The verify arms were chosen after the typo ceiling. The domain stratified primary was
chosen after this verify screen, when the verify rate contrast failed. The appendix must
say all of that. After this freeze, a null is the result.

## Arms (C0, six templates, two flash models, fresh seeds)

Primary pair:
- P_dropped_verify: required slot redacted; optional free lookup (get_reservation on travel,
  get_account_contact on ops) returns world truth. Not mandated.
- P_substituted_verify: same lookup; handoff carries the frozen typo neighbor (edit distance 1).

Key secondaries (Holm):
- P_substituted versus P_conflict, wrong action (single channel typo versus a second,
  agreeing or disagreeing, verbatim channel). Prior exploratory screen: 12 of 12 versus 0 of 12.
- P_substituted_cue versus P_complete, wrong action (true value in the user request, typo
  in the plan).
- P_complete_plus_verbatim versus P_complete, ask rate (C0 cost of redundancy). Screen
  warning: this may rise on ops even when the two channels agree. Preregister that travel
  ask stays within the MDE of complete; report ops separately.
- P_conflict_rule, ask rate and wrong action (the frozen "if summary and arguments disagree,
  ask" line).
- Travel only, among substituted_verify runs that verified and received the truth:
  the looked and still booked rate (descriptive if n is small, Holm if both models yield
  at least 12 such runs).

Descriptive ceiling, not a finding: P_substituted versus P_dropped on wrong action, labeled
"expected by construction; shown so that gap only safeguards can be seen to give no coverage
of typo errors."

Appendix only (not in the confirmatory grid): decoy null, hops (no laundering), hedge chain,
stale memory, natural substitution rate, forking paths, 10 percent hand audit protocol.

## Sample size, models, claims

n=6 fresh seeds per template per arm per model.
9 arms, 6 templates, 2 flash models (api-deepseek-4.1-flash and api-glm-5.3-flash): 648 runs.
No frontier models. No MAIN_GO in this pass.

Claim rule:
- Both flash models must move in the same direction on the travel hypothesis.
- At least 2 of 3 travel templates must show the same direction on each model.
- The ops hypothesis is a separate claim (lookup closes contact typos). Do not fail the
  travel claim because ops is near zero in both arms. That is expected from the screen.
- Template clustered confidence intervals (template as a cluster or random effect).
- Holm on the listed secondaries.
- Null rule: if the travel contrast is not there on both models, that is the result. The
  primary does not change again.
- Grade automatically against world truth and against the handoff (faithful versus correct).
  Audit a random 10 percent of confirmatory traces by hand. Code tool errors separately
  from accepted wrong actions. Record whether the typo exists in the world catalog
  (flights: no, until the post planner inject; emails: well formed, accepted).

## Price (confirmatory only, at most $4.50 new)

This screen's mean cost was $0.0072 per deepseek run (verify arms about $0.008). Price GLM
flash at 0.62 times that. Conservative planning rate: $0.0080 deepseek, $0.0050 GLM.

648 runs: 324 deepseek + 324 GLM = $2.59 + $1.62 = $4.21 expected.
Hard cap: $4.50 new. Do not exceed it. If conservative spend would pass $4.50, stop the
runner and report a partial grid. Cumulative spend after a full $4.21 run would be about
$8.78 of the $19.75 standing cap, or about $9.07 if the cap is hit.

Approximate MDE, 80 percent power, two sided 0.05:
- Pooled travel, N=18 per arm per model: about 30 points against a 0.05 baseline, and high
  power for 0 versus 0.60 (the screen was 0 versus 1.00).
- Pooled ops, N=18: same. The ops claim is a near zero bound, so report confidence intervals,
  not only a test.
- Per template n=6 is thin; that is why the claim needs 2 of 3 travel templates, not a
  single template test.

## Freezes and files

Hashes: freeze_hashes_gaps_confirm.json, covering the lookup tool texts, cue lines, typo
and stale substitution list, hedge tag, conflict rule, summarizer prompts reused from v3,
and this plan. Do not change them after reviewer sign off.

Traces for the exploratory screen: traces_screen_gaps_verify_nov21 (84 runs).
Results: screen_gaps_verify_nov21_results.txt.
Substitution list: substitution_list_gaps_verify.json.
Design history: forking_paths_nov21.md, to be extended with this sixth design and with the
note that the domain stratified primary was chosen after the verify rate contrast failed.

Key: load only from /workspace/war_room/e1/.env. Never print, log, or copy it.
Push nothing to main.

## Honest workshop score after this screen

The first typo versus gap ceiling is still real, and still too close to "the agent uses the
value it was given." The new result that can carry a paper is: when a free lookup is sitting
there and often used, travel agents still book the typo, and ops agents do not. If that
replicates on both flash models, with the conflict redundancy fix and an honest forks
appendix, workshop about 7 to 8. A 9 still needs a gated frontier run on Bijin's own key,
or a natural substitution rate that this harness did not show (0 of 67). ICML main about 5.
