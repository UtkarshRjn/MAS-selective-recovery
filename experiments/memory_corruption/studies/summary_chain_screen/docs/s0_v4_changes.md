# S0 v4 line-level changes from v3 (21 November 2026, Pacific Time)

v3 plan sha f6ee1bd9062da082f9a8a271ebd74948c6cddd3e806dd9416fd61e5d7a610995 is superseded. freeze_hashes_s0.json, freeze_hashes_s0_v2.json, and freeze_hashes_s0_v3.json were not modified. This re-freeze is freeze_hashes_s0_v4.json.

OpenAI researchers and Anthropic Researchers had approved v3. NeurIPS and ICML Reviewers blocked v3 because is_clean_false_alarm_pair used the neither-subset rule, so {truth, alt A} versus {truth, alt B} counted as a false alarm even though both channels name the truth. Anthropic had flagged the same edge case as non-blocking and asked for a count of FA pairs in which both sets contain the truth. Under the v4 rule that count is zero by construction.

No API calls. No MAIN_GO_s0.txt. 20 unit tests passed.

This freeze changes only the clean false-alarm decision, its tests, the plan and addendum lines that describe it, this diff note, and the gap check. Runner, cost-watch wrapper, source builder, summarizer, prompts, and v1-v3 hash and gap files are unchanged.

## Combined v4 rule

Restricted named sets are unchanged from v3. Let t_i = (truth in s_i).

- FA if both sets are non-empty and t1 != t2.
- FA if neither set contains the truth and s1 != s2.
- NOT FA if both channels name the truth, whatever the extra mentions.
- New secondary, not in the kill: truth-plus-competing-id rate, true when a channel names the truth together with the typo or an invented id. The any-id difference secondary is kept.
- Anthropic's requested count of FA pairs in which both sets contain the truth is zero by construction.

## Files not changed (v3 hashes still match)

- s0_source.py
- s0_summarize.py
- run_s0_nov21.py
- run_s0_cost_watch.py
- dry_run_s0_nov21.py
- dry_run_s0_nov21.txt
- summarizer_prompt.txt
- summarizer_prompt_s0_alt.txt
- s0_v2_changes.md
- s0_v3_changes.md
- check_s0_v3_false_alarm_gap.py
- s0_v3_fa_gap.txt
- freeze_hashes_s0.json
- freeze_hashes_s0_v2.json
- freeze_hashes_s0_v3.json

run_s0_nov21.py was left unchanged on purpose, so the analyze text printout still shows the v3 secondaries. The new truth-plus-competing-id rate and the zero fa_both_contain_truth count are in the false_alarm_rate record and therefore in the results JSON.

## New files

- s0_v4_changes.md: this note.
- freeze_hashes_s0_v4.json: the v4 freeze.
- check_s0_v4_false_alarm_gap.py and s0_v4_fa_gap.txt: zero-cost v2 / v3-subset / v4-truth comparison, including the new cases.

## plan_s0_nov21.md (v3 187 lines, v4 189 lines)

- Line 1: title now says "v4".
- Line 3: records that NeurIPS blocked v3 plan sha f6ee1bd9 on {truth, A} versus {truth, B}; OpenAI and Anthropic had approved v3; points to s0_v4_changes.md and freeze_hashes_s0_v4.json. v1, v2, and v3 hash files are left untouched.
- Line 5: paid-run sign-off now points at freeze_hashes_s0_v4.json.
- Line 120: BLOCKING FIX. Clean false alarm is decided on truth membership, not set inclusion. Truth-plus-competing-id is a new secondary. Anthropic's both-contain-truth FA count is zero by construction. Restricted sets, omissions, typo codebook conflict, and the 20% kill are unchanged.
- Line 136: notes the new unit tests and the v4 gap check.
- Files section: lists the v4 changes note, the v4 gap-check files, and freeze_hashes_s0_v4.json.

## addendum_s0_nov21.md (still 75 lines; blocking section rewritten)

- Title and intro now say v4. Credits the NeurIPS block of v3 and Anthropic's non-blocking note on the same edge case. OpenAI's v3 approval is recorded.
- The blocking-code-change section now states the truth-membership rule, the new secondary, and the new unit tests. The v1 omission fix and the v3 restricted set are kept.

