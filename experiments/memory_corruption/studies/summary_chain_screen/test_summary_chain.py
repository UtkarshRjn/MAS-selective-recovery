"""Zero-cost unit tests for the S0 freeze. No API, no key, no ledger writes."""
from __future__ import annotations

import os
import sys
import json
import hashlib
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
import _core  # noqa: F401  (puts core/ on the import path)

import perturb_gaps as PG
import s0_source as S
import s0_grade as G
import s0_summarize as SM
import run_summary_chain as R


class TestJobCounts(unittest.TestCase):
    def test_counts(self):
        c = R.expected_counts()
        self.assertEqual(c['composition_jobs'], 144)
        self.assertEqual(c['channel_jobs'], 256)
        self.assertEqual(c['total_jobs'], 400)
        self.assertEqual(c['composition_calls'], 336)
        self.assertEqual(c['total_calls'], 592)
        js = R.build_jobs()
        self.assertEqual(len(js), 400)
        paths = [R.path_of(j) for j in js]
        self.assertEqual(len(paths), len(set(paths)))


class TestSource(unittest.TestCase):
    def test_typo_and_clean_identity(self):
        for tmpl in S.TEMPLATES:
            for seed in (30, 41, 60, 91):
                typo = S.make_annotated_cell(tmpl, seed, 'typo')
                clean = S.make_annotated_cell(tmpl, seed, 'clean')
                ts, cs = S.build_source(typo), S.build_source(clean)
                ok_t, rt = S.source_ok(typo, ts)
                ok_c, rc = S.source_ok(clean, cs)
                self.assertTrue(ok_t, (tmpl, seed, rt))
                self.assertTrue(ok_c, (tmpl, seed, rc))
                self.assertEqual(S.build_source(typo), ts)
                self.assertIn(typo['gap_true'], ts)
                self.assertIn(typo['gap_neighbor'], ts)
                self.assertEqual(S.extract_verbatim_value(ts), typo['gap_neighbor'])
                self.assertEqual(S.extract_verbatim_value(cs), clean['gap_true'])
                self.assertNotIn('copy these exactly', ts.lower())
                self.assertNotIn('copy these exactly', cs.lower())
                self.assertTrue(S.value_bookable(typo, typo['gap_true']))
                self.assertTrue(S.value_bookable(typo, typo['gap_neighbor']))
                # typo must not leak into the clean source
                self.assertNotIn(clean['gap_neighbor'], cs)
                self.assertEqual(PG.edit_distance(typo['gap_true'], typo['gap_neighbor']), 1)

    def test_different_seeds_differ(self):
        a = S.build_source(S.make_annotated_cell('trip', 30, 'typo'))
        b = S.build_source(S.make_annotated_cell('trip', 31, 'typo'))
        self.assertNotEqual(a, b)

    def test_trip_long_is_longer(self):
        a = S.build_source(S.make_annotated_cell('trip', 30, 'typo'))
        b = S.build_source(S.make_annotated_cell('trip_long', 30, 'typo'))
        self.assertGreater(len(b), len(a))
        self.assertGreater(len(b), 2000)


