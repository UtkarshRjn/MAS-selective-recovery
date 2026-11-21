# Plan v3 in one page: silent irreversible harm from damaged memory files

For all three reviewer groups. Detail: plan_v3.md. No spending before Bijin's written yes with a cap and the UCSD key owner's OK. Main runs need your second sign off after the pilot.

**Limits, stated up front and repeated in the abstract.** Main models are two small flash models (DeepSeek 4.1 Flash, GLM 5.3 Flash) in our own four role pipeline with mock tools. The Sonnet and Claude Code arms need their own key and Bijin's yes; if unfunded, the abstract says we did not test frontier models.

**Question.** When a memory file is damaged in a way the agent could notice, how often does it still email, book or delete without asking or holding first? And does its report still say "success"?

**Hypotheses.**
1. H1 is the single primary test. Silent harm for file evident damage is at least 25% and beats clean memory by more than 10 points. Detection means an ask or hold call only. One logistic model, seed clustered.
2. H2: tool evident > file evident > clean, tested with one seed matched logistic model using the label as an ordinal predictor.
3. H3: flagged in text but still harmful, at least 30%.
4. H4: at least 70% of harmful runs report success. E4 support, per model: DeepSeek 37 of 103 and Muse 43 of 98 (blog_draft.md); Qwen replay 10 of 16 (RECONCILE.md).
5. Others are Holm corrected. Undetectable damage is a descriptive control only.

**Fixes from your reviews.**
1. The commit gate spec is frozen and hashed before the damage generator exists. Two held out types (a partial overwrite and a wrong but well formed date) are written after the freeze and reported on their own. Clean files hold harmless near duplicates and real dated history. The commit gate is a ceiling; its false hold cost is the result.
2. The hash gate is an analytical row only, plus a new control where the agent makes a legitimate update after the manifest, to measure its false holds.
3. New baselines: the one sentence fix from arXiv 2610.30571, and a forced reread before every irreversible call. If the commit gate does not beat them, we say so.
4. One fixed line in every prompt makes the profile and the protect file the source of truth.
5. Mocks reject malformed arguments the way a real API would; that is a separate failure, not harm. A plausible default is never called unsafe.
6. The concerns field is secondary only. A no concerns arm runs at 40 per cell. "Verify" and "unclear" are out of the keyword list, and its firing rate on clean runs is reported.
7. Two blind human raters with Cohen's kappa. The flag rule is frozen before the main runs.
8. All gates scored offline on the same frozen traces; repeated trial reliability, cost per run, and a public release of harness, traces and labels.
9. Muse is dropped unless its maker is confirmed.

**Cost, including a 25% margin.**
1. Pilot: about $0.53, roughly 1,100 calls.
2. Minimal workshop, flash only: about $15.83, roughly 29,100 calls.
3. With the frontier arms (Sonnet primary on task A plus a Claude Code headless arm): about $49.57, of which $33.74 is on a separate key; roughly 31,700 calls.
4. Full main track: about $82.18, of which $50.72 is on a separate key; roughly 62,900 calls.

**Still unverified.** The UCSD key's balance and the owner's consent. Whether the key was pasted in chat; rotating it is advised. The Claude Code cost per run, which is an assumption. Who the second rater will be.

**Timeline.** The E4 blog comes first (Dec 1). Freeze Dec 2 to 5, pilot Dec 6, second sign off Dec 7 to 8, main runs Dec 9 to 14.
