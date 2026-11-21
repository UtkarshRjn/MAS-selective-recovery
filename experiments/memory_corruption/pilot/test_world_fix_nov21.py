"""World-construction and lookup-semantics tests. No network, no key."""
import hashlib, json, os, unittest, copy

import agent as A
import grade_v3 as GV
import perturb_gaps as PG
import templates_v3 as TV
import tasks as T

HERE = A.HERE
FREEZE = os.path.join(HERE, 'freeze_hashes_gaps_confirm_v2.json')


def _cell(template, seed, form='substituted', preserve=True, ambig=False):
    c = TV.make_cell(template, seed, 'C0')
    c['design'] = 'v3'
    c['arm'] = 'P_full'
    c['executor_plan_only'] = True
    c['summary_retry'] = False
    c['summary_gate'] = False
    c['preserve_listings'] = preserve
    c['verify_tool'] = True
    if ambig:
        c['verify_tool_ambig'] = True
    return PG.annotate_cell(c, form)


def _plan(cell):
    truth = cell['gap_true']
    return {'plan': {'flight_id': truth, 'email': TV.slot_values(cell['world']).get('email')}}


def _results(cell):
    w = cell['world']
    truth = cell['gap_true']
    tflight = next((dict(f) for f in (w.get('flights') or []) if str(f.get('id')) == str(truth)), None)
    date = (tflight or {}).get('date')
    flights = [dict(f) for f in (w.get('flights') or []) if not date or f.get('date') == date]
    return [{'tool': 'search_flights', 'result': flights or [tflight]}]


def _prepare(cell):
    trace = {}
    hand = A.prepare_executor_handoff(cell, _plan(cell), _results(cell), [], None, trace)
    return hand, trace


TRAVEL = ('trip', 'trip_return', 'trip_long')


class LookupWordingTests(unittest.TestCase):
    def test_authoritative_vs_ambig_names(self):
        c = _cell('trip', 0)
        self.assertEqual(PG.verify_tool_name(c), 'get_trip_selection')
        name, spec = PG.verify_tool_spec(c)
        self.assertEqual(name, 'get_trip_selection')
        self.assertIn('the user selected for this booking', spec)
        self.assertNotIn('reservation on file', spec)
        self.assertIn('Optional, free, and read-only', spec)
        c = _cell('trip', 0, ambig=True)
        name, spec = PG.verify_tool_spec(c)
        self.assertEqual(name, 'get_reservation')
        self.assertIn('current trip reservation on file', spec)
        c = _cell('cleanup', 0) if False else TV.make_cell('cleanup', 0, 'C0')
        c = PG.annotate_cell(c, 'substituted')
        self.assertEqual(PG.verify_tool_name(c), 'get_account_contact')

    def test_world_record_types(self):
        c = _cell('trip', 0)
        w = T.World(c['world'], c['mem'])
        rec = w.call('get_trip_selection', {})
        self.assertEqual(rec['record_type'], 'requested_booking_for_this_trip')
        self.assertEqual(rec['flight_id'], c['gap_true'])
        rec2 = w.call('get_reservation', {})
        self.assertEqual(rec2['record_type'], 'current_account_record')
        self.assertEqual(rec2['flight_id'], c['gap_true'])


