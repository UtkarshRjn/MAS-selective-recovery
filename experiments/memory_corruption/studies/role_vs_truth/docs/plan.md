# L3 draft: handoff summarizers keep the plan's value, not the true value

Status: DRAFT. Not frozen, no spend. 23 November 2026 (PT).

## 1. First principles

A summarizer cannot know which of two conflicting values is true. It can only use what the
text shows: which value the plan tells it to act on, where each value sits, how much space
the summary has, and what else competes for that space. So the question "does the error
survive?" is badly posed. The right question is: **which mention does the summarizer keep,
and why?** Truth can only matter through these features.

That gives a simple causal model of one handoff:

    keep(mention) depends on role (plan or evidence), distance from the plan line,
                  summary budget, and competing important content (load)

The error survives only when it sits in the role that the summarizer keeps. After the first
summary, a surviving conflict is written compactly (both values close together), so later
handoffs rarely change it. Thus the first compression decides.

## 2. What the existing data already show (L2 traces, post hoc)

1. **Role decides which value is lost, every time.** In 2,016 plain-prompt runs that started
   with both values, the summary kept both 1,602 times and kept one value 414 times. In all
   414, it kept the plan's value and dropped the evidence-line value. It never dropped the
   plan's value (0 of 414). In L1 to L2, the plan's value was always the wrong one, so this
   looked like "errors survive".
2. **Content, not length, explains the 800-word peak.** The 800- and 1,500-word inputs carried
   the same 48 binding trip rules; 1,500 only added low-importance notes about earlier trips,
   and loss was lower there. Length was not the variable.
3. **Kept conflicts are compact.** When both values survived, they sat a median of 191
   characters apart in the summary. This explains the first-hop bottleneck.
4. **Conflict words are not the protection.** Only 28% of summaries that kept both values used
   a word such as "conflict". In pipelines, later loss was rare with or without such a word.

None of this is a confirmatory claim: it is post hoc. L3 tests it with fresh data.

## 3. Hypotheses (to pre-register)

H1. Role selectivity. The evidence-role value is lost much more often than the plan-role
    value, whichever one is true. Test: swap roles (truth in the plan, wrong value in the
    evidence line). Predictions: (a) the plan-role value is lost in at most 2% of runs in both
    role arms; (b) the evidence-role value is lost at similar rates in both arms (difference
    within 10 percentage points). Consequence: with the truth in the plan, the summarizer
    removes the error (V to T) at the same rate that it otherwise removes the truth.
H2. Distance. Evidence loss rises with distance from the plan line: adjacent < middle < end.
H3. Budget. Evidence loss falls with summary budget: 40 > 80 > 160 words.
H4. Load. At a fixed input length, 24 binding rules raise evidence loss compared with 0 rules
    (neutral filler of the same length).
H5. First hop decides. In 2-hop chains, the second hop loses the evidence value at a rate no
    higher than single-hop loss at the adjacent position (same budget and load), plus 5 points.
Quantitative check: an additive logistic model (role + position + budget + load), fitted on
budgets 40 and 160, predicts budget 80; the observed rate is inside the 95% interval in at
least 75% of cells.
Kill rule: if H1(b) fails on both models, the role claim is a null.

## 4. Design changes that remove the L2 confounds

- Every input has exactly 900 words. Only the position of the evidence line, the role
  assignment and the load change. The same padding paragraphs, in the same per-item random
  order, appear in every cell of one item.
- Load is changed by swapping 24 binding rules for neutral filler of the same length, not by
  adding text.
- The evidence line moves between three fixed points of the same text (adjacent to the plan,
  the middle, the end), so distance is varied on identical content.
- One error type (E1, the typo). E4 behaved the same in L2.
- Temperature 0.7, 2 samples per item, items as a random effect.

## 5. Grid and cost

Per model: 2 roles x 3 positions x 3 budgets x 2 loads = 36 cells, plus a provenance-prompt (F2)
arm of 2 roles x 3 positions x 2 loads = 12 cells; 24 items x 2 samples. That is 2,304 calls per
model. The 2-hop stage adds 384 chains (768 calls). Total about 5,376 calls, about $5 on the
two flash models at L2's observed rates.

## 6. What it would take for a main-track paper

- The role finding must hold on at least four model families, including two frontier models.
  The UCSD gateway has only two chat models, so this needs another key.
- One realistic setting: an orchestrator with subagents on a real task, where errors arise
  naturally (stale memory, a wrong tool result), not only by injection.
- An executor at the end, so that harm is measured.
- If role selectivity holds, the practical rule is short: **put verified values in the plan
  role, and put unverified ones in an evidence role.** The handoff format, not the model,
  decides which value survives.

## 7. Decisions recorded (Bijin, 23 November 2026)

- Bijin, verbatim: "Run L3, cap $7" (to: Do you approve the paid L3 run?).
- Enforcement: the ledger holds $18.2851 from L1 and L2, so PILOT_STOP_AT = 25.28 allows
  $7.00 new. Key spend before L3 was $24.98 of its $50 limit.
- Order, so that the budget-80 predictions exist before any budget-80 data:
  (1) stage C3 --fit-only (budgets 40 and 160, and F2); (2) --predict, hash recorded;
  (3) stage C3 (the budget-80 jobs); (4) stage H3; (5) --score. The runner refuses step 3 or 4
  without l3_predictions.json.
- Models: api-deepseek-4.1-flash, api-glm-5.3-flash. Seeds 300-311. Error type E1. Self-approved
  freeze, no outside reviewers.
- Tests are read on the plain prompts (budgets 40, 80, 160). F2 is descriptive only.
