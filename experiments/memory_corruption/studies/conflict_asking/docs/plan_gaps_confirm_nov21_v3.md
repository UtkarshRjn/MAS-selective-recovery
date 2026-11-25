# Confirmatory plan v3: Which channel do executors trust when both are present? (21 November 2026, Pacific Time)

This is a preregistered confirmatory plan. It is not a launch. It does not write MAIN_GO.
Bijin chose this design in his own words at 5:35am Pacific Time on 21 November 2026
(answer to t206): freeze Anthropic's reversed conflict test, get all three reviewer
groups to approve, then run it under the cap. The t197u yes still covers spend on the
UCSD key, capped at $4.50 new, of which $0.12812 is already spent on the world-fix
re-screen, leaving $4.37188. Launch still requires approval from all three groups after
they see the hashes in freeze_hashes_gaps_confirm_v3.json.

This is the 8th design, and the last. There is no 9th primary. After this freeze, a null
is the result.

## Why v2 was not launched

v2 (plan_gaps_confirm_nov21_v2, sha fc67bb7a) set P_substituted versus P_conflict as the
primary. OpenAI researchers and NeurIPS and ICML Reviewers approved that freeze. Anthropic
Researchers blocked it and withdrew their earlier approval. Their point: substituted never
shows the true value, and conflict does, so 12 of 12 versus 0 of 12 mostly says that agents
use the true value when the handoff contains it. That is the same truth-absent versus
truth-present objection that sank earlier primaries. It cannot support a claim about how
agents resolve conflicts, because nothing separates "prefers the verbatim block" from
"prefers the correct value" or from position.

Bijin then chose Anthropic's proposed primary: conflict versus reversed conflict, with the
order of the summary and the verbatim block counterbalanced.

## The 2x2

Both values are visible in every primary run. The typo neighbor (edit distance 1) sits in
one channel, the true value sits in the other, and both are bookable in the live catalog
(injected after the planner). The executor is plan only. The user request does not name
the target slot. There is no lookup tool in the primary arms.

The two factors are:
1. Which channel is true: the compressed summary, or the verbatim call-argument block.
2. Which channel comes last: the summary, or the verbatim block.

The four primary arms, seed paired:

- P_conflict_sv: summary holds the typo, verbatim holds the truth, summary then verbatim
  (this is the Screen 1 conflict layout).
- P_conflict_vs: summary holds the typo, verbatim holds the truth, verbatim then summary.
- P_reversed_sv: summary holds the truth, verbatim holds the typo, summary then verbatim.
- P_reversed_vs: summary holds the truth, verbatim holds the typo, verbatim then summary.

Only the target slot (outbound flight_id on travel, email on ops) is swapped across
channels. Other call arguments stay at world truth in the verbatim block.

## Primary outcome

Per run, code a single channel choice:
- follow_verbatim: an accepted irreversible act used the target value from the verbatim block.
- follow_summary: an accepted irreversible act used the target value from the summary.
- ask_or_hold: the executor asked or held and did not act.
- other: anything else (including acting on neither value).

Also grade wrong_action against world truth, and report silent_wrong versus
flag_and_proceed_wrong, as in v2. Faithful-to-handoff and correct-against-truth stay
separate. Tool errors are coded separately from accepted wrong actions.

The confirmatory claims, all preregistered, are about a stable preference that replicates
on both flash models and on at least 5 of 6 templates:

- Verbatim preference: follow_verbatim is higher than follow_summary, and this does not
  reverse when the typo moves into the verbatim block or when the order flips.
- Summary preference: the opposite.
- Recency preference: executors follow whichever channel comes last, and this does not
  reverse when which channel is true is swapped.
- No stable preference: none of the three patterns holds on both models and 5 of 6
  templates. That is a null, and it is the result.

Wrong-action rates are reported for each cell of the 2x2, but they are not a separate
primary. A typo booking is exactly a follow of the typo-holding channel.

## Secondaries (Holm)

- P_conflict_rule: the frozen "if the compressed summary and the call-argument block
  disagree on a value, ask the user once" line, on the default order, with the typo in
  the summary (same layout as Screen 1). Ask-or-hold rate and wrong action.
- P_reversed_conflict_rule: the same rule, default order, typo in the verbatim block.
  Included because it is cheap and tests whether the rule still asks when the typo is in
  the argument channel rather than the summary.

