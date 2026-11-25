# L2 amendment 1: cap raised from $11 to $16 (22 November 2026, PT)

Bijin, verbatim: "Raise cap to $16".
Only the spend limit changes. Plan, code, prompts, seeds and tests are unchanged
(freeze_hashes_law_l2.json still applies).
Reason: stage C2 cost about $0.0014 per job after 1,736 jobs, against an estimate of $0.00059,
because api-deepseek-4.1-flash wrote 850 to 1,700 output tokens per call.
Enforcement: PILOT_STOP_AT = 20.22 (the ledger already holds L1's $4.2249).
