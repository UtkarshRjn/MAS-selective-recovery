# S0 v2 line-level changes from v1 (21 November 2026, Pacific Time)

v1 plan sha 6abdaafd8cc47efbe28cb63866586a3ee0abde14febbc2eb2a88f2fa18a289cb is superseded. freeze_hashes_s0.json was not modified. This re-freeze is freeze_hashes_s0_v2.json.

No API calls. No MAIN_GO_s0.txt. 14 unit tests passed, including the new omission test.

## Files not changed (v1 hashes still match)

- s0_source.py
- s0_summarize.py
- dry_run_s0_nov21.py
- dry_run_s0_nov21.txt
- summarizer_prompt.txt
- summarizer_prompt_s0_alt.txt
- freeze_hashes_s0.json

## New files

- addendum_s0_nov21.md: analysis-only rules from all three groups, plus the Eckhardt and Lee 1985 citation.
- run_s0_cost_watch.py: paid-run wrapper. After composition depth 1, if projected total (spend/calls * 592) is above $2.80, stop and report; do not start later stages, including depth 4 (not a silent drop).
- s0_v2_changes.md: this note.
- freeze_hashes_s0_v2.json: the v2 freeze.

## plan_s0_nov21.md (v1 176 lines, v2 184 lines)

- Line 1: title now says "v2" and "re-freeze".
- Line 3: records that OpenAI blocked v1 sha 6abdaafd on the clean false-alarm definition; points to the addendum, the changes note, and freeze_hashes_s0_v2.json. v1 hash file is left untouched.
- Line 50: "independent jobs", not "independent samples"; hop 1 is expected to be shared at temperature 0; s is a rate over worlds.
- Line 52: typo_survival is transmission of the recommended value, not error detection.
- Line 54: same transmission wording in the outcome list.
- Lines 77-80 (inserted after v1 line 76): hop-1 vs later-hop asymmetry; descriptive hop-wise conditional transmission; tie rule; ceiling-at-depth-1 wording; n=24 is a screen and a pass means "not refuted".
- Line 87 (was v1 83): "hops interact" is not used when s1 is 1.0; report interval widths; a screen pass is not confirmation of a law.
- Line 114 (was v1 110): report per-template excess; Eckhardt and Lee 1985; S0 cannot separate item difficulty from channel coupling; a follow-up needs at least 3 samples per item per channel.
- Line 120 (was v1 116): BLOCKING FIX. Clean false alarm is both valid, each names a target-slot flight id, and those id sets differ. truth_only vs neither is not a false alarm. State-difference and omission rates are secondaries and do not drive the 20% kill.
- Line 136 (was v1 132): notes the new unit test; dry-run files were not changed.
- Line 144 (was v1 140): paid run must use run_s0_cost_watch.py; after step 1, stop if the projected total is above $2.80; never drop depth 4 silently; report the real final spend.
- Lines 166-180 (files section, was v1 162-172): lists the addendum, changes note, wrapper, and v2 hash file.

## s0_grade.py (v1 451 lines, v2 585 lines)

- Lines 44-73 (inserted at the old code_text entry): FLIGHT_ID_RE, named_target_ids, named_id_set, names_a_target_id, is_clean_false_alarm_pair.
- Line 81 and line 105: code_text now stores named_ids from flight ids in a valid summary.
- Lines 341-363 (was v1 309): law_result now returns ceiling_at_depth_1, simple_hit_depth4, and family_claim (simple_s_k, saturating, not_discriminated, neither_hits, or ceiling_at_depth_1).
- Lines 384-401 (was v1 330-347): pair_codes.visible_conflict now uses is_clean_false_alarm_pair. The old last clause that treated state "neither" as a visible conflict is gone. Omission flags are reported separately (c1_omission, c2_omission, omission_either).
- Lines 447-460 and 473: correlation_from_pairs also returns per-template excess.
- Lines 477-502 (was v1 407-420): false_alarm_rate uses is_clean_false_alarm_pair for the primary rate and the 20% kill. Adds secondaries: state_diff, omission_c1, omission_c2, omission_either.
- Lines 537-585 (new): hopwise_conditional and law_claim_across_models.

## test_s0_nov21.py (v1 214 lines, v2 239 lines)

- Lines 130-133: the all-agree clean case now supplies named_ids.
- Lines 138-164: new test_omission_is_not_false_alarm. Replaces the v1 block (lines 136-139) that treated truth_only vs neither as a kill. The new test asserts that omission is not a false alarm and not a visible_conflict, that state_diff and omission_c2 are still reported, that two different named ids do count as a false alarm, and that code_text fills named_ids.

## run_s0_nov21.py (v1 592 lines, v2 636 lines)

- Line 3: paid runs must go through run_s0_cost_watch.py.
- Lines 383-403: print family_claim and ceiling_at_depth_1; compute and print descriptive hop-wise conditional transmission from depth-4 logs.
- Lines 407-430: composition kill uses the tie rule and does not label a ceiling-at-depth-1 miss as "hops interact".
- Lines 449-466: print per-template excess; false-alarm output includes state_diff and omission rates.
- Lines 482-483: payload includes ceiling_at_depth_1_both and family_claims.

The runner's $3.00 hard stop is unchanged. The after-step-1 $2.80 projection stop lives only in run_s0_cost_watch.py, which is why paid runs must use the wrapper.
