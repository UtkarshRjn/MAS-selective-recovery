"""Tests for stats_exact.py and the runner's use of it. Offline, no API calls."""
import importlib.util
import unittest

import _core  # noqa: F401  (puts core/ on the import path)
import stats_exact as SX

HAVE_NUMERIC = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))


def _recs(counts):
    """counts: {(tc, lp): (n_fv, n_fs, n_ask)} -> rows with y codes 0, 1, 2."""
    out = []
    for (tc, lp), (a, b, c) in counts.items():
        for y, n in ((0, a), (1, b), (2, c)):
            out += [{'y': y, 'tc': tc, 'lp': lp}] * n
    return out


class TestSeparation(unittest.TestCase):
    def test_no_events_is_not_estimable(self):
        # Deepseek primary grid: every run asked.
        sep = SX.separation_report(_recs({c: (0, 0, 36) for c in SX.DESIGN_CELLS}))
        self.assertFalse(sep[0]['estimable'])
        self.assertFalse(sep[1]['estimable'])
        self.assertEqual(sep[0]['n_events'], 0)

    def test_events_in_every_cell_is_estimable(self):
        # GLM follow_verbatim: 3, 2, 3, 2 events across the four cells; no follow_summary.
        counts = {(1.0, 0.0): (3, 0, 33), (1.0, 1.0): (2, 0, 34),
                  (0.0, 0.0): (3, 0, 33), (0.0, 1.0): (2, 0, 34)}
        sep = SX.separation_report(_recs(counts))
        self.assertTrue(sep[0]['estimable'])
        self.assertFalse(sep[1]['estimable'])

    def test_one_empty_cell_is_not_estimable(self):
        counts = {(1.0, 0.0): (3, 0, 33), (1.0, 1.0): (0, 0, 36),
                  (0.0, 0.0): (3, 0, 33), (0.0, 1.0): (2, 0, 34)}
        self.assertFalse(SX.separation_report(_recs(counts))[0]['estimable'])

    def test_degenerate_interval_does_not_exclude_zero(self):
        self.assertFalse(SX.ci_excludes_zero(-1.58, -1.58))
        self.assertFalse(SX.ci_excludes_zero(None, 1.0))
        self.assertFalse(SX.ci_excludes_zero(-0.5, 0.5))
        self.assertTrue(SX.ci_excludes_zero(0.1, 0.9))
        self.assertTrue(SX.ci_excludes_zero(-0.9, -0.1))


class TestExactTests(unittest.TestCase):
    def test_fisher_known_values(self):
        # 36/36 vs 34/36: 2 * C(36,2)/C(72,2) = 0.49296
        self.assertAlmostEqual(SX.fisher_exact_two_sided(36, 36, 34, 36), 0.492958, places=5)
        self.assertAlmostEqual(SX.fisher_exact_two_sided(3, 3, 0, 3), 0.1, places=9)
        self.assertAlmostEqual(SX.fisher_exact_two_sided(36, 36, 35, 36), 1.0, places=9)
        self.assertIsNone(SX.fisher_exact_two_sided(0, 0, 1, 1))

    def test_mcnemar_known_values(self):
        self.assertEqual(SX.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(SX.mcnemar_exact(2, 0), 0.5)
        self.assertAlmostEqual(SX.mcnemar_exact(0, 3), 0.25)
        self.assertAlmostEqual(SX.mcnemar_exact(0, 6), 2 / 64)

    def test_paired_uses_mcnemar_when_keys_match(self):
        rule = [{'template': 't', 'seed': s, 'a': True} for s in range(4)]
        base = [{'template': 't', 'seed': s, 'a': s > 1} for s in range(4)]
        p, method, d = SX.paired_ask_test(rule, base, lambda r: r['a'])
        self.assertEqual(method, 'mcnemar_exact')
        self.assertEqual((d['rule_only_ask'], d['matched_only_ask']), (2, 0))
        self.assertAlmostEqual(p, 0.5)

    def test_falls_back_to_fisher_without_pairing(self):
        rule = [{'template': 't', 'a': True}] * 3
        base = [{'template': 't', 'a': False}] * 3
        p, method, _ = SX.paired_ask_test(rule, base, lambda r: r['a'])
        self.assertEqual(method, 'fisher_exact')
        self.assertAlmostEqual(p, 0.1)


class TestHolm(unittest.TestCase):
    def test_adjusted_p_is_monotone(self):
        # The old runner reported 0.1537 and 0.0768 for two equal p-values.
        h = SX.holm([('a', 0.0768), ('b', 0.0768)])
        self.assertAlmostEqual(h['a']['p_holm'], 0.1536)
        self.assertAlmostEqual(h['b']['p_holm'], 0.1536)

    def test_step_down_stops_after_first_failure(self):
        h = SX.holm([('a', 0.01), ('b', 0.04), ('c', 0.03)])
        self.assertTrue(h['a']['reject_05'])        # 0.01 <= 0.05/3
        self.assertFalse(h['c']['reject_05'])       # 0.03 > 0.05/2
        self.assertFalse(h['b']['reject_05'])       # stopped
        self.assertGreaterEqual(h['b']['p_holm'], h['c']['p_holm'])


@unittest.skipUnless(HAVE_NUMERIC, 'numpy and scipy are needed to import the runner')
class TestRunnerLogit(unittest.TestCase):
    def _rows(self, counts):
        rows = []
        setups = {(1.0, 0.0): 'P_conflict_sv', (1.0, 1.0): 'P_conflict_vs',
                  (0.0, 0.0): 'P_reversed_sv', (0.0, 1.0): 'P_reversed_vs'}
        choice = {0: 'follow_verbatim', 1: 'follow_summary', 2: 'ask_or_hold'}
        i = 0
        for cell, ns in counts.items():
            for y, n in enumerate(ns):
                for _ in range(n):
                    rows.append({'setup': setups[cell], 'template': 'T%d' % (i % 6), 'seed': i,
                                 'verbatim_holds': 'truth' if cell[0] else 'neighbor',
                                 'v3_grade': {'conflict_choice': choice[y]}})
                    i += 1
        return rows

    def test_all_ask_grid_reports_nothing_significant(self):
        import run_conflict_asking as RC
        fit = RC.fit_mnlogit(self._rows({c: (0, 0, 36) for c in SX.DESIGN_CELLS}), n_boot=50)
        for eq in ('follow_verbatim_vs_ask', 'follow_summary_vs_ask'):
            for v in fit[eq].values():
                self.assertFalse(v['estimable'])
                self.assertFalse(v['excludes_zero'])
                self.assertIsNone(v['est'])


if __name__ == '__main__':
    unittest.main()
