"""Unit tests for verify/cue/hedge/stale perturbations. No network, no key."""
import hashlib, json, os, unittest

import agent as A
import grade_v3 as GV
import perturb_gaps as PG
import templates_v3 as TV
import tasks as T

HERE = A.HERE
FREEZE = os.path.join(HERE, 'freeze_hashes_gaps_verify.json')
SUBST = os.path.join(HERE, 'substitution_list_gaps_verify.json')

FROZEN = {
    'lookup_tool_reservation': 'lookup_tool_reservation.txt',
    'lookup_tool_contact': 'lookup_tool_contact.txt',
    'cue_request_flight': 'cue_request_flight.txt',
    'cue_request_email': 'cue_request_email.txt',
    'stale_memory_flight': 'stale_memory_flight.txt',
    'stale_memory_email': 'stale_memory_email.txt',
    'hedge_tag': 'hedge_tag.txt',
    'substitution_list': 'substitution_list_gaps_verify.json',
}


def _cell(template='trip', seed=0, form='substituted'):
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
        return {'plan': {'flight_id': truth, 'email': TV.slot_values(cell['world']).get('email')}}
    return {'plan': {'email': truth}}


def _results(cell):
    key = cell['gap_slot']
    truth = cell['gap_true']
    if key == 'flight_id':
        return [{'tool': 'search_flights', 'result': {'flights': [{'id': truth, 'price': 200}]}}]
    return [{'tool': 'get_user_profile', 'result': {'email': truth}}]


class FreezeAndListTests(unittest.TestCase):
    def test_hashes_if_file_present(self):
        if not os.path.exists(FREEZE):
            self.skipTest('freeze file written just before the run')
        frozen = json.load(open(FREEZE))
        for key, name in FROZEN.items():
            digest = hashlib.sha256(open(os.path.join(HERE, name), 'rb').read()).hexdigest()
            self.assertEqual(digest, frozen[key], key)

    def test_typo_neighbors_edit_distance_1_or_2_and_not_in_catalog(self):
        data = json.load(open(SUBST))
        for row in data['typo_neighbor']:
            if row['seed'] > 15:
                continue
            self.assertIn(row['edit_distance'], (1, 2), row)
            if row['slot'] == 'flight_id':
                self.assertFalse(row['exists_in_world_catalog'], row)

    def test_stale_flights_exist_in_catalog(self):
        data = json.load(open(SUBST))
        for row in data['stale_memory']:
            if row['slot'] == 'flight_id' and row['seed'] <= 1:
                self.assertTrue(row['exists_in_world_catalog'], row)
                self.assertNotEqual(row['value'], row['truth'])


class RequestAndCueTests(unittest.TestCase):
    def test_default_request_does_not_contain_truth(self):
        for tmpl in TV.TEMPLATES:
            c = _cell(tmpl, 0, 'substituted')
            self.assertNotIn(c['gap_true'], c['request'], tmpl)

    def test_cue_puts_truth_in_request_and_survives_substitution(self):
        c = _cell('trip', 0, 'substituted')
        c = PG.apply_cue_to_request(c)
        self.assertIn('FL599', c['request'])
        self.assertIn(c['cue_line'], c['request'])
        trace = {}
        hand = A.prepare_executor_handoff(c, _plan(c), _results(c), [], None, trace)
        # request keeps truth; plan part has neighbor
        req, body = PG.split_user_request(hand)
        self.assertIn('FL599', req)
        self.assertNotIn('FL599', body)
        self.assertIn('FL590', body)
        self.assertNotIn('FL590', req)

    def test_ops_cue_email(self):
        c = _cell('cleanup', 0, 'substituted')
        c = PG.apply_cue_to_request(c)
        truth, neigh = c['gap_true'], c['gap_neighbor']
        hand = A.prepare_executor_handoff(c, _plan(c), _results(c), [], None, {})
        req, body = PG.split_user_request(hand)
        self.assertIn(truth, req)
        self.assertIn(neigh, body)
        self.assertNotIn(truth, body)


class LookupAndStaleTests(unittest.TestCase):
    def test_world_lookup_returns_truth_not_neighbor(self):
        c = _cell('trip', 0, 'substituted')
        w = T.World(c['world'], c['mem'])
        rec = w.call('get_reservation', {})
        self.assertEqual(rec['flight_id'], 'FL599')
        self.assertNotEqual(rec['flight_id'], c['gap_neighbor'])
        self.assertTrue(rec.get('accepted') is not False)

    def test_ops_lookup_returns_email(self):
        c = _cell('cleanup', 0, 'substituted')
        w = T.World(c['world'], c['mem'])
        rec = w.call('get_account_contact', {})
        self.assertEqual(rec['email'], c['gap_true'])

    def test_stale_overrides_neighbor_and_appends_memory(self):
        c = _cell('trip', 0, 'substituted')
        typo = c['gap_neighbor']
        c = PG.apply_stale_memory(c)
        self.assertNotEqual(c['gap_neighbor'], typo)
        self.assertEqual(c['gap_neighbor'], 'FL569')
        self.assertIn('FL569', c['mem'])
        self.assertIn('Old trip note', c['mem'])

    def test_hedge_exact(self):
        self.assertTrue(PG.hedge_exact_in('x from MEMORY.md, unverified y'))
        self.assertTrue(PG.hedge_exact_in('value is unverified'))
        self.assertFalse(PG.hedge_exact_in('see MEMORY.md for the airline'))


class AgreeingAndGradeTests(unittest.TestCase):
    def test_agreeing_verbatim_has_truth_in_both_channels_no_llm(self):
        c = _cell('trip', 0, 'complete')
        c['agreeing_verbatim'] = True
        c['handoff_mode'] = 'verbatim'
        hand = A.prepare_executor_handoff(c, _plan(c), _results(c), [], None, {})
        self.assertIn('FL599', hand)
        self.assertIn('Call arguments from the planner source', hand)
        self.assertIn('Compressed planner handoff', hand)

    def test_faithful_but_wrong(self):
        cell = {'gap_true': 'FL599', 'gap_neighbor': 'FL590'}
        g = {'wrong_action': True, 'acted': True, 'asked': False,
             'actions': [{'args': {'flight_id': 'FL590'}, 'wrong': True, 'fabricated': False}]}
        g['used_target'] = 'neighbor'
        tr = {'executor_handoff': 'User request:\nBook a trip.\n\nPlan: book FL590\n', 'world_log': []}
        g = GV.extend_grade(cell, tr, g)
        self.assertTrue(g['faithful_to_handoff'])
        self.assertTrue(g['faithful_but_wrong'])
        self.assertFalse(g['correct_vs_truth'])
        self.assertFalse(g['faithful_to_request'])

    def test_verify_before_act(self):
        cell = {'gap_true': 'FL599', 'gap_neighbor': 'FL590'}
        log = [
            {'tool': 'get_reservation', 'args': {}, 'result': {'flight_id': 'FL599'}, 'irreversible': False, 'accepted': True},
            {'tool': 'book_flight', 'args': {'flight_id': 'FL590'}, 'result': {'status': 'booked'}, 'irreversible': True, 'accepted': True},
        ]
        g = GV.extend_grade(cell, {'executor_handoff': 'FL590', 'world_log': log},
                            {'acted': True, 'wrong_action': True, 'actions': [{'args': {'flight_id': 'FL590'}}], 'used_target': 'neighbor'})
        self.assertTrue(g['verify_before_act_or_pause'])
        self.assertTrue(g['verify_returned_truth'])


if __name__ == '__main__':
    unittest.main()
