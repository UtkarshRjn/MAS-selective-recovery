"""Identity and rendering tests for the reversed-conflict 2x2. No network, no key."""
import copy
import hashlib
import json
import os
import unittest

import _core  # noqa: F401  (puts core/ on the import path)
import agent as A
import paths as P
import grade_v3 as GV
import perturb_gaps as PG
import templates_v3 as TV
import tasks as T

HERE = os.path.dirname(os.path.abspath(__file__))
FREEZE = os.path.join(HERE, 'docs', 'freeze_hashes_gaps_confirm_v3.json')

PRIMARY = (
    ('P_conflict_sv', 'conflict', PG.ORDER_SUMMARY_THEN_VERBATIM),
    ('P_conflict_vs', 'conflict', PG.ORDER_VERBATIM_THEN_SUMMARY),
    ('P_reversed_sv', 'reversed_conflict', PG.ORDER_SUMMARY_THEN_VERBATIM),
    ('P_reversed_vs', 'reversed_conflict', PG.ORDER_VERBATIM_THEN_SUMMARY),
)
TEMPLATES = TV.TEMPLATES
SEEDS = range(16)


def _cell(template, seed, form, order=None):
    c = TV.make_cell(template, seed, 'C0')
    c['design'] = 'v3'
    c['arm'] = 'P_full'
    c['executor_plan_only'] = True
    c['summary_retry'] = False
    c['summary_gate'] = False
    if order:
        c['conflict_order'] = order
    return PG.annotate_cell(c, form)


def _plan(cell):
    vals = TV.slot_values(cell['world'])
    key = cell['gap_slot']
    if key == 'flight_id':
        return {'plan': {k: vals.get(k) for k in ('flight_id', 'return_flight_id', 'hotel_id', 'nights', 'email', 'airline') if vals.get(k) is not None}}
    return {'plan': {k: vals.get(k) for k in ('email', 'patterns', 'protected', 'retention') if vals.get(k) is not None}}


def _results(cell):
    w = cell['world']
    truth = cell['gap_true']
    if cell['gap_slot'] == 'flight_id':
        flights = [dict(f) for f in (w.get('flights') or []) if str(f.get('id')) == str(truth)]
        return [{'tool': 'search_flights', 'result': flights or [{'id': truth}]}]
    return [{'tool': 'get_user_profile', 'result': {'email': truth}}]


def _prepare(cell):
    trace = {}
    hand = A.prepare_executor_handoff(cell, _plan(cell), _results(cell), [], None, trace)
    return hand, trace


def _catalog_ids(world):
    flights = [str(f.get('id')) for f in (world.get('flights') or []) if f.get('id')]
    hotels = [str(h.get('id')) for h in (world.get('hotels') or []) if h.get('id')]
    return (tuple(flights), tuple(hotels))


class LegacyConflictStillHolds(unittest.TestCase):
    def test_conflict_summary_wrong_verbatim_true(self):
        for form in ('conflict', 'conflict_rule'):
            hand, tr = _prepare(_cell('trip', 0, form))
            summary = tr.get('conflict_summary') or tr.get('handoff_summary')
            block = tr.get('conflict_verbatim') or tr.get('verbatim_args') or ''
            self.assertIn('FL590', summary)
            self.assertNotIn('FL599', summary)
            self.assertIn('FL599', block)
            self.assertNotIn('FL590', block)
            parts = PG.parse_conflict_handoff(hand)
            self.assertTrue(parts['ok'])
            self.assertEqual(parts['order'], PG.ORDER_SUMMARY_THEN_VERBATIM)
            # reassembled default-order handoff matches Screen 1 layout
            rebuilt = PG.assemble_conflict_handoff(
                parts['request'], parts['summary'], parts['verbatim'], parts['flags'], parts['order']
            )
            self.assertEqual(hand, rebuilt)


