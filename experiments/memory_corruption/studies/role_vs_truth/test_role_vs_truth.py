"""Offline tests for L3 (role selectivity). No key, no calls."""
import os
import random
import unittest

import _core  # noqa: F401  (puts core/ on the import path)
import role_texts as L3
import run_role_vs_truth as R
import handoff_texts as LI


class TestInputs(unittest.TestCase):
    def test_every_cell_is_900_words_and_holds_both_values(self):
        for t in LI.TEMPLATES:
            for sd in (300, 305, 311):
                cell = LI.make_item(t, sd, 'E1')
                for role in L3.ROLES:
                    for pos in L3.POSITIONS:
                        for load in L3.LOADS:
                            text = L3.l3_input(cell, role, pos, load)
                            self.assertEqual(L3.words(text), L3.TARGET_WORDS, (t, sd, role, pos, load))
                            self.assertEqual(LI.input_state(cell, text), 'V')

    def test_role_swaps_only_the_two_values(self):
        cell = LI.make_item('trip', 300, 'E1')
        a = L3.l3_input(cell, 'wrong_plan', 'middle', 24)
        b = L3.l3_input(cell, 'truth_plan', 'middle', 24)
        truth, wrong = cell['gap_true'], cell['gap_neighbor']
        swapped = a.replace(truth, '@@').replace(wrong, truth).replace('@@', wrong)
        self.assertEqual(swapped, b)

    def test_positions_share_the_same_padding(self):
        cell = LI.make_item('trip', 300, 'E1')
        texts = [L3.l3_input(cell, 'wrong_plan', p, 24) for p in L3.POSITIONS]
        ev = L3.evidence_line(cell, cell['gap_true'])
        stripped = [t.replace(' ' + ev, '').replace(ev + '\n', '') for t in texts]
        self.assertEqual(stripped[0], stripped[1])
        self.assertEqual(stripped[1], stripped[2])

    def test_load_changes_rules_not_length(self):
        cell = LI.make_item('trip', 300, 'E1')
        zero = L3.l3_input(cell, 'wrong_plan', 'end', 0)
        full = L3.l3_input(cell, 'wrong_plan', 'end', 24)
        self.assertEqual(zero.count('Binding trip rule:'), 0)
        self.assertEqual(full.count('Binding trip rule:'), 24)
        self.assertEqual(L3.words(zero), L3.words(full))

    def test_evidence_position_order(self):
        cell = LI.make_item('trip', 300, 'E1')
        ev = L3.evidence_line(cell, cell['gap_true'])
        idx = [L3.l3_input(cell, 'wrong_plan', p, 24).index(ev) for p in L3.POSITIONS]
        self.assertLess(idx[0], idx[1])
        self.assertLess(idx[1], idx[2])

    def test_role_outcome(self):
        self.assertEqual(L3.role_outcome('S', 'wrong_plan'), 'evidence_lost')
        self.assertEqual(L3.role_outcome('T', 'wrong_plan'), 'plan_lost')
        self.assertEqual(L3.role_outcome('T', 'truth_plan'), 'evidence_lost')
        self.assertEqual(L3.role_outcome('S', 'truth_plan'), 'plan_lost')
        self.assertEqual(L3.role_outcome('V', 'truth_plan'), 'both')
        self.assertIsNone(L3.role_outcome(None, 'truth_plan'))


class TestModel(unittest.TestCase):
    def test_logistic_recovers_parameters(self):
        rng = random.Random(2)
        true_w = [-1.0, 0.8, -0.5]
        X, y = [], []
        for _ in range(4000):
            x = [1.0, float(rng.random() < 0.5), rng.uniform(-1, 1)]
            X.append(x)
            y.append(1 if rng.random() < R.predict(true_w, x) else 0)
        w = R.fit_logistic(X, y, ridge=1e-6)
        for a, b in zip(w, true_w):
            self.assertAlmostEqual(a, b, delta=0.2)

    def test_budget_80_is_held_out(self):
        self.assertNotIn(80, R.FIT_BUDGETS)


class TestRunner(unittest.TestCase):
    def test_job_keys_unique(self):
        jobs = R.stage_c3_jobs(seeds=(300,)) + R.stage_h3_jobs(seeds=(300,))
        self.assertEqual(len({R.job_key(j) for j in jobs}), len(jobs))

    def test_dry_run(self):
        self.assertEqual(R.main(['--dry-run', '--seeds', '1']), 0)

    def test_paid_stage_refuses_without_go(self):
        if os.path.exists(R.GO_FILE):
            self.skipTest('a go file exists on this machine')
        with self.assertRaises(SystemExit):
            R.main(['--stage', 'C3'])


if __name__ == '__main__':
    unittest.main()
