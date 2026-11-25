# S0 analysis-only addendum (v4 re-freeze, 21 November 2026, Pacific Time)

This addendum is part of the S0 v4 re-freeze. It does not authorize spend. It does not write MAIN_GO_s0.txt. Do not change v4-hashed files after sign-off.

It records the analysis rules requested by Anthropic Researchers (non-blocking notes on v1, a blocking named-id fix on v2, and a non-blocking note on v3 about {truth, A} versus {truth, B}), NeurIPS and ICML Reviewers (required analysis addendum on v1; they approved v2 and blocked v3 on that same edge case), and OpenAI researchers (blocking omission fix on v1, blocking named-id fix on v2, and they approved v3). The blocking false-alarm change is also in the v4 plan and in s0_grade.py; this file states the other analysis rules and the wording.

v1 plan sha 6abdaafd8cc47efbe28cb63866586a3ee0abde14febbc2eb2a88f2fa18a289cb, v2 plan sha fa249536ef0dab099e8f64eacda87ba924b1130f7b528382ce2fe1cd9676eda3, and v3 plan sha f6ee1bd9062da082f9a8a271ebd74948c6cddd3e806dd9416fd61e5d7a610995 are superseded. freeze_hashes_s0.json, freeze_hashes_s0_v2.json, and freeze_hashes_s0_v3.json are left untouched.

## 1. Tie rule

Each depth cell has n=24, so the depth-4 Wilson interval is wide (about plus or minus 0.2). Both s1^k and the saturating backup can hit at depth 4 on the same model.

Rule: if both families hit at depth 4 on a model, report "not discriminated" for that model and claim no law for that model. A law claim needs one family to hit and the other to miss, on both models, with the same winning family. If the two models disagree, report "mixed" and claim no law.

## 2. Hop-wise conditional transmission (descriptive, not a primary)

Hop 1 compresses the long synthetic source. Hops 2, 3, and 4 compress an 80-word summary. Per-hop transmission is therefore not stationary, and a miss of the simple s^k law is the expected outcome, not a surprise. The saturating backup family exists for this reason.

Depth-4 jobs already log hops 1 through 4. Report, as a descriptive secondary only:

```
P(survive hop j | survived hop j-1) for j = 1..4
```

for typo transmission and for disagreement survival. Do not use this table as a new primary or to pick a family after seeing the data.

## 3. Part 2 framing (Eckhardt and Lee 1985)

Citation, opened on official pages this turn: Dave E. Eckhardt, Jr., and Larry D. Lee. A Theoretical Basis for the Analysis of Multiversion Software Subject to Coincident Errors. IEEE Transactions on Software Engineering, vol. SE-11, December 1985. doi:10.1109/tse.1985.231895. IEEE Xplore: https://ieeexplore.ieee.org/document/1701974. A January 1985 NASA technical report by the same authors, with the title "A theoretical basis for the analysis of redundant software subject to coincident errors," is at https://ntrs.nasa.gov/citations/19850015006 and http://hdl.handle.net/2060/19850015006.

The paper gives a probabilistic model in which independently chosen versions can fail together because some inputs are harder (the coincident-error intensity varies over the input space). That mechanism sits behind the Knight and Leveson 1986 empirical result.

At one sample per item per channel, excess correlation in a same-model i.i.d. pair can come from item-difficulty variation across worlds (some worlds always keep the typo), not only from channel coupling. So:

- Report excess correlation separately per template.
- State in the write-up that S0 cannot separate item difficulty from channel coupling.
- A follow-up needs at least 3 samples per item per channel.

## 4. Wording

