"""Unit tests for the reviewer-mandated re-screen: summary checks, slots, oracle, prompts."""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as A
import handoff_summary as HS
import heldout as H
import run_pilot as R


def _cell(task='A', seed=0, name='C0', arm='P_full', mode='short'):
    c = H.make_any(task, seed, name)
    c['task'] = task
    c['arm'] = arm
    c['handoff_mode'] = mode
    c['request'] = c.get('request') or ''
    return c


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
        raw = self.replies.pop(0)
        return raw, {'tin': 10, 'tout': 20, 'cost': 0.0, 'latency_s': 0.01,
                     'retries': 0, 'finish': 'stop', 'message_keys': ['content', 'role']}


class TestSummaryChecks(unittest.TestCase):
    def test_empty_fails(self):
        ok, reason, d = HS.check_summary('', [('email', 'a@b.c')])
        self.assertFalse(ok)
        self.assertEqual(reason, 'empty')

    def test_json_fails_even_if_long(self):
        words = ' '.join(['word'] * 50)
        text = '{"plan": "%s"}' % words
        ok, reason, d = HS.check_summary(text, [])
        self.assertFalse(ok)
        self.assertEqual(reason, 'not_plain_text')
        self.assertFalse(HS.is_plain_text('{"plan": "1'))
        self.assertFalse(HS.is_plain_text('{'))

    def test_word_bounds(self):
        short = ' '.join(['word'] * 39)
        ok, reason, d = HS.check_summary(short, [])
        self.assertFalse(ok)
        self.assertEqual(reason, 'too_short')
        mid = ' '.join(['word'] * 40)
        ok, reason, d = HS.check_summary(mid, [])
        self.assertTrue(ok)
        long = ' '.join(['word'] * 101)
        ok, reason, d = HS.check_summary(long, [])
        self.assertFalse(ok)
        self.assertEqual(reason, 'too_long')

    def test_missing_slots(self):
        body = ' '.join(['word'] * 40) + ' email a@b.c flight FL1'
        ok, reason, d = HS.check_summary(body, [('email', 'a@b.c'), ('flight_id', 'FL1'), ('hotel_id', 'H9')])
        self.assertFalse(ok)
        self.assertEqual(reason, 'missing_slots')
        self.assertEqual(d['missing'], [('hotel_id', 'H9')])

    def test_nights_uses_word_boundary(self):
        # "4" must not match the 4 in a date like 2026-12-04
        body = 'Book on 2026-12-04. ' + ' '.join(['word'] * 40)
        miss = HS.missing_slots(body, [('nights', '4')])
        self.assertEqual(miss, [('nights', '4')])
        body2 = 'Stay 4 nights. ' + ' '.join(['word'] * 40)
        self.assertEqual(HS.missing_slots(body2, [('nights', '4')]), [])


class TestSlotExtraction(unittest.TestCase):
    def test_required_only_if_in_source(self):
        cell = _cell('A', 0, 'C0')
        slots = HS.candidate_slots(cell)
        self.assertIn('FL599', slots['flight_id'])
        # source without the id does not require it
        need = HS.required_slot_values(cell, 'email avery.silva55@campusmail.example and nothing else')
        keys = [k for k, v in need]
        self.assertIn('email', keys)
        self.assertNotIn('flight_id', keys)

    def test_required_when_source_has_values(self):
        cell = _cell('A', 0, 'C0')
        src = 'Book FL599 and H475 for 4 nights, email avery.silva55@campusmail.example on Pacific Air'
        need = HS.required_slot_values(cell, src)
        keys = sorted({k for k, v in need})
        self.assertEqual(keys, ['airline', 'email', 'flight_id', 'hotel_id', 'nights'])


