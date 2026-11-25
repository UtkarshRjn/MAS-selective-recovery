"""Offline tests for the L1 law line. No key, no calls."""
import math
import unittest

import _core  # noqa: F401  (puts core/ on the import path)
import handoff_texts as LI
import state_model as LM
import harness as H
import run_handoff_chains as LR


class TestModel(unittest.TestCase):
    def test_identity_chain_keeps_start(self):
        self.assertEqual(LM.predict_chain('S', [LM.identity()] * 3), LM.onehot('S'))

    def test_fit_leaves_unmeasured_rows_none(self):
        fit = LM.fit_matrix([('V', 'V'), ('V', 'S'), ('T', None)], alpha=0)
        self.assertEqual(fit['P'][LM.IDX['V']], [0.5, 0.5, 0.0, 0.0])
        self.assertIsNone(fit['P'][LM.IDX['T']])
        self.assertEqual(fit['dropped_invalid'], 1)
        with self.assertRaises(ValueError):
            LM.vecmat(LM.onehot('T'), fit['P'])

    def test_reproduction_number_threshold(self):
        # V resolves to truth; S survives with 0.6 and stays silent. rho(Q) = 0.6.
        P = [[0, 0, 1, 0], [0, 0.6, 0.4, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        self.assertAlmostEqual(LM.reproduction_number(P, 1), 0.6)
        self.assertAlmostEqual(LM.reproduction_number(P, 2), 1.2)   # snowballs at b=2
        self.assertAlmostEqual(LM.critical_branching(P), 1 / 0.6)

    def test_tree_counts_match_closed_form(self):
        P = [[1, 0, 0, 0], [0, 0.6, 0.4, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        tree = LM.predict_tree(LM.onehot('S'), P, 2, 3)
        for k, level in enumerate(tree):
            self.assertAlmostEqual(level['S'], 1.2 ** k)

    def test_reground_turns_silent_into_visible(self):
        P = [[0.5, 0.5, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0.5, 0.5]]
        G = LM.reground_matrix(P)
        self.assertEqual(G[LM.IDX['S']], [1.0, 0, 0, 0])
        self.assertEqual(G[LM.IDX['N']], [0, 0, 1.0, 0])
        self.assertLess(LM.p_harm(LM.vecmat(LM.onehot('S'), G)), LM.p_harm(LM.vecmat(LM.onehot('S'), P)))

    def test_union_merge_and_fallback(self):
        self.assertEqual(LM.union_merge('S', 'T'), 'V')
        self.assertEqual(LM.union_merge('N', 'T'), 'T')
        out, fb = LM.predict_merge(LM.onehot('S'), LM.onehot('T'), {})
        self.assertEqual(out, LM.onehot('V'))
        self.assertEqual(fb, 1.0)
        out, fb = LM.predict_merge(LM.onehot('S'), LM.onehot('T'), {('S', 'T'): [0, 1, 0, 0]})
        self.assertEqual(out, LM.onehot('S'))
        self.assertEqual(fb, 0.0)

    def test_spectral_radius_complex_pair(self):
        self.assertAlmostEqual(LM.spectral_radius_2x2([[0, -1], [1, 0]]), 1.0)


class TestInputs(unittest.TestCase):
    def test_relay_and_compress_inputs_have_the_intended_state(self):
        for t in LI.TEMPLATES:
            for sd in (100, 105, 111):
                for e in LI.ERRORS:
                    cell = LI.make_item(t, sd, e)
                    for f in LI.LLM_FIXES:
                        for s in LI.RELAY_STATES:
                            self.assertEqual(LI.input_state(cell, LI.relay_input(cell, s, f)), s, (t, sd, e, f, s))
                        starts = ('V', 'T') if e == 'E1' else ('V',)
                        for s in starts:
                            self.assertEqual(LI.input_state(cell, LI.compress_input(cell, s, f)), s)

    def test_e4_is_a_different_real_flight(self):
        cell = LI.make_item('trip', 100, 'E4')
        ids = {f['id'] for f in cell['world']['flights']}
        self.assertIn(cell['gap_neighbor'], ids)
        self.assertNotEqual(cell['gap_neighbor'], cell['gap_true'])

    def test_pin_survives_any_summary(self):
        cell = LI.make_item('trip', 100, 'E1')
        text = LI.relay_input(cell, 'S', 'F1')
        pin = LI.extract_pin(text)
        out = LI.attach_pin('Summary that dropped every id.', pin)
        self.assertEqual(LI.input_state(cell, out), 'S')
        self.assertEqual(LI.attach_pin('x', ''), 'x')

    def test_merge_input_combines_pins_once(self):
        cell = LI.make_item('trip', 100, 'E1')
        body, pin = LI.merge_input(cell, 'S', 'S', 'F1')
        self.assertEqual(pin.count('- flight_id'), 1)
        _, pin0 = LI.merge_input(cell, 'S', 'T', 'F0')
        self.assertEqual(pin0, '')

    def test_no_copy_exactly_label(self):
        cell = LI.make_item('trip_long', 100, 'E1')
        for f in LI.LLM_FIXES:
            self.assertNotIn('copy these exactly', LI.compress_input(cell, 'V', f).lower())


class TestRunner(unittest.TestCase):
    def test_grid_sizes(self):
        m = list(H.MODELS)
        # per model per item: compress 3x2 (E1: 2 starts, E4: 1) x samples 2 ... computed directly
        jc = LR.stage_c_jobs(m, seeds=(100,))
        per_model_template = (2 + 1) * 3 * 2 + 4 * 2 * 3 * 2 + 6 * 2 * 3
        self.assertEqual(len(jc), per_model_template * len(LI.TEMPLATES) * len(m))
        self.assertEqual(len({LR.job_key(j) for j in jc}), len(jc))
        jp = LR.stage_p_jobs(m, seeds=(100,))
        self.assertEqual(len({LR.job_key(j) for j in jp}), len(jp))

    def test_dry_run_is_clean(self):
        self.assertEqual(LR.main(['--dry-run', '--seeds', '1']), 0)

    def test_paid_stage_refuses_without_go(self):
        import os
        if os.path.exists(LR.GO_FILE):
            self.skipTest('a go file exists on this machine')
        with self.assertRaises(SystemExit):
            LR.main(['--stage', 'C'])


if __name__ == '__main__':
    unittest.main()
