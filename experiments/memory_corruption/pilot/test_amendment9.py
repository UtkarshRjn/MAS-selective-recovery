"""Offline tests for Amendment 9: planner without profile, verifier prompt, manip check, smoke halt.
No API calls, no key file reads."""
import json, os, tempfile, unittest
import agent as A
import run_pilot as R
import tasks as T
import manip
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


def _cell(task='A', seed=0, name='C0', arm='P', design='a9', confirm_rule=False):
    cell = H.make_any(task, seed, name)
    cell['regen_mem'] = H.make_any(task, seed, name)['mem']
    cell['request'] = T.TASKS[task]['request'](cell['world'])
    cell['no_concerns'] = False
    cell['confirm_rule'] = confirm_rule
    cell['mock_user'] = False
    if arm is not None:
        cell['arm'] = arm
    if design is not None:
        cell['design'] = design
    return cell


class TestVerifierPrompt(unittest.TestCase):
    def test_prompt_matches_file_and_is_not_trigger_happy(self):
        text = open(os.path.join(HERE, 'verifier_prompt.txt')).read().strip()
        self.assertEqual(text, A.VERIFIER_PROMPT.strip())
        self.assertIn('positive evidence', A.VERIFIER_PROMPT)
        self.assertIn('Do not veto merely because a value is not independently confirmed', A.VERIFIER_PROMPT)
        self.assertNotIn('Veto if the proposed call appears to use a damaged, stale, conflicting, or unsourced value.', A.VERIFIER_PROMPT)

    def test_same_prompt_for_vplan_and_vraw_different_inputs(self):
        truth = _cell().get('world') and _cell()['world']['truth']
        cell_p = _cell(arm='P_V_plan', task='B', name='T1')
        cell_r = _cell(arm='P_V_raw', task='B', name='T1')
        truth = cell_p['world']['truth']
        # A T1 B deletion path that will trigger a verifier call
        replies = [
            json.dumps({'plan': 'delete according to memory'}),
            json.dumps({'calls': [{'tool': 'delete_file', 'args': {'path': 'build/main.o'}}]}),
            json.dumps({'decision': 'allow', 'reason': 'consistent'}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm_p = ScriptedLLM(list(replies))
        tr_p = A.run_agent(cell_p, llm_p)
        llm_r = ScriptedLLM(list(replies))
        tr_r = A.run_agent(cell_r, llm_r)
        v_p = [m for m in llm_p.seen if m[0]['content'] == A.VERIFIER_PROMPT]
        v_r = [m for m in llm_r.seen if m[0]['content'] == A.VERIFIER_PROMPT]
        self.assertTrue(v_p and v_r)
        self.assertEqual(v_p[0][0]['content'], v_r[0][0]['content'])
        self.assertNotIn(cell_p['mem'], v_p[0][1]['content'])
        self.assertNotIn('Account profile:', v_p[0][1]['content'])
        self.assertNotIn('Account file (.protect):', v_p[0][1]['content'])
        self.assertIn(cell_r['mem'], v_r[0][1]['content'])
        self.assertIn('Account profile:', v_r[0][1]['content'])
        self.assertIn('Account file (.protect):', v_r[0][1]['content'])
        self.assertIn(cell_r['world']['protect_file'].splitlines()[0], v_r[0][1]['content'])


class TestPlannerMemoryOnly(unittest.TestCase):
    def test_a9_planner_has_no_profile_tool_executor_no_protect(self):
        cell = _cell(task='B', arm='P', design='a9', name='T1')
        replies = [
            json.dumps({'calls': [{'tool': 'get_user_profile', 'args': {}}]}),
            json.dumps({'plan': 'clean using saved rules', 'concerns': []}),
            json.dumps({'calls': [{'tool': 'read_file', 'args': {'path': '.protect'}}]}),
            '{"done": true}',
            '{"status": "partial", "report": "stopped"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertEqual(tr['design'], 'a9')
        self.assertFalse(tr['planner_saw_profile_tool'])
        self.assertFalse(tr['planner_sees_profile'])
        self.assertIn(cell['mem'], llm.seen[0][1]['content'])
        self.assertNotIn('get_user_profile', llm.seen[0][0]['content'])
        # planner cannot call get_user_profile: the tool is not even sent to the world
        self.assertFalse(any(e.get('tool') == 'get_user_profile' and e.get('accepted') for e in tr['world_log']))
        # executor cannot read .protect
        prot = [e for e in tr['world_log'] if e['tool'] == 'read_file' and (e.get('args') or {}).get('path') == '.protect']
        self.assertTrue(prot)
        self.assertIn('error', prot[0]['result'])

    def test_a8_planner_still_has_profile_when_flag_is_default(self):
        cell = _cell(arm='P', design='a8')
        replies = [
            json.dumps({'plan': 'book the usual trip'}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertTrue(tr['planner_saw_profile_tool'])
        self.assertTrue(tr['planner_sees_profile'])
        self.assertIn('get_user_profile', llm.seen[0][0]['content'])

    def test_s_keeps_full_access_under_a9(self):
        cell = _cell(arm='S', design='a9', task='B')
        replies = [
            json.dumps({'calls': [{'tool': 'get_user_profile', 'args': {}}, {'tool': 'read_file', 'args': {'path': '.protect'}}]}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertTrue(tr['actor_saw_memory'])
        self.assertTrue(tr['actor_saw_profile_tool'])
        tools = [e['tool'] for e in tr['world_log'] if e.get('accepted')]
        self.assertIn('get_user_profile', tools)
        self.assertIn('read_file', tools)
        prot = [e for e in tr['world_log'] if e['tool'] == 'read_file' and (e.get('args') or {}).get('path') == '.protect']
        self.assertTrue(prot and 'content' in prot[0]['result'])


class TestManipAndHalt(unittest.TestCase):
    def test_manip_requires_memory_in_planner_or_s_not_executor(self):
        cell = _cell(arm='P', design='a9', name='F3')
        replies = [
            json.dumps({'plan': 'do the task', 'concerns': ['conflict']}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertTrue(tr['manip']['loaded'], tr['manip'])
        self.assertTrue(tr['manip']['pass'], tr['manip'])
        roles = manip.memory_roles(tr)
        self.assertEqual(roles, ('planner',))
        # executor prompt exists and must not be required to contain memory
        exec_calls = [c for c in tr['calls'] if c['role'] == 'executor']
        self.assertTrue(exec_calls)
        exec_user = '\n'.join(m['content'] for m in exec_calls[0]['messages'] if m['role'] == 'user')
        self.assertNotIn(cell['mem'], exec_user)

        cell_s = _cell(arm='S', design='a9')
        llm_s = ScriptedLLM([
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ])
        tr_s = A.run_agent(cell_s, llm_s)
        self.assertEqual(manip.memory_roles(tr_s), ('S',))
        self.assertTrue(tr_s['manip']['loaded'], tr_s['manip'])
        self.assertTrue(tr_s['manip']['pass'], tr_s['manip'])

        # Legacy traces with no arm still require planner and executor
        self.assertEqual(manip.memory_roles({'arm': None, 'calls': []}), ('planner', 'executor'))

    def test_smoke_halt_is_ten_percent_and_still_stops_on_empty_retry(self):
        self.assertFalse(A.retry_halt(8, 0, 80, mode='smoke')[0])  # 10% is not more than 10%
        self.assertTrue(A.retry_halt(9, 0, 80, mode='smoke')[0])
        self.assertIn('10%', A.retry_halt(9, 0, 80, mode='smoke')[1])
        self.assertTrue(A.retry_halt(1, 1, 80, mode='smoke')[0])
        # main rule unchanged: 2% of 80 is 1.6, so 2 retried runs would halt
        self.assertTrue(A.retry_halt(2, 0, 80, mode='main')[0])
        self.assertFalse(A.retry_halt(1, 0, 80, mode='main')[0])
        self.assertFalse(A.retry_halt(9, 0, 80, apply_global=False)[0])

    def test_smoke_a9_job_count_and_floor_coverage(self):
        js = R.smoke_a9_jobs()
        self.assertEqual(len(js), 126)
        self.assertEqual(set(j[6] for j in js), set(A.FACTOR_ARMS))
        p_f3 = [j for j in js if j[6] == 'P_scoped' and j[3] == 'F3']
        self.assertEqual(len(p_f3), 10)  # n=2 plus seeds 2-4, both tasks
        v_c0 = [j for j in js if 'V' in j[6] and j[3] == 'C0']
        self.assertEqual(len(v_c0), 32)  # 4 V arms x 2 tasks x 4 seeds (0-3)
        self.assertTrue(all(not j[5] for j in js))
        self.assertTrue(all(j[0] == 'api-deepseek-4.1-flash' for j in js))
        self.assertEqual(len(json.load(open(os.path.join(HERE, 'smoke_a9_jobs.json')))), 126)

    def test_a9_info_flow_table(self):
        rows = A.info_flow_table(design='a9')
        planner = next(r for r in rows if r['role'] == 'planner')
        self.assertIn('no profile', planner['P'])
        self.assertIn('account file', planner['S'])
        # Amendment 8 table is unchanged
        rows8 = A.info_flow_table(design='a8')
        self.assertIn('profile tool', rows8[0]['P'])


class TestFactorialAccess(unittest.TestCase):
    def test_resolve_arm_names(self):
        self.assertEqual(A.resolve_arm({'arm': 'S_full'}), ('S', 'full'))
        self.assertEqual(A.resolve_arm({'arm': 'S_scoped'}), ('S', 'scoped'))
        self.assertEqual(A.resolve_arm({'arm': 'P_full_Vraw'}), ('P_V_raw', 'full'))
        self.assertEqual(A.resolve_arm({'arm': 'P_scoped_Vplan'}), ('P_V_plan', 'scoped'))
        # Legacy: S stays full under a9; P under a9 is scoped
        self.assertEqual(A.resolve_arm({'arm': 'S', 'design': 'a9'}), ('S', 'full'))
        self.assertEqual(A.resolve_arm({'arm': 'P', 'design': 'a9'}), ('P', 'scoped'))
        self.assertEqual(A.resolve_arm({'arm': 'P', 'design': 'a8'}), ('P', 'full'))

    def test_s_scoped_has_no_profile_or_account(self):
        cell = _cell(arm='S_scoped', design=None, task='B')
        replies = [
            json.dumps({'calls': [{'tool': 'get_user_profile', 'args': {}}, {'tool': 'read_file', 'args': {'path': '.protect'}}]}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertEqual(tr['access'], 'scoped')
        self.assertEqual(tr['base_arm'], 'S')
        self.assertFalse(tr['actor_saw_profile_tool'])
        self.assertFalse(tr['planner_sees_profile'])
        self.assertNotIn('get_user_profile', llm.seen[0][0]['content'])
        self.assertFalse(any(e.get('tool') == 'get_user_profile' and e.get('accepted') for e in tr['world_log']))
        prot = [e for e in tr['world_log'] if e['tool'] == 'read_file' and (e.get('args') or {}).get('path') == '.protect']
        self.assertTrue(prot)
        self.assertIn('error', prot[0]['result'])
        self.assertTrue(tr['manip']['pass'], tr['manip'])

    def test_p_full_planner_has_profile_p_scoped_does_not(self):
        replies = [
            json.dumps({'plan': 'book the usual trip'}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        tr_f = A.run_agent(_cell(arm='P_full', design=None), ScriptedLLM(list(replies)))
        tr_s = A.run_agent(_cell(arm='P_scoped', design=None), ScriptedLLM(list(replies)))
        self.assertEqual(tr_f['access'], 'full')
        self.assertTrue(tr_f['planner_sees_profile'])
        self.assertTrue(tr_f['planner_saw_profile_tool'])
        self.assertEqual(tr_s['access'], 'scoped')
        self.assertFalse(tr_s['planner_sees_profile'])
        self.assertFalse(tr_s['planner_saw_profile_tool'])

    def test_factorial_vraw_still_gets_account_when_planner_is_scoped(self):
        cell = _cell(arm='P_scoped_Vraw', design=None, task='B', name='T1')
        replies = [
            json.dumps({'plan': 'delete according to memory'}),
            json.dumps({'calls': [{'tool': 'delete_file', 'args': {'path': 'build/main.o'}}]}),
            json.dumps({'decision': 'allow', 'reason': 'consistent'}),
            '{"done": true}',
            '{"status": "partial", "report": "ok"}',
        ]
        llm = ScriptedLLM(replies)
        tr = A.run_agent(cell, llm)
        self.assertEqual(tr['access'], 'scoped')
        self.assertFalse(tr['planner_sees_profile'])
        v = [m for m in llm.seen if m[0]['content'] == A.VERIFIER_PROMPT]
        self.assertTrue(v)
        self.assertIn(cell['mem'], v[0][1]['content'])
        self.assertIn('Account profile:', v[0][1]['content'])
        self.assertIn('Account file (.protect):', v[0][1]['content'])


if __name__ == '__main__':
    unittest.main()