class TestOracleAndPrompts(unittest.TestCase):
    def test_oracle_valid_for_seeds(self):
        for task in 'AB':
            for seed in (0, 1, 2):
                cell = _cell(task, seed, 'C0')
                text = HS.fill_oracle(cell, [{'field': 'note', 'reason': 'check'}])
                need = [(k, v) for k, vs in HS.candidate_slots(cell).items() for v in vs]
                ok, reason, d = HS.check_summary(text, need)
                self.assertTrue(ok, (task, seed, reason, d, text))
                self.assertGreaterEqual(d['words'], 40)
                self.assertLessEqual(d['words'], 100)
                self.assertTrue(HS.is_plain_text(text))

    def test_prompts_are_frozen(self):
        naive = HS.load_prompt_naive()
        struct = HS.load_prompt_structured()
        self.assertIn('plain text', naive.lower())
        self.assertIn('40 and 100', naive)
        self.assertNotIn('{', naive.split('Handoff:')[0])  # prompt body has no JSON object
        self.assertIn('must keep every call argument', struct.lower())
        self.assertIn('must keep every planner flag', struct.lower())
        self.assertIn('plain text', struct.lower())
        # sha256 recorded beside the files
        freeze = json.load(open(os.path.join(os.path.dirname(__file__), 'freeze_hashes_rescreen_nov21.json')))
        for fname, key in [
            ('summarizer_prompt.txt', 'summarizer_prompt'),
            ('summarizer_structured_prompt.txt', 'summarizer_structured_prompt'),
            ('oracle_summary_A.txt', 'oracle_summary_A'),
            ('oracle_summary_B.txt', 'oracle_summary_B'),
        ]:
            path = os.path.join(os.path.dirname(__file__), fname)
            h = hashlib.sha256(open(path, 'rb').read()).hexdigest()
            self.assertEqual(h, freeze[key], key)

    def test_ask_kind_split(self):
        self.assertEqual(HS.ask_kind({'summary_valid': False, 'status': 'invalid_summary'}), 'invalid_summary')
        self.assertEqual(HS.ask_kind({'status': 'ok', 'ended_by': 'ask_user'}), 'ask')
        self.assertEqual(HS.ask_kind({'status': 'ok', 'ended_by': 'hold'}), 'hold_without_ask')
        self.assertEqual(HS.ask_kind({'status': 'harness_error', 'ended_by': None}), 'malformed_or_failed')
        self.assertEqual(HS.ask_kind({'status': 'ok', 'ended_by': None}), 'done')


