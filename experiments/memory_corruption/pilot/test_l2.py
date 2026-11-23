"""Offline tests for L2 (compression-ratio law). No key, no calls."""
import math
import os
import random
import unittest

import l2_inputs as L2
import l2_run as R
import law_inputs as LI


class TestInputs(unittest.TestCase):
    def test_every_level_and_placement_keeps_the_state(self):
        for t in LI.TEMPLATES:
            for sd in (200, 205, 211):
                for e in LI.ERRORS:
                    cell = LI.make_item(t, sd, e)
                    for lvl in L2.LEVELS:
                        for s in LI.RELAY_STATES:
                            self.assertEqual(LI.input_state(cell, L2.l2_input(cell, s, lvl)), s, (t, sd, e, lvl, s))
                        self.assertEqual(LI.input_state(cell, L2.l2_input(cell, 'V', lvl, 'spread')), 'V')
                        for p in L2.PIN_ARMS:
                            self.assertEqual(LI.input_state(cell, L2.l2_input(cell, 'V', lvl, pin=p)), 'V')

    def test_lengths_hit_the_target(self):
        cell = LI.make_item('trip', 200, 'E1')
        for lvl in L2.LEVELS[1:]:
            n = L2.words(L2.l2_input(cell, 'V', lvl, 'spread'))
            self.assertLessEqual(abs(n - lvl), 10, (lvl, n))

    def test_padding_has_no_flight_ids(self):
        cell = LI.make_item('trip_long', 200, 'E1')
        for p in L2.padding_pool(cell):
            self.assertIsNone(L2.FL_RE.search(p))

    def test_spread_puts_the_truth_at_the_end(self):
        cell = LI.make_item('trip', 200, 'E1')
        text = L2.l2_input(cell, 'V', 1500, 'spread')
        self.assertGreater(text.index(cell['gap_true']), text.index('Additional context'))
        adj = L2.l2_input(cell, 'V', 1500, 'adjacent')
        self.assertLess(adj.index(cell['gap_true']), adj.index('Additional context'))

    def test_pin_values(self):
        cell = LI.make_item('trip', 200, 'E1')
        self.assertIn('- flight_id: %s' % cell['gap_neighbor'], L2.l2_input(cell, 'V', 300, pin='pin_wrong'))
        self.assertIn('- flight_id: %s' % cell['gap_true'], L2.l2_input(cell, 'V', 300, pin='pin_truth'))
        with self.assertRaises(ValueError):
            L2.l2_input(cell, 'S', 300, pin='pin_wrong')

    def test_f2_only_at_80_words(self):
        with self.assertRaises(ValueError):
            L2.messages('F2', 'x', 'b40')


class TestFit(unittest.TestCase):
    def test_logistic_recovers_parameters(self):
        rng = random.Random(1)
        xs, ys = [], []
        for _ in range(4000):
            x = rng.uniform(-1, 3)
            xs.append(x)
            ys.append(1 if rng.random() < R.sigmoid(-2 + 1.5 * x) else 0)
        a, b = R.fit_logistic(xs, ys)
        self.assertAlmostEqual(a, -2, delta=0.2)
        self.assertAlmostEqual(b, 1.5, delta=0.2)

    def test_logistic_stays_finite_under_separation(self):
        a, b = R.fit_logistic([0, 0, 1, 1], [0, 0, 1, 1])
        self.assertTrue(math.isfinite(a) and math.isfinite(b))

    def test_interp_matrix(self):
        I = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
        Z = [[0.0, 1.0, 0.0, 0.0] for _ in range(4)]
        mats = {80: I, 1500: Z}
        self.assertEqual(R.interp_matrix(mats, 80), I)
        self.assertEqual(R.interp_matrix(mats, 3000), Z)
        mid = R.interp_matrix(mats, int(math.sqrt(80 * 1500)))
        self.assertAlmostEqual(mid[0][0], 0.5, places=2)
        self.assertAlmostEqual(mid[0][1], 0.5, places=2)


class TestRunner(unittest.TestCase):
    def test_job_keys_unique(self):
        jc, jp = R.stage_c2_jobs(seeds=(200,)), R.stage_p2_jobs(seeds=(200,))
        self.assertEqual(len({R.job_key(j) for j in jc}), len(jc))
        self.assertEqual(len({R.job_key(j) for j in jp}), len(jp))

    def test_level_800_is_not_used_to_fit(self):
        self.assertNotIn(800, L2.CAL_LEVELS)

    def test_dry_run(self):
        self.assertEqual(R.main(['--dry-run', '--seeds', '1']), 0)

    def test_paid_stage_refuses_without_go(self):
        if os.path.exists(R.GO_FILE):
            self.skipTest('a go file exists on this machine')
        with self.assertRaises(SystemExit):
            R.main(['--stage', 'C2'])


if __name__ == '__main__':
    unittest.main()