class TestGrader(unittest.TestCase):
    def test_near_miss_does_not_collide(self):
        self.assertTrue(G.value_present('book FL473 please', 'FL473'))
        self.assertFalse(G.value_present('book FL473 please', 'FL472'))
        self.assertFalse(G.value_present('book FL472 please', 'FL473'))

    def test_codebook(self):
        g = G.code_text('Book FL472 or maybe FL473.', 'FL472', 'FL473')
        self.assertTrue(g['valid'] and g['disagreement'] and g['state'] == 'disagreement')
        g = G.code_text('Book FL473 only.', 'FL472', 'FL473')
        self.assertTrue(g['typo_only'] and g['state'] == 'typo_only')
        g = G.code_text('Book FL472 only.', 'FL472', 'FL473')
        self.assertTrue(g['truth_only'])
        g = G.code_text('', 'FL472', 'FL473')
        self.assertFalse(g['valid'])
        g = G.code_text('{"flight_id": "FL473"}', 'FL472', 'FL473')
        self.assertFalse(g['valid'])
        self.assertEqual(g['invalid_reason'], 'not_plain')

    def test_prediction_and_saturating(self):
        # Fake depth-1: 24/24 typo survival would be ceiling; use 12/24 = 0.5
        rows = []
        for t in ('trip', 'trip_long'):
            for i in range(12):
                rows.append({
                    'template': t, 'grade': {
                        'valid': True, 'typo_present': i < 6, 'disagreement': i < 3,
                        'truth_present': True,
                    },
                })
        s1 = G.measure_survival(rows, 'typo_survival')
        self.assertAlmostEqual(s1['wilson']['p'], 0.5)
        pred = G.predict_from_s1(s1)
        self.assertAlmostEqual(pred['predictions']['2']['s1_pow_k'], 0.25)
        self.assertAlmostEqual(pred['predictions']['4']['s1_pow_k'], 0.0625)
        # Saturating fit: s1=0.8, s2=0.7 -> valid a, s_inf
        fit = G.fit_saturating(0.8, 0.7)
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(fit['a'], 0)
        self.assertLessEqual(fit['a'], 1)
        self.assertAlmostEqual(G.saturating_value(fit, 1), 0.8, places=6)
        self.assertAlmostEqual(G.saturating_value(fit, 2), 0.7, places=6)
        miss, reasons = G.prediction_miss(0.9, 0.1, 0.05, 0.2)
        self.assertTrue(miss and 'ratio_gt_2' in reasons)
        miss, reasons = G.prediction_miss(0.12, 0.10, 0.05, 0.2)
        self.assertFalse(miss)

    def test_correlation_and_false_alarm(self):
        # Independent typo_only at p=0.5 should be near p1*p2=0.25
        rows = []
        for t in ('trip', 'trip_long'):
            for i in range(40):
                c1 = {'valid': True, 'typo_only': (i % 2 == 0), 'state': 'typo_only' if i % 2 == 0 else 'truth_only'}
                c2 = {'valid': True, 'typo_only': (i % 4 < 2), 'state': 'typo_only' if i % 4 < 2 else 'truth_only'}
                rows.append({'template': t, 'c1': c1, 'c2': c2})
        rec = G.correlation_from_pairs(rows)
        self.assertTrue(rec['identified'])
        self.assertAlmostEqual(rec['p1'], 0.5)
        # Clean: all truth_only, same named id => false alarm 0
        clean = [{'template': 'trip',
                  'c1': {'valid': True, 'state': 'truth_only', 'named_ids': ['FL472'], 'truth': 'FL472'},
                  'c2': {'valid': True, 'state': 'truth_only', 'named_ids': ['FL472'], 'truth': 'FL472'}} for _ in range(20)]
        fa = G.false_alarm_rate(clean)
        self.assertEqual(fa['rate'], 0.0)
        self.assertFalse(fa['kill'])

    def test_omission_is_not_false_alarm(self):
        """truth_only vs neither (an omission) is not a clean false alarm or a visible conflict."""
        rows = [{'template': 'trip',
                 'c1': {'valid': True, 'state': 'truth_only', 'named_ids': ['FL472'], 'truth': 'FL472'},
                 'c2': {'valid': True, 'state': 'neither', 'named_ids': [], 'truth': 'FL472'}} for _ in range(20)]
        fa = G.false_alarm_rate(rows)
        self.assertEqual(fa['rate'], 0.0)
        self.assertFalse(fa['kill'])
        self.assertEqual(fa['state_diff']['rate'], 1.0)
        self.assertEqual(fa['omission_c2']['rate'], 1.0)
        pc = G.pair_codes(rows[0]['c1'], rows[0]['c2'])
        self.assertFalse(pc['visible_conflict'])
        self.assertTrue(pc['c2_omission'])
        # Two different named ids is a false alarm and a visible conflict
        diff = [{'template': 'trip',
                 'c1': {'valid': True, 'state': 'truth_only', 'named_ids': ['FL472'], 'truth': 'FL472'},
                 'c2': {'valid': True, 'state': 'neither', 'named_ids': ['FL999'], 'truth': 'FL472'}}]
        fa2 = G.false_alarm_rate(diff)
        self.assertEqual(fa2['rate'], 1.0)
        self.assertTrue(fa2['kill'])
        self.assertTrue(G.pair_codes(diff[0]['c1'], diff[0]['c2'])['visible_conflict'])
        # code_text names the flight ids it sees
        g = G.code_text('Book FL472 only.', 'FL472', 'FL473')
        self.assertEqual(g['named_ids'], ['FL472'])
        g2 = G.code_text('No flight identifier here at all.', 'FL472', 'FL473')
        self.assertEqual(g2['named_ids'], [])
        self.assertEqual(g2['state'], 'neither')


