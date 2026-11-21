# The first November 21 screen is void

Date: Saturday, 21 November 2026, Pacific Time.

The 72 run exploratory screen in traces_screen_nov21 must not be used as evidence that compression causes over asking or a completion collapse. NeurIPS and ICML reviewers blocked plan_main_nov21 for this reason. Anthropic and OpenAI approved with changes and raised the same core worry.

## What was wrong

In the 24 compressed runs (P_comp and P_comp_flags):
1. 17 of 24 handoff_summary fields were empty strings. The executor prompt ended with "Compressed planner handoff:" and then nothing.
2. The 7 non empty summaries were not 80 word plain text. They started with raw planner JSON, and some were cut mid token (for example "{" or '{"plan": "1').
3. The only compressed run that completed (B, seed 1, C0, P_comp) had an 1,854 character handoff, so it was not compressed in any useful sense.
4. The old manipulation check still passed 72 of 72, because it never tested that the summary was non empty, plain text, within a word limit, or complete for the required call arguments.

So the headline "compression causes over asking and completion collapse" was, in those traces, the executor asking after an empty or broken handoff. That is true by construction and is not a test of a valid summary.

## Root cause

LLM.complete always sent response_format json_object. The summarizer was also capped at 400 tokens and had no retry and no log of the raw reply. The model was required to emit JSON while the prompt asked for plain text. Many replies came back empty or truncated mid JSON.

## What we did instead

The summarizer now runs in plain text mode, with a 4000 token cap, raw response logging, one retry, and a hard slot coverage check. We added a frozen hand written oracle arm and a frozen structured prompt arm. A reviewer mandated exploratory re-screen then ran on deepseek only, with a $0.40 new spend cap. Results are in rescreen_nov21_results.txt and rescreen_nov21_results.json. They are also exploratory and belong in the appendix.

The first screen's uncompressed arms (S_full, S_match, P_short, P_long) did not use the broken summarizer. Those runs can be described as exploratory context, but they are still not confirmatory evidence.
