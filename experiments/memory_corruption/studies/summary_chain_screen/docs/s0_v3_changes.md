# S0 v3 line-level changes from v2 (21 November 2026, Pacific Time)

v2 plan sha fa249536ef0dab099e8f64eacda87ba924b1130f7b528382ce2fe1cd9676eda3 is superseded. freeze_hashes_s0.json and freeze_hashes_s0_v2.json were not modified. This re-freeze is freeze_hashes_s0_v3.json.

This v3 freeze folds two independent blocks of v2, which named the same grader artifact:

- OpenAI researchers: named_target_ids / is_clean_false_alarm_pair counted every FL id. On trip_long, a correct summary that also kept the return flight, or that also named an outbound listing alternative, versus a verbatim {truth} channel counted as a false alarm and a visible conflict.
- Anthropic Researchers: the same all-FL-id match, especially likely on ds_p1 versus ds_p2, because the p2 prompt asks for an identifier list first. That could have crossed the 20% clean kill on a grading artifact.

No API calls. No MAIN_GO_s0.txt. 19 unit tests passed, including the kept v2 omission test and the new trip_long / invented-id / typo-codebook tests.

## Combined v3 rule (credits both groups)

1. Restricted named set = outbound target-slot candidates (truth, typo, and world['flights'] ids, excluding return_flights and hotels) PLUS any invented FL id that is in neither the source nor the catalog. The same restricted set is used for omission flags. (Anthropic's invented-id clause, plus OpenAI's outbound-only candidate list. Return ids are excluded even when they also appear in world['flights'].)
2. Clean false alarm = both valid, both restricted sets non-empty, and neither is a subset of the other (OpenAI's subset rule). An invented id versus the truth therefore counts (Anthropic). An extra catalog mention does not.
3. The all-ids difference is kept as a secondary, labeled "any-id difference", and never drives the 20% kill (both groups).
4. Typo-run visible_conflict is taken from the codebook only: true when the two channels together contain both typo and truth, or when one channel is in the disagreement state. Do not use the generic set rule on typo runs (OpenAI).
5. Unit tests: OpenAI's three trip_long cases ({truth} vs {truth, return_id} is not a false alarm; {truth} vs {truth, outbound_alt} is not; {truth} vs {outbound_alt} is) plus hotel exclusion; Anthropic's two extra tests (both name the truth and one also lists two other catalog flights = not a false alarm; one names the truth and the other an invented FL id = is a false alarm). The v2 omission test still passes.
6. Zero-cost check (non-blocking, Anthropic): check_s0_v3_false_alarm_gap.py ran the old and new definitions on mocked dry-run summaries, on synthetic p2-style list-all channels for S0 clean seeds 80-91, and on saved confirmatory-v3 trip and trip_long summary texts. Results are in s0_v3_fa_gap.txt. The gap is large. On S0 clean seeds, truth-only versus a list of every FL id in the source is 24/24 (rate 1.00) under v2 and 0/24 (rate 0.00) under v3. The same 1.00-to-0.00 gap holds for truth versus truth plus two outbound alternatives, and for truth versus truth plus the return id on trip_long. On confirmatory-v3 trip and trip_long texts, truth-only versus the saved handoff_summary is unchanged at 72/144 (rate 0.50) under both rules (those are real truth-versus-other-id conflicts). Truth-only versus a list of every FL id in the executor handoff is 144/144 under v2 and 0/144 under v3. The fix mattered.

## Files not changed (v2 hashes still match)

- s0_source.py
- run_s0_cost_watch.py
- dry_run_s0_nov21.txt
- summarizer_prompt.txt
- summarizer_prompt_s0_alt.txt
- s0_v2_changes.md
- freeze_hashes_s0.json
- freeze_hashes_s0_v2.json

## New files

- s0_v3_changes.md: this note.
- freeze_hashes_s0_v3.json: the v3 freeze.
- check_s0_v3_false_alarm_gap.py and s0_v3_fa_gap.txt: zero-cost old-versus-new comparison.

## plan_s0_nov21.md (v2 184 lines, v3 187 lines)

- Line 1: title now says "v3".
- Line 3: records that OpenAI and Anthropic independently blocked v2 plan sha fa249536 on the all-FL-id artifact; NeurIPS and ICML Reviewers had approved v2; points to s0_v3_changes.md and freeze_hashes_s0_v3.json. v1 and v2 hash files are left untouched.
- Line 5: paid-run sign-off now points at freeze_hashes_s0_v3.json.
- Line 120: BLOCKING FIX, crediting both groups. Restricted outbound set plus invented ids; subset rule; any-id difference is a secondary; typo visible_conflict from the codebook only; omissions still are not false alarms.
- Line 136: notes the new unit tests and the zero-cost gap check. dry_run_s0_nov21.txt was not rewritten.
- Files section: lists the v3 changes note, the gap-check files, and freeze_hashes_s0_v3.json.

## addendum_s0_nov21.md (v2 69 lines, v3 75 lines)

- Title and intro now say v3. Credits Anthropic's v2 block and OpenAI's v2 block, and notes that NeurIPS approved v2.
- The blocking-code-change section now states the restricted set, the subset rule, invented ids, any-id difference, typo codebook conflict, and the new unit tests. The v1 omission fix is kept.

## s0_grade.py (v2 585 lines, v3 705 lines)

- named_target_ids still extracts every FL id, but that set is now stored as all_named_ids and is only used for the any-id-difference secondary.
- New helpers: outbound_candidate_ids (world['flights'] minus return_flights, plus truth and typo; hotels excluded), catalog_ids, restrict_named_ids (outbound intersect named, union invented ids that are in neither the source nor the catalog).
- is_clean_false_alarm_pair now requires both restricted sets non-empty and neither a subset of the other (was s1 != s2 on all FL ids).
- New is_any_id_difference and typo_visible_conflict.
- code_text accepts cell and source, stores named_ids (restricted) and all_named_ids (every FL id).
- pair_codes takes condition='clean' or 'typo'. Typo visible_conflict uses the codebook only.
- false_alarm_rate adds the any_id_difference secondary. The 20% kill still uses only is_clean_false_alarm_pair.

## s0_summarize.py (still 206 lines; call sites changed)

- run_hops now passes cell=cell and source=source (the original source, not the previous hop text) into code_text, so restricted ids and invented-id checks are computed on every hop.

## run_s0_nov21.py (v2 636 lines, v3 644 lines)

- Fallback code_text calls pass cell and source.
- Verbatim re-grade in _channel_index rebuilds the cell so return flights can be excluded.
- analyze prints the any-id difference secondary next to the clean false-alarm rate.

## dry_run_s0_nov21.py (still 98 lines; one call site changed)

- The verbatim grade now passes cell and source. dry_run_s0_nov21.txt was not rewritten.

## test_s0_nov21.py (v2 239 lines, v3 351 lines)

- test_omission_is_not_false_alarm is unchanged in intent and still passes.
- New TestRestrictedFalseAlarm: OpenAI's trip_long seed 80 cases, hotel exclusion, Anthropic's two extra catalog / invented-id tests, and typo codebook conflict versus the generic set rule.

The runner's $3.00 hard stop and the wrapper's after-step-1 $2.80 projection stop are unchanged. Paid runs must still use run_s0_cost_watch.py, and only after all three groups approve the v3 hashes and MAIN_GO_s0.txt is written.