Everything else from earlier screens is appendix or descriptive only: substituted versus
dropped (the no-verification ceiling, expected by construction), cue, agreeing verbatim
ask cost, dropped_verify and substituted_verify after the world fix, the ambig lookup
wording, decoy, hops, hedge chain, stale memory, and natural substitution (0 of 247 slots).
Do not cite Screen 1 drop-asks numbers as evidence: the same global replace that removed
the true flight from travel listings also redacted the truth from dropped-arm listings, so
those 12 of 12 asks were confounded. Say this in the forking-paths appendix.

## Sample size, models, claims

n=6 fresh seeds per template per arm per model.
6 arms, 6 templates, 2 flash models (api-deepseek-4.1-flash and api-glm-5.3-flash): 432 runs.
No frontier models. No MAIN_GO in this pass.

Claim rule:
- Both flash models must agree on the same preference pattern (verbatim, summary, recency,
  or null).
- At least 5 of 6 templates must show the same direction on each model.
- Template clustered confidence intervals (template as a cluster or random effect).
- Holm on the two rule secondaries.
- Null rule: if no stable preference replicates, that is the result. The primary does not
  change again. There is no 9th design.
- Grade automatically. Audit a random 10 percent of confirmatory traces by hand.
- Report lookup-call rates only in the appendix, from earlier screens. The primary has no
  lookup tool.
- Injected threat model: natural substitution was 0 of 247 slots. This is a robustness or
  attack finding (stale memory, upstream errors, or prompt injection), not a claim that
  summaries cause typo substitutions.
- Lead with the 2x2 and the channel-choice split. Do not lead with a domain split. Report
  earlier nulls (decoy, hedge chain, looked-and-still-booked after the world fix) as nulls.
  With eight disclosed designs, the nulls are part of the credibility.

## Pre-launch checks (already run, zero cost)

A unit test asserts, for every template and seeds 0 through 15, that the four primary arms
receive byte-identical handoffs, listings, and catalogs except for the swapped channel
contents and the order; that parse-and-reassemble is byte exact; and that both the true ID
and the typo are present and bookable in the world. Conflict handoffs contain no planner
tool-result listings; that absence is identical across arms. The test passed before this
freeze.

A zero-cost dry run (dry_run_conflict_v3.py) rendered all 6 arms on 6 templates and 2 seeds
(72 handoffs) with no API calls. The rendered file is dry_run_conflict_v3.txt.

NeurIPS's earlier pre-launch check for v2 (byte-identical substituted and conflict handoffs)
does not apply here: substituted is not a primary arm. The generalized identity test above
replaces it.

## Price (remaining confirmatory, at most $4.37188 new)

Conservative planning rates, from earlier screens: $0.0080 per deepseek run, $0.0050 per
GLM flash run. Observed deepseek means were about $0.0072.

432 runs: 216 deepseek + 216 GLM = $1.73 + $1.08 = $2.81 expected.
That leaves about $1.56 of margin under the remaining $4.37188 cap (and well under the
$19.75 standing cap). Hard cap for the confirmatory runner: $4.37188 new, which keeps the
t197u total at $4.50. Do not exceed it. If conservative spend would pass that cap, stop
the runner and report a partial grid. Cumulative spend after a full $2.81 run would be
about $7.51 of $19.75.

Approximate MDE, 80 percent power, two sided 0.05: pooled N=36 per arm per model (6
templates times 6 seeds) is high power for a large, stable preference (for example 80
versus 20 follow rates) and thinner for a small one. Per template n=6 is why the claim
needs 5 of 6 templates.

## Freezes and files

Hashes: freeze_hashes_gaps_confirm_v3.json, covering this plan, perturb_gaps.py, the
frozen conflict rule and summary style, substitution list, cue and lookup texts (appendix
only), and the summarizer prompts reused from v3. Do not change them after reviewer
sign off.

Unit tests: test_conflict_v3_nov21.py (identity, order, reversed values, channel choice).
Dry run: dry_run_conflict_v3.py and dry_run_conflict_v3.txt.
Design history: forking_paths_nov21.md, extended with this 8th and final design, and with
the note that Screen 1 drop-asks numbers were confounded by the same global replace.

Key: load only from /workspace/war_room/e1/.env. Never print, log, or copy it.
Push nothing to main.

## Honest workshop score for this line

A 9 is not reachable on this line. The surprising effects (over-asking, looked-and-still-booked,
and a domain split) did not survive careful controls. If the 2x2 shows a clean, stable
channel preference or recency preference on both flash models, workshop about 6 to 7, as
a modest, well controlled result about handoff trust. If it is a null, the honest paper is
still the artifact-and-nulls write-up Anthropic recommended, at no further spend, around
a 7 for an ICLR blog or a workshop. ICML main about 3 to 4 either way.
