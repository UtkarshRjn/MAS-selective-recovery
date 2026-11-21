"""Unit tests for the Nov 21 gaps-not-errors perturbations. No network, no key."""
import hashlib
import json
import os
import unittest

import agent as A
import grade_v3 as GV
import perturb_gaps as PG
import templates_v3 as TV
import handoff_summary as HS

HERE = A.HERE
FREEZE = os.path.join(HERE, 'freeze_hashes_gaps_nov21.json')

FROZEN_FILES = {
    'conflict_rule': 'conflict_rule.txt',
    'decoy_note_flight': 'decoy_note_flight.txt',
    'decoy_note_email': 'decoy_note_email.txt',
    'hedge_tag': 'hedge_tag.txt',
    'conflict_summary_style': 'conflict_summary_style.txt',
}


def _cell(template='trip', seed=0, form='complete'):
    c = TV.make_cell(template, seed, 'C0')
    c['design'] = 'v3'
    c['arm'] = 'P_full'
    c['executor_plan_only'] = True
    c['summary_retry'] = False
    c['summary_gate'] = False
    return PG.annotate_cell(c, form)


def _plan(cell):
    key = cell['gap_slot']
    truth = cell['gap_true']
    if key == 'flight_id':
        return {'plan': {'flight_id': truth, 'hotel_id': TV.slot_values(cell['world']).get('hotel_id'),
                         'nights': TV.slot_values(cell['world']).get('nights'),
                         'email': TV.slot_values(cell['world']).get('email')}}
    return {'plan': {'email': truth, 'patterns': TV.slot_values(cell['world']).get('patterns')}}


def _results(cell):
    key = cell['gap_slot']
    truth = cell['gap_true']
    if key == 'flight_id':
        return [{'tool': 'search_flights', 'result': {'flights': [{'id': truth, 'price': 200}]}}]
    return [{'tool': 'get_user_profile', 'result': {'email': truth}}]


def _prepare(cell, form=None):
    if form:
        cell = PG.annotate_cell(cell if 'gap_form' in cell else cell, form)
    trace = {}
    hand = A.prepare_executor_handoff(cell, _plan(cell), _results(cell), [], None, trace)
    return hand, trace


class FreezeTests(unittest.TestCase):
    def test_hashes_match_files(self):
        frozen = json.load(open(FREEZE))
        for key, name in FROZEN_FILES.items():
            digest = hashlib.sha256(open(os.path.join(HERE, name), 'rb').read()).hexdigest()
            self.assertEqual(digest, frozen[key], key)

    def test_conflict_rule_text(self):
        self.assertIn('disagree', PG.conflict_rule_text().lower())
        self.assertIn('ask', PG.conflict_rule_text().lower())


class NeighborTests(unittest.TestCase):
    def test_flight_near_miss_is_deterministic_and_different(self):
        self.assertEqual(PG.near_miss_id('FL771'), 'FL772')
        self.assertEqual(PG.near_miss_id('FL599'), 'FL590')  # last digit 9+1 wraps to 0
        self.assertNotEqual(PG.near_miss_id('FL599'), 'FL599')

    def test_email_near_miss(self):
        e = 'avery.silva55@campusmail.example'
        n = PG.near_miss_email(e)
        self.assertEqual(n, 'avery.silva55@campus-mail.example')
        self.assertNotEqual(n, e)

    def test_annotate_stamps_values(self):
        c = _cell('trip', 0, 'substituted')
        self.assertEqual(c['gap_slot'], 'flight_id')
        self.assertEqual(c['gap_true'], 'FL599')
        self.assertEqual(c['gap_neighbor'], 'FL590')
        c = _cell('cleanup', 0, 'substituted')
        self.assertEqual(c['gap_slot'], 'email')
        self.assertIn('campus-mail', c['gap_neighbor'])


