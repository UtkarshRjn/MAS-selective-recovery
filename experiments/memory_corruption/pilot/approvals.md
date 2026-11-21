# Approvals and disclosed deviations (memory damage pilot)

All times are America/Los_Angeles (PT). This file records approvals as relayed to me in Bijin's 1:1 chat with his bot.
I cannot independently verify anything that happened outside that chat.

## 1. Spend approval for the first pilot
- **Who and when:** Bijin Jiby, Thu Nov 19, 2026, 4:25 PM PT, in his 1:1 chat, answering the bot's budget question ("How much should I spend?").
- **Exact words:** "Yes, run the pilot only, cap $1. The key owner is OK with it."
- **Key-owner consent:** recorded as Bijin's statement only. It was NOT independently verified; the key owner has not confirmed it to me directly.
- **Spend under this approval:** $0.588 by token pricing, $0.432 by the server /key/info spend change (ledger.json). The run stopped by itself well below the $0.90 internal stop.

## 2. Earlier owner messages about budget and safety (verbatim)
- **Nov 19, 2026, about 3:4x PM PT** (the exact minute is not available to me): "I can increase the budget but reviewers and open ai and anthropic researchers needs to have a detailed plan that will work and have a proper backing"
- **Standing rule** (date and time not available to me): "the key is a shared UCSD resource. Load it from the env file only, and never print, log or copy the key. Keep a cost counter and stop before you reach $10. Push nothing to main."
- **Original request:** "I want to run an experiment to see what if memory becomes corrupt like MD file that leads to proportion of failure, can you research on any ways for that talk to anthropic researchers and reviewers if they agree then create an experiment to rest it"

## 3. Reviewer sign-offs (the reviewers' plan)
| Round | Group | Verdict | Scope |
|---|---|---|---|
| 1 (plan_v2) | NeurIPS/ICML Reviewers | APPROVE WITH CHANGES; workshop 5/10 (6 with fixes), ICML main 3/10 | pilot only (~$0.55) |
| 1 | Anthropic Researchers | APPROVE WITH CHANGES; 7/10 | pilot only; main runs need a second sign-off; no spend without Bijin's own yes with a cap AND the key owner's OK |
| 1 | OpenAI researchers | APPROVE WITH CHANGES; research 6/10, workshop 7/10, main 4/10 | pilot; key balance and owner consent to be confirmed |
| 2 (pilot results, 4:50 PM PT) | NeurIPS/ICML Reviewers | APPROVE WITH CHANGES for a second pilot only; workshop 4/10 now, 6/10 if the reframe holds | not ready for the ~$18 tier |
| 2 (4:50 PM PT) | Anthropic Researchers | APPROVE WITH CHANGES; about 6/10 | re-pilot under $0.25 after fixes; main runs need Bijin's fresh written yes, a cap, and the key owner's OK |
| 2 (4:50 PM PT) | OpenAI researchers | APPROVE WITH CHANGES for the harness fixes; research 5/10, workshop 6/10, main 3/10 | smoke re-pilot (~$0.20); not ready for the main tier |

The plan_v3 must-fixes from round 1 are mapped in plan_v3.md. The round 2 must-fixes are answered in amendment2_2026-11-19.md.

## 4. Status of further spending
- **Re-pilot:** NOT approved yet. `run_repilot.sh` refuses to start unless `approvals/REPILOT_GO.txt` holds Bijin's exact words and time.
- **Minimal tier (~$15 to $18):** NOT approved. It needs all of the following:
  - the tier gates in amendment2 section 8;
  - all three groups' sign-off;
  - Bijin's fresh written yes with a cap;
  - the key owner's OK.

## 5. Disclosed deviations
1. **Labelling:** plan_v3 section 2 says Bijin plus one teammate label every damage type before any run. Instead, one labeller (the bot) assigned the labels for the pilot. The two-rater protocol (blind to condition, Cohen's kappa) is still pending and is a tier gate.
2. **Amendment 1** (16:45 PT, hashed in freeze_hashes.json stage 5): deepseek's max_tokens was raised from 900 to 2000 after it truncated 94 of 474 calls. Only the 21 affected runs were re-run, into traces_fix/. The original traces are kept.
3. **Pipeline:** a 3-role pipeline (planner, executor, writer) instead of plan_v3's 4 roles.
4. **Amendment 2** (this round, before any new data):
   - Task A authority line: Task A gets its own authority line without .protect, because Task A has no .protect file.
   - Harness fixes 2 to 5.
   - Manipulation check.
   - New primary endpoints, with H2 changed after the pilot.
   - Rubric v1.
   - Gate variants.
5. **Not run in the pilot:** G-prompt and G-reread.

## 6. Repeat pilot approval (added Nov 19, 2026)
- **Who and when:** Bijin Jiby, Thu Nov 19, 2026, 5:07 PM PT, in his 1:1 chat, answering "Run the repeat pilot?"
- **Exact words:** "Yes, run the repeat pilot. Stop total spend at $0.80, inside my $1 cap."
- **Stop:** run_repilot.sh changed from $0.75 to $0.80 total cumulative pilot spend (higher of token pricing and the server counter). This is a dated change after Amendment 2's freeze, made on Bijin's instruction.
- **Copy:** approvals/REPILOT_GO.txt

2026-11-21 12:55am PT (t140u), Bijin verbatim: "Run the memory corruption thing as well send it to anthropic researchers and reviewers and if they are confident create a new PR and research on it run the experiments". Interpreted cap for workshop tier: $20 total on TritonAI (plan estimate $16-19), only after all three groups sign off.

2026-11-21 1:00am PT (widget answer to t143s720), Bijin verbatim: "Yes, $20 total on TritonAI for the workshop tier, and the key owner is OK with about $20 on the shared UCSD key." Key-owner OK is Bijin's statement only (not independently verified). Hard stop $19.75.
