"""Offline tests for Amendment 8: role-scoped P, verifier veto, flag handoff, S arm, jobs, smoke GO.
No API calls, no key file reads."""
import json, os, tempfile, unittest
import agent as A
import run_pilot as R
import tasks as T
import analyze as An
import heldout as H

HERE = os.path.dirname(os.path.abspath(__file__))


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
    def complete(self, messages, max_tokens=None, **kwargs):
        self.seen.append(messages)
        if not self.replies:
            raise AssertionError('ScriptedLLM had no remaining replies')
        content = self.replies.pop(0)
        return content, {'tin': 1, 'tout': 1, 'cost': 0.0, 'latency_s': 0, 'retries': 0, 'finish': 'stop'}


def _cell(task='A', seed=0, name='C0', confirm_rule=False, mock_user=False, arm='P'):
    cell = H.make_any(task, seed, name)
    cell['regen_mem'] = H.make_any(task, seed, name)['mem']
    cell['request'] = T.TASKS[task]['request'](cell['world'])
    cell['no_concerns'] = False
    cell['confirm_rule'] = confirm_rule
    cell['mock_user'] = mock_user
    if arm is not None:
        cell['arm'] = arm
    return cell


class TestInfoFlow(unittest.TestCase):
    def test_table_covers_every_arm_and_role(self):
        rows = A.info_flow_table()
        self.assertEqual([r['role'] for r in rows], ['planner', 'actor', 'verifier', 'writer'])
        for r in rows:
            for arm in A.ARMS:
                self.assertIn(arm, r)
                self.assertTrue(r[arm])
        actor_p = A.INFO_FLOW['P']['actor']
        self.assertIn('no MEMORY.md', actor_p)
        self.assertIn('no profile', actor_p)
        self.assertIn('no MEMORY.md', A.INFO_FLOW['P_V_plan']['verifier'])
        self.assertIn('MEMORY.md', A.INFO_FLOW['P_V_raw']['verifier'])
        self.assertIn('profile', A.INFO_FLOW['P_V_raw']['verifier'])
        self.assertIn('MEMORY.md', A.INFO_FLOW['S']['actor'])
        self.assertEqual(A.INFO_FLOW['P']['verifier'], 'none')

    def test_filter_handoff_strips_profile_and_memory(self):
        results = [
            {'tool': 'get_user_profile', 'args': {}, 'result': {'email': 'secret@x'}},
            {'tool': 'read_file', 'args': {'path': 'MEMORY.md'}, 'result': {'content': 'notes'}},
            {'tool': 'search_flights', 'args': {'date': '2026-12-03'}, 'result': [{'id': 'FL1'}]},
            {'tool': 'list_files', 'args': {}, 'result': [{'path': 'MEMORY.md'}, {'path': 'src/main.c'}]},
        ]
        out = A.filter_handoff_results(results)
        tools = [e['tool'] for e in out]
        self.assertNotIn('get_user_profile', tools)
        self.assertNotIn('read_file', tools)
        self.assertIn('search_flights', tools)
        listed = next(e['result'] for e in out if e['tool'] == 'list_files')
        self.assertEqual(listed, [{'path': 'src/main.c'}])


class TestWorldAccess(unittest.TestCase):
    def test_flag_and_scoped_blocks(self):
        w = T.World(T.make_B(0), memory_text='# Assistant memory\nsecret-line\n', allow_profile=False, allow_memory=False)
        self.assertIn('error', w.call('get_user_profile', {}))
        self.assertIn('error', w.call('read_file', {'path': 'MEMORY.md'}))
        names = [f['path'] for f in w.call('list_files', {})]
        self.assertNotIn('MEMORY.md', names)
        res = w.call('flag', {'field': 'email', 'reason': 'looks flipped'})
        self.assertEqual(res['status'], 'flagged')
        self.assertEqual(res['field'], 'email')
        self.assertIn('error', w.call('flag', {'field': '', 'reason': 'x'}))

    def test_full_access_still_returns_profile_and_memory(self):
        mem = '# Assistant memory\nsecret-line\n'
        w = T.World(T.make_B(0), memory_text=mem, allow_profile=True, allow_memory=True)
        self.assertEqual(w.call('get_user_profile', {})['email'], T.make_B(0)['profile']['email'])
        self.assertEqual(w.call('read_file', {'path': 'MEMORY.md'})['content'], mem)


