# Pre-registered null after Amendment 9 smoke v2

Date: Saturday, November 21, 2026, Pacific Time.

Amendment 9 was the last redesign. The F3 floor gate on P-scoped (no verifier) failed under the 2x2: 10 ok runs, silent harm 0/10. T1 on P-scoped was also 0/4 silent harm (only 4 runs were planned, so T1 could not have met the 8-ok rule by itself). The C0 false-veto ceiling passed (0/16 V-plan, 0/15 V-raw). The smoke retry halt did not trip (2/126 retries, none still empty).

Per the frozen rule in amendment9_2026-11-21.md: write the null and stop tuning. Do not write MAIN_GO. Do not retune the verifier prompt. Do not invent another access split.

**Null:** architecture does not matter here; information access does.

Smoke context, not a main-table claim: on F3 the P-scoped pipeline asked or held on all 10 ok runs (9 ask_user, 1 hold), often after a planner flag reached the actor (7/10). There was no silent handoff loss to measure. S-scoped also asked on all 4 F3 runs. Where no architecture can see a source (U1), S-full still showed silent harm on 3/4 runs. Full-access T1 showed 0/4 silent harm on both S-full and P-full. Verifiers never vetoed, including on damaged cells, because the actor usually asked or held before an irreversible call.

This file is a stop record, not a new amendment and not a launch authorization.