class ReversedAndOrderTests(unittest.TestCase):
    def test_reversed_summary_true_verbatim_typo(self):
        hand, tr = _prepare(_cell('trip', 0, 'reversed_conflict'))
        self.assertEqual(tr['summary_holds'], 'truth')
        self.assertEqual(tr['verbatim_holds'], 'neighbor')
        self.assertIn('FL599', tr['conflict_summary'])
        self.assertNotIn('FL590', tr['conflict_summary'])
        self.assertIn('FL590', tr['conflict_verbatim'])
        self.assertNotIn('FL599', tr.get('conflict_verbatim').split('flight_id')[1].split('\n')[0])

    def test_order_flip_puts_verbatim_first(self):
        c = _cell('trip', 0, 'conflict', PG.ORDER_VERBATIM_THEN_SUMMARY)
        hand, tr = _prepare(c)
        parts = PG.parse_conflict_handoff(hand)
        self.assertEqual(parts['order'], PG.ORDER_VERBATIM_THEN_SUMMARY)
        self.assertLess(hand.find('Call arguments from the planner source'), hand.find('Compressed planner handoff:'))
        self.assertIn('FL590', parts['summary'])
        self.assertIn('FL599', parts['verbatim'])

    def test_rule_forms_set_the_flag(self):
        for form in ('conflict_rule', 'reversed_conflict_rule'):
            c = _cell('trip', 0, form)
            self.assertTrue(c.get('conflict_rule'))
            self.assertIn(form, PG.CONFLICT_FORMS)


class ChannelChoiceTests(unittest.TestCase):
    def test_follow_verbatim_vs_summary_vs_ask(self):
        cell = {'gap_true': 'FL599', 'gap_neighbor': 'FL590', 'gap_slot': 'flight_id',
                'summary_holds': 'neighbor', 'verbatim_holds': 'truth'}
        g = {'acted': True, 'wrong_action': False, 'actions': [
            {'tool': 'book_flight', 'args': {'flight_id': 'FL599'}}
        ]}
        self.assertEqual(GV.conflict_channel_choice(g, cell), 'follow_verbatim')
        g2 = {'acted': True, 'wrong_action': True, 'actions': [
            {'tool': 'book_flight', 'args': {'flight_id': 'FL590'}}
        ]}
        self.assertEqual(GV.conflict_channel_choice(g2, cell), 'follow_summary')
        self.assertEqual(GV.conflict_channel_choice({'asked': True, 'acted': False, 'actions': []}, cell), 'ask_or_hold')
        # reversed: typo is in verbatim
        rev = dict(cell, summary_holds='truth', verbatim_holds='neighbor')
        self.assertEqual(GV.conflict_channel_choice(g2, rev), 'follow_verbatim')
        self.assertEqual(GV.conflict_channel_choice(g, rev), 'follow_summary')