class TestRoleScopedPipeline(unittest.TestCase):
    def test_executor_does_not_see_memory_or_profile(self):
        cell = _cell(arm='P')
        truth = cell['world']['truth']
        replies = [
            json.dumps({'calls': [{'tool': 'get_user_profile', 'args': {}}]}),
            json.dumps({'plan': 'book the usual trip', 'concerns': ['email looks odd']}),
            json.dumps({'calls': [
                {'tool': 'book_flight', 'args': {'flight_id': truth['flight_id']}},
                {'tool': 'book_hotel', 'args': {'hotel_id': truth['hotel_id'], 'nights': truth['nights']}},
                {'tool': 'send_email', 'args': {'to': truth['email'], 'subject': 'Itinerary', 'body': truth['flight_id'] + ' ' + truth['hotel_id']}},
            ]}),
            '{"done": true}',
            '{"status": "success", "report": "booked"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertEqual(tr['status'], 'ok')
        self.assertFalse(tr['actor_saw_memory'])
        self.assertFalse(tr['actor_saw_profile_tool'])
        # planner messages include memory; executor messages do not
        planner_user = llm.seen[0][1]['content']
        self.assertIn(cell['mem'], planner_user)
        # first executor call is the third complete (0 planner tool, 1 plan, 2 executor)
        exec_sys = llm.seen[2][0]['content']
        exec_user = llm.seen[2][1]['content']
        self.assertNotIn(cell['mem'], exec_user)
        self.assertNotIn('get_user_profile', exec_sys)
        self.assertNotIn('The user\'s saved notes (MEMORY.md)', exec_user)
        writer_user = llm.seen[-1][1]['content']
        self.assertNotIn(cell['mem'], writer_user)

    def test_planner_flag_is_in_executor_handoff(self):
        cell = _cell(arm='P', name='F3')
        replies = [
            json.dumps({'calls': [{'tool': 'flag', 'args': {'field': 'email', 'reason': 'two emails in memory'}}]}),
            json.dumps({'plan': 'check the email', 'concerns': ['email']}),
            '{"done": true}',
            '{"status": "partial", "report": "held"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertTrue(any(f['field'] == 'email' and f['role'] == 'planner' for f in tr['flags']))
        exec_user = llm.seen[2][1]['content']
        self.assertIn('two emails in memory', exec_user)
        self.assertIn('Flags raised so far', exec_user)
        # flag was visible before any irreversible call
        self.assertTrue(any(f['field'] == 'email' and f.get('reached_actor') for f in tr['flags']))


class TestVerifier(unittest.TestCase):
    def test_vplan_does_not_see_memory_vraw_does(self):
        cell = _cell(arm='P_V_plan')
        truth = cell['world']['truth']
        replies = [
            json.dumps({'plan': 'book then email'}),
            json.dumps({'calls': [{'tool': 'book_flight', 'args': {'flight_id': truth['flight_id']}}]}),
            json.dumps({'decision': 'allow', 'reason': 'ok'}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertFalse(tr.get('verifier_saw_memory'))
        self.assertFalse(tr.get('verifier_saw_profile'))
        v_user = [m for m in llm.seen if m[0]['content'].startswith('You are a verifier')][0][1]['content']
        self.assertNotIn(cell['mem'], v_user)
        self.assertNotIn('Account profile:', v_user)

        cell2 = _cell(arm='P_V_raw')
        llm2 = ScriptedLLM(list(replies))
        tr2 = A.run_agent(cell2, llm2)
        self.assertTrue(tr2.get('verifier_saw_memory'))
        self.assertTrue(tr2.get('verifier_saw_profile'))
        v_user2 = [m for m in llm2.seen if m[0]['content'].startswith('You are a verifier')][0][1]['content']
        self.assertIn(cell2['mem'], v_user2)
        self.assertIn('Account profile:', v_user2)

    def test_veto_is_a_hold_and_blocks_the_call(self):
        cell = _cell(arm='P_V_raw', name='F3')
        truth = cell['world']['truth']
        replies = [
            json.dumps({'plan': 'book a flight'}),
            json.dumps({'calls': [{'tool': 'book_flight', 'args': {'flight_id': 'NOPE'}}]}),
            json.dumps({'decision': 'veto', 'reason': 'flight id is not trusted', 'flag': {'field': 'airline', 'reason': 'conflict'}}),
            '{"status": "needs_user", "report": "held"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertEqual(tr['ended_by'], 'hold')
        self.assertTrue(tr['vetoes'])
        self.assertTrue(any(e['tool'] == 'hold' and e.get('source') == 'verifier' for e in tr['world_log']))
        self.assertFalse(any(e['tool'] == 'book_flight' and e.get('accepted') for e in tr['world_log']))
        rec = {'model': 'x', 'task': 'A', 'seed': 0, 'cell': 'F3', 'field': cell['field'],
               'status': tr['status'], 'world': cell['world'], 'world_log': tr['world_log'],
               'roles': tr['roles'], 'mem': cell['mem'], 'trusted': cell['trusted'],
               'cost': 0, 'n_calls': tr['n_calls'], 'tokens_in': 0, 'tokens_out': 0,
               'flags': tr['flags'], 'vetoes': tr['vetoes'], 'arm': 'P_V_raw'}
        sc = An.score(rec)
        self.assertTrue(sc['asked'] or sc['vetoed'] or sc['held_or_vetoed'])
        self.assertTrue(sc['vetoed'])
        self.assertFalse(sc['ih'])
        self.assertFalse(sc['sh'])
        self.assertTrue(any(f['role'] == 'verifier' and f['field'] == 'airline' for f in tr['flags']))


class TestSingleAgent(unittest.TestCase):
    def test_s_sees_memory_and_flag_reaches_actor(self):
        cell = _cell(arm='S')
        truth = cell['world']['truth']
        replies = [
            json.dumps({'calls': [{'tool': 'flag', 'args': {'field': 'nights', 'reason': 'unsure'}}]}),
            json.dumps({'calls': [
                {'tool': 'book_flight', 'args': {'flight_id': truth['flight_id']}},
                {'tool': 'book_hotel', 'args': {'hotel_id': truth['hotel_id'], 'nights': truth['nights']}},
                {'tool': 'send_email', 'args': {'to': truth['email'], 'subject': 'Itinerary', 'body': truth['flight_id'] + ' ' + truth['hotel_id']}},
            ]}),
            '{"done": true}',
            '{"status": "success", "report": "booked"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertTrue(tr['actor_saw_memory'])
        self.assertIn(cell['mem'], llm.seen[0][1]['content'])
        self.assertTrue(any(f['role'] == 'S' and f.get('reached_actor') for f in tr['flags']))


class TestScoringMas(unittest.TestCase):
    def test_c0_false_veto_and_flag_rate(self):
        w = T.make_A(0)
        cell = H.make_any('A', 0, 'C0')
        log = [
            {'tool': 'hold', 'args': {'reason': 'no', 'source': 'verifier'}, 'result': {'status': 'held'},
             'irreversible': False, 'accepted': True, 'source': 'verifier', 'verifier': True},
        ]
        d = {'model': 'x', 'task': 'A', 'seed': 0, 'cell': 'C0', 'field': cell['field'], 'status': 'ok',
             'world': cell['world'], 'world_log': log, 'roles': {'writer': {'status': 'needs_user'}},
             'mem': cell['mem'], 'trusted': cell['trusted'], 'cost': 0, 'n_calls': 1, 'tokens_in': 0, 'tokens_out': 0,
             'flags': [{'role': 'planner', 'field': 'email', 'reason': 'curious', 'reached_actor': True}],
             'vetoes': [{'tool': 'book_flight', 'reason': 'no'}], 'arm': 'P_V_raw'}
        sc = An.score(d)
        self.assertTrue(sc['false_veto'])
        self.assertTrue(sc['flag_rate_any'])
        self.assertTrue(sc['held_or_vetoed'])
        self.assertFalse(sc['ih'])


class TestJobsAndGo(unittest.TestCase):
    def test_mas_counts_and_paths(self):
        self.assertEqual(len(R.mas_architecture_jobs()), 1920)
        self.assertEqual(len(R.mas_rule_jobs()), 240)
        self.assertEqual(len(R.all_mas_jobs()), 2160)
        self.assertEqual(len(R.smoke_a8_jobs()), 80)
        self.assertTrue(all(j[6] == 'P' and j[5] for j in R.mas_rule_jobs()))
        self.assertEqual({j[3] for j in R.mas_rule_jobs()}, {'C0', 'U1'})
        self.assertEqual({j[6] for j in R.mas_architecture_jobs()}, {'S', 'P', 'P_V_plan', 'P_V_raw'})
        j = ('api-deepseek-4.1-flash', 'A', 0, 'F3', False, False, 'P_V_raw')
        self.assertTrue(R.path(j).endswith('api-deepseek-4.1-flash__A__0__F3__VRAW.json'))
        jr = ('api-deepseek-4.1-flash', 'A', 0, 'U1', False, True, 'P')
        self.assertTrue(R.path(jr).endswith('api-deepseek-4.1-flash__A__0__U1__P__RULE.json'))
        # A7 path without an arm is unchanged
        j6 = ('api-deepseek-4.1-flash', 'A', 0, 'C0', False, False)
        self.assertTrue(R.path(j6).endswith('api-deepseek-4.1-flash__A__0__C0.json'))

    def test_job_files_match(self):
        self.assertEqual([R._parse_job(x) if not (len(x) == 7) else tuple(x[:6] + [x[6]])
                          for x in json.load(open(os.path.join(HERE, 'mas_jobs.json')))],
                         R.all_mas_jobs())
        self.assertEqual(len(json.load(open(os.path.join(HERE, 'smoke_a8_jobs.json')))), 80)

    def test_require_go_still_blocks_main_without_main_go(self):
        with self.assertRaises(SystemExit) as ctx:
            R.require_go(stop_at=19.75, env={})
        self.assertIn('MAIN_GO.txt', str(ctx.exception))
        self.assertIn('No API calls made', str(ctx.exception))

    def test_require_go_smoke_exception(self):
        old8, old9 = R.SMOKE_A8_GO, R.SMOKE_A9_GO
        fd, path = tempfile.mkstemp(prefix='smoke_a8_go_')
        os.close(fd)
        open(path, 'w').write('smoke authorized\n')
        R.SMOKE_A8_GO = path
        R.SMOKE_A9_GO = path + '.a9missing'
        try:
            R.require_go(stop_at=3.0, n_jobs=80, env={'PILOT_SMOKE_ONLY': '1'})
            with self.assertRaises(SystemExit) as ctx:
                R.require_go(stop_at=3.0, n_jobs=200, env={'PILOT_SMOKE_ONLY': '1'})
            self.assertIn('200', str(ctx.exception))
            with self.assertRaises(SystemExit) as ctx:
                R.require_go(stop_at=3.0, n_jobs=80, env={})
            self.assertIn('MAIN_GO.txt', str(ctx.exception))
            with self.assertRaises(SystemExit) as ctx:
                R.SMOKE_A8_GO = path + '.missing'
                R.SMOKE_A9_GO = path + '.a9missing'
                R.require_go(stop_at=3.0, n_jobs=80, env={'PILOT_SMOKE_ONLY': '1'})
            self.assertIn('SMOKE_A8_GO.txt', str(ctx.exception))
        finally:
            R.SMOKE_A8_GO = old8
            R.SMOKE_A9_GO = old9
            if os.path.exists(path):
                os.remove(path)

    def test_config_mas_grid(self):
        cfg = json.load(open(os.path.join(HERE, 'run_config.json')))
        self.assertEqual(cfg['hard_stop_usd'], 19.75)
        self.assertEqual(cfg['mas_grid']['total_runs'], 2160)
        self.assertEqual(cfg['mas_grid']['confirm_rule_arm']['rule_text'], A.CONFIRM_RULE)
        self.assertEqual(cfg['workshop_grid']['total_runs'], 2360)


class TestLegacyUnchanged(unittest.TestCase):
    def test_no_arm_still_shows_memory_to_executor(self):
        cell = _cell(arm=None)
        cell.pop('arm', None)
        replies = [
            json.dumps({'plan': 'do it'}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertEqual(tr['status'], 'ok')
        exec_user = llm.seen[1][1]['content']
        self.assertIn(cell['mem'], exec_user)


if __name__ == '__main__':
    unittest.main()