class TestMockHops(unittest.TestCase):
    def test_chain_and_invalid_stop(self):
        cell = S.make_annotated_cell('trip', 30, 'typo')
        source = S.build_source(cell)
        truth, typo = cell['gap_true'], cell['gap_neighbor']
        keep_both = 'Recommended %s, also listed %s and hotel stays put.' % (typo, truth) + (' word' * 40)
        typo_only = 'Recommended %s only, hotel already chosen, rest omitted.' % typo + (' word' * 40)
        llm = SM.MockLLM([keep_both, typo_only, typo_only, typo_only])
        hops, tr = SM.run_hops(cell, source, llm, n_hops=4, prompt_name='p1', temperature=0.0, seed=30)
        self.assertEqual(len(hops), 4)
        self.assertTrue(hops[0]['grade']['disagreement'])
        self.assertTrue(hops[1]['grade']['typo_only'])
        self.assertEqual(tr['cost'], 0.0)
        self.assertTrue(all(c.get('json_object') is False for c in tr['calls']))
        self.assertTrue(all(c.get('max_tokens') == 4000 for c in tr['calls']))
        # Invalid hop stops the chain
        llm2 = SM.MockLLM(['{"flight": "%s"}' % typo])
        hops2, _ = SM.run_hops(cell, source, llm2, n_hops=4, seed=30)
        self.assertFalse(hops2[0]['grade']['valid'])
        self.assertEqual(hops2[1]['invalid_reason'], 'prev_hop_invalid')
        self.assertEqual(len(llm2.script), 0)  # only one call used

    def test_prompts_load(self):
        p1, n1 = SM.load_prompt('p1')
        p2, n2 = SM.load_prompt('p2')
        self.assertIn('plain text only', p1.lower())
        self.assertIn('plain text only', p2.lower())
        self.assertNotEqual(p1, p2)
        self.assertEqual(n1, 'p1')
        self.assertIn('Do not use JSON', p2)
        self.assertIn('curly braces', p1)


class TestGoGate(unittest.TestCase):
    def test_refuse_without_s0_go(self):
        self.assertFalse(os.path.exists(R.GO_PATH) and os.path.getsize(R.GO_PATH) > 0)
        # Paid main must not be invoked. require_s0_go itself raises.
        with self.assertRaises(SystemExit) as e:
            R.require_s0_go()
        self.assertIn('MAIN_GO_s0.txt', str(e.exception))
        self.assertIn('No API calls', str(e.exception))

    def test_old_main_go_is_not_s0_go(self):
        # An older, non-empty MAIN_GO.txt next to the S0 go path must not authorize S0.
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, 'MAIN_GO.txt'), 'w') as f:
                f.write('approved for an older run\n')
            with mock.patch.object(R, 'GO_PATH', os.path.join(td, 'MAIN_GO_s0.txt')):
                with self.assertRaises(SystemExit) as e:
                    R.require_s0_go()
            self.assertIn('MAIN_GO_s0.txt', str(e.exception))


