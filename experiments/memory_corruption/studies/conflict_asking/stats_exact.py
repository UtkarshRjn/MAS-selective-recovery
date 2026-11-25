"""Small-sample statistics for the confirmatory v3 analysis. Pure Python, no numpy or scipy.

Added after the v3 grid as an analysis correction. It does not change arms, prompts,
grading, or any frozen file. Two fixes:

1. Multinomial-logit identifiability. With a saturated 2x2 design (intercept,
   true_channel, last_position, interaction), each outcome equation is identified only
   if every one of the 4 design cells has at least one run with that outcome and at
   least one run with the reference outcome. Otherwise the MLE diverges (separation)
   and an optimizer can still report convergence. Bootstrap replicates warm-started at
   the diverged point then return the same value, giving zero-width intervals that look
   significant. Such equations are reported as not estimable.

2. Rule-secondary tests. Ask rates near 100% with n=36 make the two-proportion z test
   invalid. Use an exact McNemar test when rule and matched runs pair one-to-one on
   (template, seed), and Fisher's exact test otherwise. Holm-adjusted p-values are
   made monotone (step-down maximum).
"""
from __future__ import annotations

from math import comb

DESIGN_CELLS = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))


def separation_report(recs, outcomes=(0, 1), ref=2):
    """For each outcome code, say whether its logit equation vs `ref` is identified.

    recs: dicts with 'y' (outcome code), 'tc', 'lp' (0/1 covariates).
    Returns {outcome: {'estimable': bool, 'n_events': int, 'cells': {(tc, lp): (k_outcome, k_ref)}}}.
    """
    out = {}
    for k in outcomes:
        cells = {}
        for c in DESIGN_CELLS:
            ys = [r['y'] for r in recs if (float(r['tc']), float(r['lp'])) == c]
            cells[c] = (sum(1 for y in ys if y == k), sum(1 for y in ys if y == ref))
        ok = all(kk > 0 and kr > 0 for kk, kr in cells.values())
        out[k] = {
            'estimable': ok,
            'n_events': sum(1 for r in recs if r['y'] == k),
            'cells': {'%d%d' % (int(c[0]), int(c[1])): v for c, v in cells.items()},
        }
    return out


def ci_excludes_zero(lo, hi, min_width=1e-6):
    """True only for a real interval that sits entirely on one side of 0."""
    if lo is None or hi is None:
        return False
    if hi - lo < min_width:
        return False  # degenerate interval: no sampling variation, not evidence
    return lo > 0 or hi < 0


def fisher_exact_two_sided(k1, n1, k2, n2):
    """Two-sided Fisher exact p for k1/n1 vs k2/n2 (sum of tables no more likely than observed)."""
    if n1 <= 0 or n2 <= 0:
        return None
    K, N = k1 + k2, n1 + n2
    denom = comb(N, n1)

    def pmf(x):
        return comb(K, x) * comb(N - K, n1 - x) / denom

    p_obs = pmf(k1)
    lo, hi = max(0, K - n2), min(K, n1)
    p = sum(pmf(x) for x in range(lo, hi + 1) if pmf(x) <= p_obs * (1 + 1e-7))
    return min(1.0, p)


def mcnemar_exact(b, c):
    """Exact two-sided McNemar p from discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def paired_ask_test(rule_rows, base_rows, is_ask, key=lambda r: (r.get('template'), r.get('seed'))):
    """Ask rate, rule vs matched no-rule cell.

    Exact McNemar if the two cells pair one-to-one on `key`; otherwise Fisher exact.
    Returns (p, method, detail).
    """
    rk = [key(r) for r in rule_rows]
    bk = [key(r) for r in base_rows]
    paired = (
        bool(rule_rows)
        and all(None not in k for k in rk + bk)
        and len(set(rk)) == len(rk) == len(bk) == len(set(bk))
        and set(rk) == set(bk)
    )
    if paired:
        base = {key(r): r for r in base_rows}
        b = sum(1 for r in rule_rows if is_ask(r) and not is_ask(base[key(r)]))
        c = sum(1 for r in rule_rows if not is_ask(r) and is_ask(base[key(r)]))
        return mcnemar_exact(b, c), 'mcnemar_exact', {'rule_only_ask': b, 'matched_only_ask': c}
    k1 = sum(1 for r in rule_rows if is_ask(r))
    k2 = sum(1 for r in base_rows if is_ask(r))
    p = fisher_exact_two_sided(k1, len(rule_rows), k2, len(base_rows))
    return (1.0 if p is None else p), 'fisher_exact', {'k_rule': k1, 'n_rule': len(rule_rows),
                                                       'k_matched': k2, 'n_matched': len(base_rows)}


def holm(ps, alpha=0.05):
    """Holm step-down on [(name, p)]. Adjusted p-values are monotone non-decreasing."""
    m = len(ps)
    ordered = sorted(ps, key=lambda x: x[1])
    out = {}
    still_rejecting = True
    prev_adj = 0.0
    for i, (name, p) in enumerate(ordered):
        thresh = alpha / (m - i)
        adj = max(prev_adj, min(1.0, p * (m - i)))
        prev_adj = adj
        reject = still_rejecting and p <= thresh
        if not reject:
            still_rejecting = False
        out[name] = {'p': p, 'p_holm': adj, 'reject_05': reject, 'holm_threshold': thresh}
    return out
