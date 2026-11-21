"""Unit tests for the workshop-tier hard stop (cap minus $0.25) and per-cell spend logging.
No API calls, no key file reads."""
import json, os, tempfile, unittest
import agent as A
import run_pilot as R

HERE = os.path.dirname(os.path.abspath(__file__))

class TestHardStop(unittest.TestCase):
    def test_config_value_is_19_75(self):
        cfg = json.load(open(os.path.join(HERE, 'run_config.json')))
        self.assertEqual(cfg['hard_stop_usd'], 19.75)
        self.assertEqual(cfg['cap_usd'], 20.0)
        self.assertAlmostEqual(cfg['hard_stop_usd'], cfg['cap_usd'] - 0.25)

    def test_load_hard_stop_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'run_config.json')
            json.dump({'hard_stop_usd': 19.75}, open(path, 'w'))
            self.assertEqual(A.load_hard_stop(path=path, env={}), 19.75)

    def test_env_overrides_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'run_config.json')
            json.dump({'hard_stop_usd': 19.75}, open(path, 'w'))
            self.assertEqual(A.load_hard_stop(path=path, env={'PILOT_STOP_AT': '0.80'}), 0.80)

    def test_missing_config_defaults_conservative(self):
        path = os.path.join(tempfile.gettempdir(), 'no-such-run-config-memory-corruption.json')
        if os.path.exists(path):
            os.remove(path)
        self.assertEqual(A.load_hard_stop(path=path, env={}), 0.90)

    def test_conservative_spend_is_max_of_token_and_server(self):
        self.assertEqual(A.conservative_spend(0.72, 0.54), 0.72)
        self.assertEqual(A.conservative_spend(0.50, 0.80), 0.80)
        self.assertEqual(A.conservative_spend(19.75, 19.75), 19.75)

    def test_over_budget_at_and_above_stop(self):
        self.assertFalse(A.over_budget(19.74, 19.00, 19.75))
        self.assertTrue(A.over_budget(19.75, 10.00, 19.75))
        self.assertTrue(A.over_budget(10.00, 19.75, 19.75))
        self.assertTrue(A.over_budget(20.00, 20.00, 19.75))

    def test_cell_spend_record_logs_per_cell_and_conservative_total(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'cell_spend.jsonl')
            rec = A.cell_spend_record(
                'api-deepseek-4.1-flash', 'A', 0, 'C0', 0.00331,
                token_spend_usd=0.720, server_delta_usd=0.535,
                hard_stop_usd=19.75, path=path,
            )
            self.assertEqual(rec['cell'], 'C0')
            self.assertEqual(rec['task'], 'A')
            self.assertEqual(rec['cost'], 0.00331)
            self.assertEqual(rec['token_spend_usd'], 0.72)
            self.assertEqual(rec['server_delta_usd'], 0.535)
            self.assertEqual(rec['conservative_spend_usd'], 0.72)
            self.assertEqual(rec['hard_stop_usd'], 19.75)
            lines = open(path).read().strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0]), rec)

    def test_require_go_blocks_workshop_stop_without_approval(self):
        with self.assertRaises(SystemExit) as ctx:
            R.require_go(stop_at=19.75)
        self.assertIn('19.75', str(ctx.exception))
        self.assertIn('MAIN_GO.txt', str(ctx.exception))
        self.assertIn('No API calls made', str(ctx.exception))

    def test_require_go_allows_pilot_stop(self):
        R.require_go(stop_at=0.80)
        R.require_go(stop_at=1.0)

class TestNoConcernsAndJobs(unittest.TestCase):
    def test_default_prompt_strings_unchanged(self):
        self.assertEqual(
            A.planner_ready_json(False),
            '{"plan": "step by step plan with the exact values to use", "concerns": ["anything that looks wrong or uncertain"]}.',
        )
        self.assertEqual(
            A.executor_done_json(False),
            '{"done": true, "concerns": ["anything that looked wrong or uncertain"]}.',
        )

    def test_no_concerns_prompt_omits_concerns_field(self):
        self.assertNotIn('concerns', A.planner_ready_json(True))
        self.assertNotIn('concerns', A.executor_done_json(True))
        self.assertIn('"plan"', A.planner_ready_json(True))
        self.assertIn('"done": true', A.executor_done_json(True))

    def test_parse_job_and_nc_path(self):
        j = R._parse_job(['api-deepseek-4.1-flash', 'A', 0, 'C0'])
        self.assertEqual(j, ('api-deepseek-4.1-flash', 'A', 0, 'C0', False, False))
        self.assertTrue(R.path(j).endswith('api-deepseek-4.1-flash__A__0__C0.json'))
        jn = R._parse_job(['api-deepseek-4.1-flash', 'A', 0, 'C0', 'NC'])
        self.assertEqual(jn[4], True)
        self.assertEqual(jn[5], False)
        self.assertTrue(R.path(jn).endswith('api-deepseek-4.1-flash__A__0__C0__NC.json'))
        self.assertNotEqual(R.path(j), R.path(jn))
        jr = R._parse_job(['api-deepseek-4.1-flash', 'A', 0, 'U1', 'RULE'])
        self.assertEqual(jr[4], False)
        self.assertEqual(jr[5], True)
        self.assertTrue(R.path(jr).endswith('api-deepseek-4.1-flash__A__0__U1__RULE.json'))