class IdentityTests(unittest.TestCase):
    """All primary arms are byte-identical except swapped channel contents and order.

    Both IDs are present and bookable in the world catalog after typo inject.
    """

    def _arms(self, template, seed):
        out = []
        for name, form, order in PRIMARY:
            cell = _cell(template, seed, form, order)
            hand, tr = _prepare(cell)
            world = copy.deepcopy(cell['world'])
            PG.inject_neighbor_into_world(world, cell)
            out.append((name, form, order, cell, hand, tr, world))
        return out

    def test_identity_and_bookable_all_templates_seeds_0_to_15(self):
        for tmpl in TEMPLATES:
            for seed in SEEDS:
                arms = self._arms(tmpl, seed)
                base_cell = arms[0][3]
                truth, neigh = base_cell['gap_true'], base_cell['gap_neighbor']
                self.assertTrue(truth)
                self.assertTrue(neigh)
                self.assertNotEqual(truth, neigh)
                # catalogs identical across the 4 arms
                cats = [_catalog_ids(a[6]) for a in arms]
                self.assertEqual(len(set(cats)), 1, '%s s%s catalogs differ: %s' % (tmpl, seed, cats))
                # both IDs bookable (travel) or accepted (ops email)
                world = T.World(copy.deepcopy(arms[0][6]), base_cell['mem'])
                if base_cell['gap_slot'] == 'flight_id':
                    for fid in (truth, neigh):
                        br = world.call('book_flight', {'flight_id': fid})
                        self.assertNotIn('error', br or {}, '%s s%s %s not bookable %s' % (tmpl, seed, fid, br))
                    flights = (arms[0][6].get('flights') or [])
                    ids = {str(f.get('id')) for f in flights}
                    self.assertIn(str(truth), ids)
                    self.assertIn(str(neigh), ids)
                parsed = []
                for name, form, order, cell, hand, tr, w in arms:
                    parts = PG.parse_conflict_handoff(hand)
                    self.assertTrue(parts['ok'], '%s s%s %s parse failed' % (tmpl, seed, name))
                    self.assertEqual(parts['order'], order, '%s s%s %s order' % (tmpl, seed, name))
                    rebuilt = PG.assemble_conflict_handoff(
                        parts['request'], parts['summary'], parts['verbatim'], parts['flags'], parts['order']
                    )
                    self.assertEqual(hand, rebuilt, '%s s%s %s reassemble mismatch' % (tmpl, seed, name))
                    parsed.append((name, parts, cell))
                # request and flags byte-identical
                reqs = [p[1]['request'] for p in parsed]
                flags = [p[1]['flags'] for p in parsed]
                self.assertEqual(len(set(reqs)), 1, '%s s%s requests differ' % (tmpl, seed))
                self.assertEqual(len(set(flags)), 1, '%s s%s flags differ' % (tmpl, seed))
                # typo-summary arms share a summary; truth-summary arms share a summary
                by = {name: parts for name, parts, _cell in parsed}
                self.assertEqual(by['P_conflict_sv']['summary'], by['P_conflict_vs']['summary'])
                self.assertEqual(by['P_reversed_sv']['summary'], by['P_reversed_vs']['summary'])
                self.assertEqual(by['P_conflict_sv']['verbatim'], by['P_conflict_vs']['verbatim'])
                self.assertEqual(by['P_reversed_sv']['verbatim'], by['P_reversed_vs']['verbatim'])
                # summaries differ only by the target value
                s_typo = by['P_conflict_sv']['summary']
                s_true = by['P_reversed_sv']['summary']
                self.assertEqual(s_typo.replace(str(neigh), str(truth)), s_true)
                self.assertIn(str(neigh), s_typo)
                self.assertIn(str(truth), s_true)
                self.assertNotIn(str(truth), s_typo)
                self.assertNotIn(str(neigh), s_true)
                # verbatim blocks differ only on the target slot line
                v_true = by['P_conflict_sv']['verbatim']
                v_typo = by['P_reversed_sv']['verbatim']
                self.assertEqual(PG.set_verbatim_slot(v_true, base_cell['gap_slot'], neigh), v_typo)
                self.assertIn(str(truth), v_true)
                self.assertIn(str(neigh), v_typo)
                # flipping order and/or swapping channel bodies turns one arm into another, byte for byte
                req, fl = reqs[0], flags[0]
                built_vs = PG.assemble_conflict_handoff(req, s_typo, v_true, fl, PG.ORDER_VERBATIM_THEN_SUMMARY)
                built_rev_sv = PG.assemble_conflict_handoff(req, s_true, v_typo, fl, PG.ORDER_SUMMARY_THEN_VERBATIM)
                built_rev_vs = PG.assemble_conflict_handoff(req, s_true, v_typo, fl, PG.ORDER_VERBATIM_THEN_SUMMARY)
                hands = {name: hand for name, _f, _o, _c, hand, _t, _w in arms}
                self.assertEqual(built_vs, hands['P_conflict_vs'])
                self.assertEqual(built_rev_sv, hands['P_reversed_sv'])
                self.assertEqual(built_rev_vs, hands['P_reversed_vs'])
                # conflict handoffs have no tool-result listings; that absence is identical
                for name, parts, _cell in parsed:
                    self.assertNotIn('Tool results the planner already received', hands[name])


class FreezeTests(unittest.TestCase):
    def test_hashes_if_present(self):
        if not os.path.exists(FREEZE):
            self.skipTest('v3 freeze written after the tests pass')
        frozen = json.load(open(FREEZE))
        for name, path in (
            ('conflict_rule', P.prompt('conflict_rule.txt')),
            ('conflict_summary_style', P.prompt('conflict_summary_style.txt')),
            ('plan_gaps_confirm_nov21_v3', os.path.join(HERE, 'docs', 'plan_gaps_confirm_nov21_v3.md')),
        ):
            digest = hashlib.sha256(open(path, 'rb').read()).hexdigest()
            self.assertEqual(digest, frozen[name], name)


if __name__ == '__main__':
    unittest.main()