- typo_survival measures faithful transmission of the planner's recommended value. The source gives no signal that the typo is wrong (both ids are in the catalog). Call it transmission, not error detection, in every table.
- Temperature 0 means s is a rate over worlds, not over repeated samples of the same world.
- Say "independent jobs," not "independent samples." Checked in the frozen code: composition uses temperature 0.0 and API seed = job_seed * 100 + hop_index, with the same p1 prompt and the same synthetic source. Hop 1 of the depth-2 and depth-4 jobs is therefore expected to match the depth-1 output for that seed, if the API honors the seed. The jobs are separate, but they share hop 1. CIs reflect seed (world) variation, not independent hop-1 draws. This does not break the composition test, because s1 is still locked from the depth-1 arm only, and depths 2 and 4 still test later hops. Disclose it.

## 5. Low power

n=24 per cell is a screen. Wilson half-widths are near plus or minus 0.2. If s1 is about 0.5, predicted s4 is about 0.06, and almost any outcome from 0 to 3 out of 24 lands inside the interval. A pass means "not refuted," not "law confirmed." Report interval widths next to every verdict.

## 6. Hop-1 versus later-hop asymmetry

As in item 2: hop 1 and later hops are not identical operations. A miss of the simple law is the expected outcome. Say so up front in the write-up.

## 7. Cost watch

After composition depth 1 (48 one-hop jobs), project the total as (observed new spend / calls completed) * 592 planned summarizer calls. If that projection is above $2.80, stop and report. Do not start later stages. This is not a silent drop of depth 4; the remaining grid, including depth 4, is left unstarted and that fact is written into the report.

The paid run must be launched via run_s0_cost_watch.py, which implements this projection stop. Do not launch run_s0_nov21.py directly for the paid grid. The hashed runner's $3.00 hard stop and $19.75 cumulative stop still apply as a backstop. With PILOT_WORKERS=8, jobs already in flight can push spend a few cents past a cap; report the real final spend.

## 8. Prior work

Keep the prior-work note already in the plan and in ideas_nov21.md: epidemic MAS models (Spark to Fire, Collective Loss of Control, Reliability-Contagion), saga and transaction papers, provenance graphs, and Knight and Leveson 1986, plus Eckhardt and Lee 1985 as in item 3. The new contribution is a pre-registered prediction made before the deeper depths run, and an N-version correlation measurement for handoff state, not priority on the contagion metaphor.

## Blocking code change already in the v4 plan and grader

The v1 omission fix and the v3 restricted named set are kept. v4 changes only the clean false-alarm decision, after NeurIPS blocked v3 because the neither-subset rule flagged {truth, alt A} versus {truth, alt B}.

Clean false alarm is: both channels valid, both restricted named sets non-empty, and then decided on truth membership. Let t_i = (truth in s_i). It is a false alarm if t1 != t2, or if neither set contains the truth and s1 != s2. It is not a false alarm if both channels name the truth, whatever the extra mentions. The restricted set is still outbound target-slot candidates (truth, typo, and world['flights'] ids, excluding return_flights and hotels), plus any invented FL id that is in neither the source nor the catalog. The same restricted set is used for omission flags. An invented id versus the truth is a false alarm. The all-ids difference remains a secondary labeled "any-id difference". A new secondary, not in the kill, is the truth-plus-competing-id rate: a channel names the truth together with the typo or an invented id. State-difference and omission rates remain secondaries. Anthropic's requested count of false-alarm pairs in which both sets contain the truth is now zero by construction.

Typo-run visible_conflict is taken from the codebook only: true when the two channels together contain both typo and truth, or when one channel is in the disagreement state. Do not use the generic set rule on typo runs.

Unit tests lock this on a trip_long cell: prior v3 cases still hold; {truth, A} versus {truth, B} is not a false alarm; {truth} versus an invented id is; {A} versus {B} is; {truth} versus {truth, invented} is not, and the truth-plus-competing-id secondary is counted; truth_only versus neither remains not a false alarm.

## If s1 is 1.0

If s1 equals 1.0 on a model, the bootstrap interval is [1, 1] and any later drop would count as a simple-law miss. Report that case as "ceiling at depth 1," not as "hops interact."