## s0_grade.py (v3 705 lines, v4 790 lines)

- is_clean_false_alarm_pair (now around lines 191-213) no longer uses the neither-subset test. It uses t_i = (truth in the restricted set), with truth read from either channel's grade (code_text now stores truth and typo).
- New helpers: invented_named_ids, channel_has_truth, channel_truth_plus_competing, is_truth_plus_competing_pair.
- code_text also stores invented_named_ids and truth_plus_competing.
- pair_codes and false_alarm_rate add the truth_plus_competing_id secondary. false_alarm_rate also reports fa_both_contain_truth, which is zero by construction. The 20% kill still uses only is_clean_false_alarm_pair.

## test_s0_nov21.py (v3 351 lines, v4 395 lines)

Prior tests that still apply and still pass, unchanged in intent:

- test_omission_is_not_false_alarm (truth_only vs neither is not a false alarm; {truth} vs an invented id still is).
- test_trip_long_return_and_outbound_alt_are_not_false_alarms.
- test_hotel_ids_are_excluded.
- test_extra_catalog_flights_are_not_a_false_alarm.
- test_invented_id_versus_truth_is_a_false_alarm.
- test_typo_visible_conflict_uses_codebook_not_set_rule, including the assertion that disagreement versus truth_only is not a clean false alarm.

Hand-built codebook dicts in test_correlation_and_false_alarm, test_omission_is_not_false_alarm, and test_typo_visible_conflict_uses_codebook_not_set_rule now include a truth field (and typo, where needed) so the v4 rule can see t_i. Their expected outcomes did not change. The typo test's "not a clean FA" case now holds because both channels contain the truth, not because of the v3 subset rule. These are the only prior tests whose fixtures were updated.

New test_truth_membership_rule on a trip_long seed 80 cell:

- {T, A} vs {T, B} is not a false alarm.
- {T} vs {X_invented} is a false alarm.
- {A} vs {B} is a false alarm.
- {T} vs {T, X_invented} is not a false alarm, and the truth-plus-competing-id secondary is counted.

## Gap check

check_s0_v4_false_alarm_gap.py reruns the v3 comparisons and the new cases, under v2, the v3 subset rule, and the v4 truth rule.

Headline, S0 clean seeds 80-91, {truth, alt A} versus {truth, alt B}: v2 24/24 (1.00), v3-subset 24/24 (1.00), v4-truth 0/24 (0.00). That is the v3 artifact, on both trip and trip_long (12/12 to 0/12 each).

The other new cases, same seeds: {A} vs {B} is 24/24 under v3 and v4. {truth} vs invented is 24/24 under v3 and v4. {truth} vs {truth, invented} is 0/24 under v3 and v4, and the truth-plus-competing-id secondary is 24/24.

The v2 extra-mention artifact is unchanged: truth-only versus a list of every FL id in the source is still v2 24/24 (1.00) and 0/24 (0.00) under both v3 and v4. Truth versus truth plus two outbound alternatives, and truth versus truth plus the return id, stay at 0 under v3 and v4.

On saved confirmatory-v3 trip and trip_long texts, truth-only versus the saved handoff_summary stays 72/144 (0.50) under v2, v3, and v4 (real truth-versus-other-id conflicts). Truth-only versus a list of every FL id in the executor handoff stays 0/144 under v3 and v4. One related comparison does move, and that is intended: a typo-only (or neighbor-only) saved summary versus a list that includes the truth was not a v3 false alarm, because the singleton non-truth set was a subset of the list, but it is a v4 false alarm, because t1 != t2. That shows up as v3 0/144 versus v4 72/144 on handoff_summary versus all handoff ids, and as v3 2/16 versus v4 4/16 on the typo mocked dry-run pairs.

The runner's $3.00 hard stop and the wrapper's after-step-1 $2.80 projection stop are unchanged. Paid runs must still use run_s0_cost_watch.py, and only after all three groups approve the v4 hashes and MAIN_GO_s0.txt is written.