class TestLengthRetry(unittest.TestCase):
    def test_default_max_tokens_is_4000(self):
        self.assertEqual(A.DEFAULT_MAX_TOKENS, 4000)

    def test_needs_length_retry_only_for_empty_length(self):
        self.assertTrue(A.needs_length_retry('', 'length'))
        self.assertTrue(A.needs_length_retry('   ', 'length'))
        self.assertFalse(A.needs_length_retry('{"plan": "x"}', 'length'))
        self.assertFalse(A.needs_length_retry('', 'stop'))
        self.assertFalse(A.needs_length_retry('', None))

    def test_retry_halt_main_only(self):
        self.assertEqual(A.retry_halt(0, 1, 2320, apply_global=True)[0], True)
        self.assertEqual(A.retry_halt(47, 0, 2320, apply_global=True)[0], True)
        self.assertEqual(A.retry_halt(46, 0, 2320, apply_global=True)[0], False)
        self.assertEqual(A.retry_halt(47, 0, 2320, apply_global=False)[0], False)
        self.assertEqual(A.retry_halt(0, 1, 2, apply_global=False)[0], False)

    def test_cell_retry_bias_over_five_percent(self):
        flag, _ = A.cell_retry_bias(n_lost=2, n_retried=0, n_cell=40)
        self.assertFalse(flag)  # 2/40 = 5%, not more than 5%
        flag, _ = A.cell_retry_bias(n_lost=3, n_retried=0, n_cell=40)
        self.assertTrue(flag)  # 3/40 = 7.5%
        flag, _ = A.cell_retry_bias(n_lost=0, n_retried=8, n_cell=40, other_retry_rates=[0.01, 0.01, 0.02])
        self.assertTrue(flag)  # 20% vs ~1.3% mean



class TestAmendment6WorkshopGrid(unittest.TestCase):
    def test_config_grid_is_1680_kept_cells(self):
        cfg = json.load(open(os.path.join(HERE, 'run_config.json')))
        g = cfg['workshop_grid']
        self.assertEqual(g['g0_plus_nc_runs'], 1680)
        self.assertEqual(g['g0_runs'], 1480)
        self.assertEqual(g['no_concerns_runs'], 200)
        self.assertEqual(g['confirm_rule_runs'], 680)
        self.assertEqual(g['total_runs'], 2360)
        self.assertEqual(set(g['g0_cells_n']), {'C0', 'C2', 'F1', 'F2', 'F3', 'F4', 'T1', 'T2', 'U1'})
        self.assertEqual(set(g['cut_cells_not_run']), {'C1', 'P1', 'HO1', 'HO2'})
        self.assertEqual(g['g0_cells_n']['C0'], 50)
        self.assertEqual(g['g0_cells_n']['C2'], 40)

    def test_workshop_jobs_match_g0_jobs_file(self):
        js = R.workshop_jobs()
        self.assertEqual(len(js), 1680)
        self.assertEqual(sum(1 for j in js if j[4]), 200)
        self.assertEqual(sum(1 for j in js if not j[4]), 1480)
        self.assertEqual({j[3] for j in js}, {'C0', 'C2', 'F1', 'F2', 'F3', 'F4', 'T1', 'T2', 'U1'})
        self.assertTrue(all(not j[5] for j in js))
        gj = [R._parse_job(x) for x in json.load(open(os.path.join(HERE, 'g0_jobs.json')))]
        self.assertEqual(gj, js)
        self.assertTrue(all(j[3] not in {'C1', 'P1', 'HO1', 'HO2'} for j in js))

    def test_jobs_refuse_cut_cells_when_workshop_stop(self):
        path = os.path.join(HERE, 'main_jobs.json')
        old = os.environ.get('PILOT_JOBS')
        os.environ['PILOT_JOBS'] = path  # kept cells: must load fine even if STOP_AT > 1
        try:
            js = R.jobs()
            self.assertEqual(len(js), 2360)
            self.assertEqual(sum(1 for j in js if j[5]), 680)
        finally:
            if old is None:
                os.environ.pop('PILOT_JOBS', None)
            else:
                os.environ['PILOT_JOBS'] = old

    def test_jobs_refuse_a_cut_cell_list(self):
        # STOP_AT is 19.75 at import when no PILOT_STOP_AT is set.
        self.assertGreater(A.STOP_AT, 1.0)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'bad_jobs.json')
            json.dump([['api-deepseek-4.1-flash', 'A', 0, 'HO1']], open(path, 'w'))
            old = os.environ.get('PILOT_JOBS')
            os.environ['PILOT_JOBS'] = path
            try:
                with self.assertRaises(SystemExit) as ctx:
                    R.jobs()
                self.assertIn('Amendment 6', str(ctx.exception))
                self.assertIn('HO1', str(ctx.exception))
                self.assertIn('No API calls made', str(ctx.exception))
            finally:
                if old is None:
                    os.environ.pop('PILOT_JOBS', None)
                else:
                    os.environ['PILOT_JOBS'] = old

    def test_retry_halt_two_percent_on_1680(self):
        # 2% of 1,680 is 33.6, so 34 retried runs halt, 33 do not.
        self.assertEqual(A.retry_halt(34, 0, 1680, apply_global=True)[0], True)
        self.assertEqual(A.retry_halt(33, 0, 1680, apply_global=True)[0], False)
        self.assertEqual(A.retry_halt(0, 1, 1680, apply_global=True)[0], True)



if __name__ == '__main__':
    unittest.main()