class TestPrepareHandoff(unittest.TestCase):
    def test_short_mode_unchanged_and_no_llm_call(self):
        cell = _cell('A', 0, 'C0', mode='short')
        llm = ScriptedLLM(['should not be called'])
        trace = {'calls': []}
        plan = {'plan': 'book the usual trip'}
        h = A.prepare_executor_handoff(cell, plan, [], [], llm, trace)
        self.assertIn('Planner output:', h)
        self.assertFalse(trace.get('handoff_compressed'))
        self.assertIsNone(trace.get('summary_valid'))
        self.assertEqual(llm.seen, [])

    def test_summarizer_uses_plain_text_mode_and_retries(self):
        cell = _cell('A', 0, 'C0', mode='compress')
        slots = HS.candidate_slots(cell)
        # first reply empty, second a valid 80-word summary with all slots
        good = (
            'Book flight %s on %s and hotel %s for %s nights. '
            'Email the itinerary to %s. Use the profile for contact and budget. '
            'If memory conflicts with the profile, follow the profile. '
            'Do not invent identifiers. Confirm the flight and hotel before you book. '
            'Keep every value from the plan. Ask only if a required value is still missing after this handoff.'
            % (slots['flight_id'][0], slots['airline'][0], slots['hotel_id'][0],
               slots['nights'][0], slots['email'][0])
        )
        # pad to 40+ words if needed (template is already long)
        llm = ScriptedLLM(['', good])
        source_vals = ' '.join([slots['flight_id'][0], slots['hotel_id'][0], str(slots['nights'][0]),
                                slots['email'][0], slots['airline'][0]])
        plan = {'plan': 'use ' + source_vals}
        results = [{'tool': 'search_flights', 'result': source_vals}]
        # planner results may be filtered; put values in the plan
        trace = {'calls': []}
        h = A.prepare_executor_handoff(cell, plan, results, [], llm, trace)
        self.assertEqual(len(llm.seen), 2)
        self.assertTrue(all(k['json_object'] is False for k in llm.kwargs))
        self.assertTrue(trace['summary_valid'])
        self.assertEqual(trace['summary_attempts'], 2)
        self.assertIn(slots['flight_id'][0], h)
        self.assertTrue(trace['slots_complete'])
        self.assertEqual(trace['summarizer_calls'][0]['raw_response'], '')
        self.assertEqual(trace['summarizer_calls'][1]['raw_response'].strip(), good.strip())

    def test_json_then_json_is_invalid(self):
        cell = _cell('A', 0, 'C0', mode='compress')
        plan = {'plan': 'book FL599 H475 4 nights avery.silva55@campusmail.example Pacific Air'}
        bad = '{"plan": "' + ' word' * 50 + '"}'
        llm = ScriptedLLM([bad, bad])
        trace = {'calls': []}
        A.prepare_executor_handoff(cell, plan, [], [], llm, trace)
        self.assertFalse(trace['summary_valid'])
        self.assertEqual(trace['summary_invalid_reason'], 'not_plain_text')
        self.assertEqual(trace['summary_attempts'], 2)

    def test_oracle_mode_does_not_call_llm(self):
        cell = _cell('B', 0, 'C0', mode='oracle')
        llm = ScriptedLLM(['nope'])
        trace = {'calls': []}
        h = A.prepare_executor_handoff(cell, {'plan': 'clean'}, [], [], llm, trace)
        self.assertEqual(llm.seen, [])
        self.assertTrue(trace['summary_valid'])
        self.assertIn('quinn.lee70@campusmail.example', h)
        self.assertIn('logs/audit/', h)
        self.assertGreaterEqual(HS.word_count(trace['handoff_summary']), 40)

    def test_keep_flags_appended(self):
        cell = _cell('A', 0, 'C0', mode='compress_keep_flags')
        slots = HS.candidate_slots(cell)
        good = (
            'Book flight %s on %s and hotel %s for %s nights and email %s. '
            % (slots['flight_id'][0], slots['airline'][0], slots['hotel_id'][0],
               slots['nights'][0], slots['email'][0])
            + ' '.join(['Keep'] * 30)
        )
        plan = {'plan': 'use %s %s %s %s %s' % (slots['flight_id'][0], slots['hotel_id'][0],
                                                slots['nights'][0], slots['email'][0], slots['airline'][0])}
        flags = [{'field': 'nights', 'reason': 'memory conflict'}]
        llm = ScriptedLLM([good])
        trace = {'calls': []}
        h = A.prepare_executor_handoff(cell, plan, [], flags, llm, trace)
        self.assertTrue(trace['summary_valid'])
        self.assertTrue(trace['flag_text_in_handoff'])
        self.assertIn('nights', h)
        self.assertIn('memory conflict', h)

    def test_complete_signature_accepts_json_object(self):
        # Default remains JSON mode for planner/executor calls.
        import inspect
        sig = inspect.signature(A.LLM.complete)
        self.assertIn('json_object', sig.parameters)
        self.assertTrue(sig.parameters['json_object'].default)


class TestRequireRescreenGo(unittest.TestCase):
    def test_rescreen_refuses_without_go(self,):
        env = {'PILOT_RESCREEN': '1'}
        # temporarily hide the file if present by passing a missing path: test the error when file missing
        # We cannot delete the real GO if we later write it. Patch the constant.
        old = R.RESCREEN_NOV21_GO
        try:
            R.RESCREEN_NOV21_GO = os.path.join(A.HERE, 'approvals', 'RESCREEN_NOV21_GO_MISSING.txt')
            with self.assertRaises(SystemExit) as cm:
                R.require_go(stop_at=2.67, n_jobs=4, env=env)
            self.assertIn('RESCREEN_NOV21_GO', str(cm.exception))
        finally:
            R.RESCREEN_NOV21_GO = old

    def test_rescreen_job_cap(self):
        env = {'PILOT_RESCREEN': '1'}
        # requires the GO file to exist; if not yet written, skip cap by writing a temp
        path = os.path.join(A.HERE, 'approvals', 'RESCREEN_NOV21_GO.txt')
        # If missing, the missing-go test already covers refusal. Only test cap when present.
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with self.assertRaises(SystemExit) as cm:
                R.require_go(stop_at=2.67, n_jobs=61, env=env)
            self.assertIn('cannot run 61', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
