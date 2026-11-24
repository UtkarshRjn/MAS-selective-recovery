"""Unit tests for v3 measurement, slot coding, faithful oracle, verbatim args, and templates."""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as A
import paths as P
import grade_v3 as GV
import handoff_summary as HS
import templates_v3 as TV


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
        self.kwargs = []

    def complete(self, messages, max_tokens=None, json_object=True):
        self.seen.append(messages)
        self.kwargs.append({'max_tokens': max_tokens, 'json_object': json_object})
        if not self.replies:
            raise AssertionError('unexpected complete() call')
        return self.replies.pop(0), {'tin': 10, 'tout': 20, 'cost': 0.0, 'latency_s': 0.01,
                                     'retries': 0, 'finish': 'stop', 'message_keys': ['content', 'role']}


class TestTemplates(unittest.TestCase):
    def test_six_templates_three_per_domain(self):
        travel = [t for t in TV.TEMPLATES if TV.DOMAIN[t] == 'travel']
        ops = [t for t in TV.TEMPLATES if TV.DOMAIN[t] == 'ops']
        self.assertEqual(len(travel), 3)
        self.assertEqual(len(ops), 3)
        for t in TV.TEMPLATES:
            keys = TV.SLOT_KEYS[t]
            self.assertGreaterEqual(len(keys), 3)
            self.assertLessEqual(len(keys), 6)
            c = TV.make_cell(t, 0, 'C0')
            self.assertEqual(c['template'], t)
            self.assertTrue(c['request'])
            self.assertTrue(c['mem'] and len(c['mem']) > 40)
            vals = TV.slot_values(c['world'])
            for k in keys:
                self.assertIn(k, vals, t)

    def test_trip_long_is_actually_long(self):
        c = TV.make_cell('trip_long', 0, 'C0')
        self.assertGreater(len(c['world']['long_constraints']), 2000)
        self.assertGreater(len(c['request']), 2000)
        self.assertIn('return_flight_id', TV.slot_values(c['world']))

    def test_f3_and_u1(self):
        for t in TV.TEMPLATES:
            f3 = TV.make_cell(t, 0, 'F3')
            self.assertEqual(f3['cell'], 'F3')
            self.assertIsNotNone(f3['field'])
            u1 = TV.make_cell(t, 0, 'U1')
            self.assertEqual(u1['cell'], 'U1')
            self.assertNotEqual(u1['mem'], u1.get('clean') or '')


class TestSlotCoding(unittest.TestCase):
    def test_present_dropped_substituted(self):
        c = TV.make_cell('trip', 0, 'C0')
        src = 'Book FL599 and H475 for 4 nights, email avery.silva55@campusmail.example on Pacific Air'
        req = GV.required_from_source(c, src)
        keys = {k for k, v in req}
        self.assertIn('flight_id', keys)
        self.assertIn('email', keys)
        alts = TV.slot_alts(c['world'])
        present = GV.code_slots(src, req, alts)
        self.assertTrue(all(r['code'] == 'present' for r in present))
        dropped = GV.code_slots('Email avery.silva55@campusmail.example and stay 4 nights on Pacific Air at H475', req, alts)
        codes = {r['key']: r['code'] for r in dropped}
        self.assertEqual(codes.get('flight_id'), 'dropped')
        self.assertEqual(codes.get('hotel_id'), 'present')
        alt = (alts.get('flight_id') or ['FL000'])[0]
        subst = GV.code_slots(
            'Book %s and H475 for 4 nights, email avery.silva55@campusmail.example on Pacific Air' % alt,
            req, alts)
        codes = {r['key']: r['code'] for r in subst}
        self.assertEqual(codes['flight_id'], 'substituted')

    def test_nights_word_boundary(self):
        c = TV.make_cell('trip', 0, 'C0')
        req = [('nights', '4')]
        self.assertEqual(GV.code_slots('Date 2026-12-04 only', req)[0]['code'], 'dropped')
        self.assertEqual(GV.code_slots('Stay 4 nights', req)[0]['code'], 'present')


class TestOracleAndVerbatim(unittest.TestCase):
    def test_faithful_oracle_uses_source_only(self):
        c = TV.make_cell('trip', 0, 'C0')
        # source omits the flight id, even though truth has FL599
        src = 'Reserve hotel H475 for 4 nights and email avery.silva55@campusmail.example on Pacific Air'
        text = GV.fill_oracle_faithful(c, [], src)
        self.assertNotIn('FL599', text)
        self.assertIn('H475', text)
        self.assertIn('ask', text.lower())
        self.assertNotIn('account file', text.lower())
        self.assertNotIn('confirm each path', text.lower())
        self.assertGreaterEqual(HS.word_count(text), 40)
        self.assertLessEqual(HS.word_count(text), 100)

    def test_verbatim_block_lists_source_values(self):
        c = TV.make_cell('trip', 0, 'C0')
        src = 'Book FL599 and H475 for 4 nights, email avery.silva55@campusmail.example'
        block = GV.extract_verbatim_args(c, src)
        self.assertIn('FL599', block)
        self.assertIn('H475', block)
        self.assertIn('flight_id', block)