class TestLockPredictions(unittest.TestCase):
    def test_lock_once(self):
        rows = []
        for model in R.MODELS:
            for tmpl in R.TEMPLATES:
                for seed in R.COMPOSE_SEEDS:
                    cell = S.make_annotated_cell(tmpl, seed, 'typo')
                    text = 'keep %s and %s please' % (cell['gap_true'], cell['gap_neighbor']) + (' w' * 40)
                    rows.append({
                        'part': 'composition', 'model': model, 'template': tmpl,
                        'seed': seed, 'depth': 1, 'condition': 'typo',
                        'grade': G.code_text(text, cell['gap_true'], cell['gap_neighbor']),
                    })
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'pred.json')
            payload, wrote = R.lock_predictions(rows, path=path)
            self.assertTrue(wrote)
            self.assertEqual(payload['models'][R.MODELS[0]]['n_depth1'], 24)
            payload2, wrote2 = R.lock_predictions(rows, path=path)
            self.assertFalse(wrote2)
            self.assertEqual(payload2['locked_at_local'], payload['locked_at_local'])



def _pad(s):
    return s + (' word' * 40)


class TestRestrictedFalseAlarm(unittest.TestCase):
    """Restricted named ids, v4 truth-membership FA rule, invented ids, typo codebook conflict."""

    def _trip_long_clean(self, seed=80):
        cell = S.make_annotated_cell('trip_long', seed, 'clean')
        src = S.build_source(cell)
        truth = cell['gap_true']
        typo = cell['gap_neighbor']
        returns = G.return_flight_ids(cell)
        outbound = G.outbound_candidate_ids(cell, truth=truth, typo=typo)
        alts = sorted(outbound - {str(truth).upper(), str(typo).upper()} - returns)
        hotels = [str(h.get('id')).upper() for h in (cell['world'].get('hotels') or []) if h.get('id')]
        ret = sorted(returns)[0] if returns else None
        catalog = G.catalog_ids(cell, truth=truth, typo=typo)
        invented = 'FL8888'
        self.assertNotIn(invented, catalog)
        self.assertNotIn(invented, src.upper())
        self.assertGreaterEqual(len(alts), 2, (seed, outbound, returns))
        self.assertIsNotNone(ret)
        self.assertTrue(hotels)
        return {
            'cell': cell, 'src': src, 'truth': truth, 'typo': typo,
            'ret': ret, 'alts': alts, 'hotel': hotels[0], 'invented': invented,
        }

    def _grade(self, text, info):
        return G.code_text(text, info['truth'], info['typo'], cell=info['cell'], source=info['src'])

    def test_trip_long_return_and_outbound_alt_are_not_false_alarms(self):
        # OpenAI: {truth} vs {truth, return_id} is not a false alarm;
        # {truth} vs {truth, outbound_alt} is not; {truth} vs {outbound_alt} is.
        info = self._trip_long_clean(80)
        truth, ret, alt = info['truth'], info['ret'], info['alts'][0]
        g_truth = self._grade(_pad('Book %s please.' % truth), info)
        g_truth_ret = self._grade(_pad('Book outbound %s and return %s.' % (truth, ret)), info)
        g_truth_alt = self._grade(_pad('Book %s, also listed %s.' % (truth, alt)), info)
        g_alt = self._grade(_pad('Book %s only.' % alt), info)
        self.assertEqual(g_truth['named_ids'], [str(truth).upper()])
        self.assertEqual(g_truth_ret['named_ids'], [str(truth).upper()])
        self.assertIn(str(ret).upper(), g_truth_ret['all_named_ids'])
        self.assertNotIn(str(ret).upper(), g_truth_ret['named_ids'])
        self.assertFalse(G.is_clean_false_alarm_pair(g_truth, g_truth_ret))
        self.assertTrue(G.is_any_id_difference(g_truth, g_truth_ret))
        self.assertFalse(G.pair_codes(g_truth, g_truth_ret)['visible_conflict'])
        self.assertFalse(G.is_clean_false_alarm_pair(g_truth, g_truth_alt))
        self.assertTrue(G.is_clean_false_alarm_pair(g_truth, g_alt))
        self.assertTrue(G.pair_codes(g_truth, g_alt)['visible_conflict'])
        # A return-only mention is an omission on the restricted set, not a contradiction.
        g_ret = self._grade(_pad('Return flight is %s.' % ret), info)
        self.assertEqual(g_ret['named_ids'], [])
        self.assertFalse(G.is_clean_false_alarm_pair(g_truth, g_ret))
        self.assertTrue(G.pair_codes(g_truth, g_ret)['c2_omission'])

    def test_hotel_ids_are_excluded(self):
        info = self._trip_long_clean(80)
        hotel = info['hotel']
        g = self._grade(_pad('Book %s and stay at %s.' % (info['truth'], hotel)), info)
        self.assertNotIn(hotel, g['named_ids'])
        self.assertNotIn(hotel, g['all_named_ids'])
        self.assertEqual(g['named_ids'], [str(info['truth']).upper()])

    def test_extra_catalog_flights_are_not_a_false_alarm(self):
        # Anthropic: both name the truth, and one also lists two other catalog flights.
        info = self._trip_long_clean(80)
        a, b = info['alts'][0], info['alts'][1]
        g_truth = self._grade(_pad('Book %s please.' % info['truth']), info)
        g_extra = self._grade(_pad('Book %s, also listed %s and %s.' % (info['truth'], a, b)), info)
        self.assertEqual(set(g_extra['named_ids']), {str(info['truth']).upper(), a, b})
        self.assertFalse(G.is_clean_false_alarm_pair(g_truth, g_extra))
        self.assertFalse(G.pair_codes(g_truth, g_extra)['visible_conflict'])
        self.assertTrue(G.is_any_id_difference(g_truth, g_extra))
        fa = G.false_alarm_rate([{'template': 'trip_long', 'c1': g_truth, 'c2': g_extra}])
        self.assertEqual(fa['rate'], 0.0)
        self.assertEqual(fa['any_id_difference']['rate'], 1.0)
        self.assertFalse(fa['kill'])

    def test_invented_id_versus_truth_is_a_false_alarm(self):
        # Anthropic: one names the truth, the other names an invented FL id.
        info = self._trip_long_clean(80)
        g_truth = self._grade(_pad('Book %s please.' % info['truth']), info)
        g_inv = self._grade(_pad('Book %s only.' % info['invented']), info)
        self.assertEqual(g_inv['named_ids'], [info['invented']])
        self.assertTrue(G.is_clean_false_alarm_pair(g_truth, g_inv))
        self.assertTrue(G.pair_codes(g_truth, g_inv)['visible_conflict'])
        fa = G.false_alarm_rate([{'template': 'trip_long', 'c1': g_truth, 'c2': g_inv}])
        self.assertEqual(fa['rate'], 1.0)

    def test_typo_visible_conflict_uses_codebook_not_set_rule(self):
        typo = {'valid': True, 'state': 'typo_only', 'typo_present': True, 'truth_present': False,
                'disagreement': False, 'named_ids': ['FL473'], 'truth': 'FL472', 'typo': 'FL473'}
        truth = {'valid': True, 'state': 'truth_only', 'typo_present': False, 'truth_present': True,
                 'disagreement': False, 'named_ids': ['FL472'], 'truth': 'FL472', 'typo': 'FL473'}
        neither = {'valid': True, 'state': 'neither', 'typo_present': False, 'truth_present': False,
                   'disagreement': False, 'named_ids': [], 'truth': 'FL472', 'typo': 'FL473'}
        both = {'valid': True, 'state': 'disagreement', 'typo_present': True, 'truth_present': True,
                'disagreement': True, 'named_ids': ['FL472', 'FL473'], 'truth': 'FL472', 'typo': 'FL473'}
        self.assertTrue(G.typo_visible_conflict(typo, truth))
        self.assertTrue(G.pair_codes(typo, truth, condition='typo')['visible_conflict'])
        # Both contain the truth, so this is not a clean FA (v3 would also have said no, via subset).
        self.assertFalse(G.is_clean_false_alarm_pair(both, truth))
        self.assertTrue(G.typo_visible_conflict(both, truth))
        self.assertTrue(G.pair_codes(both, truth, condition='typo')['visible_conflict'])
        self.assertFalse(G.typo_visible_conflict(typo, typo))
        self.assertFalse(G.typo_visible_conflict(typo, neither))
        self.assertFalse(G.pair_codes(typo, neither, condition='typo')['visible_conflict'])

    def test_truth_membership_rule(self):
        """v4 NeurIPS fix: decide FA on truth membership, not set inclusion."""
        info = self._trip_long_clean(80)
        t = str(info['truth']).upper()
        a, b = info['alts'][0], info['alts'][1]
        inv = info['invented']
        g_t = self._grade(_pad('Book %s please.' % t), info)
        g_ta = self._grade(_pad('Book %s, also listed %s.' % (t, a)), info)
        g_tb = self._grade(_pad('Book %s, also listed %s.' % (t, b)), info)
        g_inv = self._grade(_pad('Book %s only.' % inv), info)
        g_a = self._grade(_pad('Book %s only.' % a), info)
        g_b = self._grade(_pad('Book %s only.' % b), info)
        g_t_inv = self._grade(_pad('Book %s, also %s.' % (t, inv)), info)
        # {T, A} vs {T, B} is not a false alarm (both name the truth).
        self.assertEqual(set(g_ta['named_ids']), {t, a})
        self.assertEqual(set(g_tb['named_ids']), {t, b})
        self.assertFalse(G.is_clean_false_alarm_pair(g_ta, g_tb))
        self.assertFalse(G.pair_codes(g_ta, g_tb)['visible_conflict'])
        fa_ab = G.false_alarm_rate([{'template': 'trip_long', 'c1': g_ta, 'c2': g_tb}])
        self.assertEqual(fa_ab['rate'], 0.0)
        self.assertEqual(fa_ab['fa_both_contain_truth']['k'], 0)
        self.assertTrue(G.is_any_id_difference(g_ta, g_tb))
        # {T} vs {X_invented} is a false alarm.
        self.assertTrue(G.is_clean_false_alarm_pair(g_t, g_inv))
        self.assertTrue(G.pair_codes(g_t, g_inv)['visible_conflict'])
        # {A} vs {B} is a false alarm (neither names the truth, sets differ).
        self.assertTrue(G.is_clean_false_alarm_pair(g_a, g_b))
        self.assertTrue(G.pair_codes(g_a, g_b)['visible_conflict'])
        fa_alts = G.false_alarm_rate([{'template': 'trip_long', 'c1': g_a, 'c2': g_b}])
        self.assertEqual(fa_alts['rate'], 1.0)
        # {T} vs {T, X_invented} is not a false alarm, and the competing-id secondary is counted.
        self.assertEqual(set(g_t_inv['named_ids']), {t, inv})
        self.assertEqual(g_t_inv['invented_named_ids'], [inv])
        self.assertTrue(g_t_inv['truth_plus_competing'])
        self.assertFalse(g_t['truth_plus_competing'])
        self.assertFalse(G.is_clean_false_alarm_pair(g_t, g_t_inv))
        pc = G.pair_codes(g_t, g_t_inv)
        self.assertFalse(pc['visible_conflict'])
        self.assertTrue(pc['truth_plus_competing_id'])
        fa_comp = G.false_alarm_rate([{'template': 'trip_long', 'c1': g_t, 'c2': g_t_inv}])
        self.assertEqual(fa_comp['rate'], 0.0)
        self.assertEqual(fa_comp['truth_plus_competing_id']['rate'], 1.0)
        self.assertEqual(fa_comp['fa_both_contain_truth']['k'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