class FormTests(unittest.TestCase):
    def test_complete_keeps_truth(self):
        hand, tr = _prepare(_cell('trip', 0, 'complete'))
        self.assertIn('FL599', hand)
        self.assertNotIn('FL590', hand)
        cov = tr['slot_coverage']
        codes = {r['key']: r['code'] for r in cov['codes'] if r['key'] == 'flight_id'}
        self.assertEqual(codes.get('flight_id'), 'present')

    def test_dropped_redacts_truth(self):
        hand, tr = _prepare(_cell('trip', 0, 'dropped'))
        self.assertNotIn('FL599', hand)
        self.assertIn('[missing]', hand)
        self.assertNotIn('FL590', hand)
        codes = {r['key']: r['code'] for r in tr['slot_coverage']['codes'] if r['key'] == 'flight_id'}
        self.assertEqual(codes.get('flight_id'), 'dropped')

    def test_substituted_swaps_in_neighbor(self):
        hand, tr = _prepare(_cell('trip', 0, 'substituted'))
        self.assertNotIn('FL599', hand)
        self.assertIn('FL590', hand)
        codes = {r['key']: r['code'] for r in tr['slot_coverage']['codes'] if r['key'] == 'flight_id'}
        self.assertEqual(codes.get('flight_id'), 'substituted')

    def test_decoy_drops_truth_and_adds_note(self):
        hand, tr = _prepare(_cell('trip', 0, 'decoy'))
        self.assertNotIn('FL599', hand)
        self.assertIn('FL590', hand)
        self.assertIn('MEMORY.md', hand)
        self.assertIn('previous trip', hand.lower())
        codes = {r['key']: r['code'] for r in tr['slot_coverage']['codes'] if r['key'] == 'flight_id'}
        # neighbor is present and in alts, so this codes as substituted
        self.assertEqual(codes.get('flight_id'), 'substituted')

    def test_conflict_summary_wrong_verbatim_true(self):
        for form in ('conflict', 'conflict_rule'):
            hand, tr = _prepare(_cell('trip', 0, form))
            summary = tr.get('handoff_summary') or tr.get('conflict_summary')
            block = tr.get('verbatim_args') or ''
            self.assertIn('FL590', summary)
            self.assertNotIn('FL599', summary)
            self.assertIn('FL599', block)
            self.assertNotIn('FL590', block)
            self.assertIn('FL590', hand)
            self.assertIn('FL599', hand)
            # summary-only coverage is substituted; full handoff still has truth
            self.assertGreater(tr['slot_coverage_summary']['n_substituted'], 0)
            flight_h = [r for r in tr['slot_coverage_handoff']['codes'] if r['key'] == 'flight_id'][0]
            self.assertEqual(flight_h['code'], 'present')

    def test_ops_email_forms(self):
        for form, check in (
            ('dropped', lambda h, t, n: t not in h and '[missing]' in h),
            ('substituted', lambda h, t, n: t not in h and n in h),
            ('decoy', lambda h, t, n: t not in h and n in h and 'MEMORY.md' in h),
        ):
            hand, tr = _prepare(_cell('cleanup', 0, form))
            t = tr['gap_true']
            n = tr['gap_neighbor']
            self.assertTrue(check(hand, t, n), form)

    def test_all_six_templates_have_distinct_neighbor(self):
        for tmpl in TV.TEMPLATES:
            for seed in (0, 1):
                c = _cell(tmpl, seed, 'substituted')
                self.assertTrue(c['gap_true'])
                self.assertTrue(c['gap_neighbor'])
                self.assertNotEqual(c['gap_true'], c['gap_neighbor'])


class InjectAndHedgeTests(unittest.TestCase):
    def test_inject_adds_bookable_flight(self):
        c = _cell('trip', 0, 'substituted')
        w = dict(c['world'])
        w['flights'] = list(w['flights'])
        before = {f['id'] for f in w['flights']}
        self.assertNotIn(c['gap_neighbor'], before)
        ok = PG.inject_neighbor_into_world(w, c)
        self.assertTrue(ok)
        after = {f['id'] for f in w['flights']}
        self.assertIn(c['gap_neighbor'], after)
        # planner catalog size grew by one
        self.assertEqual(len(after), len(before) + 1)

    def test_hedge_attaches_and_is_detectable(self):
        c = _cell('trip', 0, 'complete')
        src, line = PG.attach_hedge_to_source('Book flight FL599.', c)
        self.assertIn('unverified', line.lower())
        self.assertIn('FL599', src)
        self.assertTrue(PG.hedge_phrase_in(src))
        self.assertFalse(PG.hedge_phrase_in('Book flight FL599 and email the user.'))

    def test_invented_ids_ignore_known(self):
        known = {'FL599', 'H475'}
        self.assertEqual(PG.invented_ids('Book FL599 or H475.', known), [])
        self.assertEqual(PG.invented_ids('Book FL123 instead of FL599.', known), ['FL123'])


class OutcomeTests(unittest.TestCase):
    def test_wrong_beats_ask(self):
        g = {'wrong_action': True, 'asked': True, 'held': False, 'acted': True}
        self.assertEqual(GV.behavioral_outcome(g), 'wrong_action')

    def test_ask_hold_correct(self):
        self.assertEqual(GV.behavioral_outcome({'asked': True}), 'ask')
        self.assertEqual(GV.behavioral_outcome({'held': True}), 'hold')
        self.assertEqual(GV.behavioral_outcome({'acted': True}), 'correct')
        self.assertEqual(GV.behavioral_outcome({}), 'other')

    def test_used_target(self):
        cell = {'gap_true': 'FL599', 'gap_neighbor': 'FL590'}
        g = {'actions': [{'args': {'flight_id': 'FL590'}, 'wrong': True}]}
        self.assertEqual(GV.used_target(g, cell), 'neighbor')
        g = {'actions': [{'args': {'flight_id': 'FL599'}, 'wrong': False}]}
        self.assertEqual(GV.used_target(g, cell), 'truth')


if __name__ == '__main__':
    unittest.main()