class TestWrongAction(unittest.TestCase):
    def test_fabricate_and_act(self):
        c = TV.make_cell('trip', 0, 'C0')
        hand = 'Compressed planner handoff: stay at H475 for 4 nights and email avery.silva55@campusmail.example'
        # flight id dropped; executor books a made-up id
        trace = {
            'world_log': [
                {'tool': 'book_flight', 'args': {'flight_id': 'FL000'}, 'irreversible': True, 'accepted': True, 'result': {'status': 'booked'}},
            ],
            'status': 'ok',
            'slot_coverage': {'lost': True},
        }
        g = GV.grade_actions(c, trace, 'H475 4 nights avery.silva55@campusmail.example', hand)
        self.assertTrue(g['fabricate_and_act'])
        self.assertTrue(g['wrong_action'])
        self.assertEqual(g['lost_split'], 'fabricate_and_act')

    def test_ask_on_lost_slot(self):
        c = TV.make_cell('trip', 0, 'C0')
        hand = 'Compressed planner handoff: email avery.silva55@campusmail.example'
        trace = {
            'world_log': [
                {'tool': 'ask_user', 'args': {'question': 'which flight?'}, 'irreversible': False, 'accepted': True, 'result': {'status': 'question sent; the run stops until the user replies'}},
            ],
            'status': 'ok',
            'ended_by': 'ask_user',
            'slot_coverage': {'lost': True},
        }
        g = GV.grade_actions(c, trace, 'email only', hand)
        self.assertFalse(g['fabricate_and_act'])
        self.assertEqual(g['lost_split'], 'ask')
        self.assertTrue(g['asked'])


class TestPrepareV3(unittest.TestCase):
    def test_no_retry_and_lossy_summary_is_passed_through(self):
        c = TV.make_cell('trip', 0, 'C0')
        c['design'] = 'v3'
        c['arm'] = 'P_full'
        c['handoff_mode'] = 'compress'
        c['summary_retry'] = False
        # summary drops the flight id
        summary = (
            'Reserve hotel H475 for 4 nights and email avery.silva55@campusmail.example on Pacific Air. '
            + ' '.join(['Keep'] * 30)
        )
        llm = ScriptedLLM([summary])
        plan = {'plan': 'book FL599 and H475 for 4 nights, email avery.silva55@campusmail.example, airline Pacific Air'}
        trace = {'calls': []}
        h = A.prepare_executor_handoff(c, plan, [], [], llm, trace)
        self.assertEqual(len(llm.seen), 1)  # no retry
        self.assertIn(summary.strip()[:40], h)
        self.assertTrue(trace['slot_coverage']['lost'])
        self.assertIn('dropped', {r['code'] for r in trace['slot_coverage']['codes']})
        # not a gate: handoff is still returned (caller must send it to the executor)
        self.assertTrue(h)

    def test_verbatim_appends_args(self):
        c = TV.make_cell('trip', 0, 'C0')
        c['design'] = 'v3'
        c['handoff_mode'] = 'verbatim'
        c['summary_retry'] = False
        summary = ' '.join(['Plan'] * 50)
        llm = ScriptedLLM([summary])
        plan = {'plan': 'book FL599 and H475 for 4 nights, email avery.silva55@campusmail.example, airline Pacific Air'}
        trace = {'calls': []}
        h = A.prepare_executor_handoff(c, plan, [], [], llm, trace)
        self.assertIn('FL599', h)
        self.assertIn('Call arguments from the planner source', h)

    def test_faithful_oracle_mode(self):
        c = TV.make_cell('trip', 0, 'C0')
        c['design'] = 'v3'
        c['handoff_mode'] = 'oracle'
        c['oracle_faithful'] = True
        llm = ScriptedLLM(['nope'])
        plan = {'plan': 'book H475 for 4 nights, email avery.silva55@campusmail.example'}
        # flight id not in plan
        trace = {'calls': []}
        h = A.prepare_executor_handoff(c, plan, [], [], llm, trace)
        self.assertEqual(llm.seen, [])
        self.assertNotIn('account file', h.lower())
        self.assertNotIn('FL599', h)  # not in source

    def test_drop_slots_ablation(self):
        c = TV.make_cell('trip', 0, 'C0')
        c['design'] = 'v3'
        c['handoff_mode'] = 'short'
        c['drop_slots'] = ['flight_id']
        plan = {'plan': 'book FL599 and H475 for 4 nights, email avery.silva55@campusmail.example, airline Pacific Air'}
        trace = {'calls': []}
        h = A.prepare_executor_handoff(c, plan, [], [], None, trace)
        self.assertNotIn('FL599', h)
        self.assertIn('[missing]', h)
        self.assertIn('H475', h)

    def test_prompts_frozen(self):
        here = os.path.dirname(os.path.abspath(__file__))
        freeze = json.load(open(os.path.join(here, 'freeze_hashes_v3.json')))
        for fname, key in [
            ('summarizer_prompt.txt', 'summarizer_prompt_80'),
            ('summarizer_prompt_250.txt', 'summarizer_prompt_250'),
            ('summarizer_prompt_1k.txt', 'summarizer_prompt_1k'),
            ('summarizer_structured_prompt.txt', 'summarizer_structured_prompt'),
            ('oracle_faithful_style.txt', 'oracle_faithful_style'),
        ]:
            h = hashlib.sha256(open(P.prompt(fname), 'rb').read()).hexdigest()
            self.assertEqual(h, freeze[key], key)


if __name__ == '__main__':
    unittest.main()