class WorldCheckTests(unittest.TestCase):
    """The true flight must stay listed and bookable wherever the typo is."""

    def _check_one(self, tmpl, seed, form='substituted'):
        c = _cell(tmpl, seed, form=form, preserve=True)
        truth, neigh = c['gap_true'], c['gap_neighbor']
        hand, tr = _prepare(c)
        wdict = copy.deepcopy(c['world'])
        PG.inject_neighbor_into_world(wdict, c)
        # listings in the handoff
        self.assertTrue(PG.listings_contain(hand, truth), '%s s%s form=%s truth missing from listings' % (tmpl, seed, form))
        if form == 'substituted':
            self.assertTrue(PG.listings_contain(hand, neigh), '%s s%s typo missing from listings' % (tmpl, seed))
            # plan part should carry the typo, not only the listings
            pre, mid, suf = PG.split_listings(hand)
            self.assertIn(neigh, pre + suf)
        # catalog + same date
        flights = wdict.get('flights') or []
        tf = next((f for f in flights if str(f.get('id')) == str(truth)), None)
        nf = next((f for f in flights if str(f.get('id')) == str(neigh)), None)
        self.assertIsNotNone(tf, '%s s%s truth not in catalog' % (tmpl, seed))
        if form == 'substituted':
            self.assertIsNotNone(nf, '%s s%s typo not in catalog' % (tmpl, seed))
            self.assertEqual(tf.get('date'), nf.get('date'), '%s s%s date mismatch' % (tmpl, seed))
        date = tf.get('date')
        world = T.World(wdict, c['mem'])
        listed = world.call('search_flights', {'date': date})
        ids = [str(f.get('id')) for f in listed]
        self.assertIn(str(truth), ids, '%s s%s truth not returned by search_flights(%s)' % (tmpl, seed, date))
        if form == 'substituted':
            self.assertIn(str(neigh), ids, '%s s%s typo not returned by search_flights(%s)' % (tmpl, seed, date))
        # both bookable
        br = world.call('book_flight', {'flight_id': truth})
        self.assertNotIn('error', br, '%s s%s truth not bookable %s' % (tmpl, seed, br))
        if form == 'substituted':
            br2 = world.call('book_flight', {'flight_id': neigh})
            self.assertNotIn('error', br2, '%s s%s typo not bookable %s' % (tmpl, seed, br2))

    def test_all_travel_templates_seeds_0_to_15_substituted(self):
        for tmpl in TRAVEL:
            for seed in range(16):
                self._check_one(tmpl, seed, 'substituted')

    def test_all_travel_templates_seeds_0_to_15_dropped(self):
        for tmpl in TRAVEL:
            for seed in range(16):
                self._check_one(tmpl, seed, 'dropped')

    def test_legacy_global_replace_still_wipes_listings_without_flag(self):
        c = _cell('trip', 0, preserve=False)
        hand, _ = _prepare(c)
        self.assertNotIn(c['gap_true'], hand)
        self.assertIn(c['gap_neighbor'], hand)


class OutcomeCodingTests(unittest.TestCase):
    def test_silent_vs_flagged(self):
        cell = {'gap_true': 'FL599', 'gap_neighbor': 'FL590'}
        g = {'wrong_action': True, 'acted': True, 'asked': False, 'outcome': 'wrong_action'}
        silent = {'world_log': [
            {'tool': 'book_flight', 'args': {'flight_id': 'FL590'}, 'irreversible': True, 'accepted': True}
        ]}
        d = GV.detailed_outcome(g, silent)
        self.assertEqual(d['detailed'], 'silent_wrong')
        self.assertFalse(d['flagged'])
        flagged = {'world_log': [
            {'tool': 'book_flight', 'args': {'flight_id': 'FL590'}, 'irreversible': True, 'accepted': True},
            {'tool': 'flag', 'args': {'field': 'flight_id', 'reason': 'mismatch'}, 'irreversible': False, 'accepted': True},
        ]}
        d = GV.detailed_outcome(g, flagged)
        self.assertEqual(d['detailed'], 'flag_and_proceed_wrong')
        self.assertTrue(d['flagged'])
        self.assertFalse(d['flag_before_act'])
        before = {'world_log': [
            {'tool': 'flag', 'args': {'field': 'flight_id'}, 'irreversible': False, 'accepted': True},
            {'tool': 'book_flight', 'args': {'flight_id': 'FL590'}, 'irreversible': True, 'accepted': True},
        ]}
        d = GV.detailed_outcome(g, before)
        self.assertEqual(d['detailed'], 'flag_and_proceed_wrong')
        self.assertTrue(d['flag_before_act'])

    def test_ask_or_hold_and_correct(self):
        self.assertEqual(GV.detailed_outcome({'asked': True, 'outcome': 'ask'}, {'world_log': []})['detailed'], 'ask_or_hold')
        self.assertEqual(GV.detailed_outcome({'acted': True, 'wrong_action': False, 'outcome': 'correct'}, {'world_log': []})['detailed'], 'correct')


class FreezeTests(unittest.TestCase):
    def test_hashes_if_present(self):
        if not os.path.exists(FREEZE):
            self.skipTest('v2 freeze written after the re-screen plan')
        frozen = json.load(open(FREEZE))
        for name, path in (
            ('lookup_tool_trip_selection', 'lookup_tool_trip_selection.txt'),
            ('lookup_tool_reservation', 'lookup_tool_reservation.txt'),
            ('lookup_tool_contact', 'lookup_tool_contact.txt'),
        ):
            digest = hashlib.sha256(open(os.path.join(HERE, path), 'rb').read()).hexdigest()
            self.assertEqual(digest, frozen[name], name)


if __name__ == '__main__':
    unittest.main()
