"""Offline tests for the shared harness. No key, no calls."""
import os
import tempfile
import unittest

import harness as H
import law_inputs as LI


class Stop(Exception):
    pass


class TestRunJobs(unittest.TestCase):
    def test_runs_every_job(self):
        rows, rep = H.run_jobs(list(range(10)), lambda j: {'j': j}, str, 'unused', write=False, workers=3)
        self.assertEqual(sorted(r['j'] for r in rows), list(range(10)))
        self.assertEqual((rep.done, rep.planned, rep.failed, rep.stopped), (10, 10, 0, None))

    def test_budget_stop_keeps_finished_work(self):
        calls = {'n': 0}

        def run_one(j):
            calls['n'] += 1
            if calls['n'] > 20:
                raise Stop('cap')
            return {'j': j}
        rows, rep = H.run_jobs(list(range(100)), run_one, str, 'unused', write=False, workers=4, stop_on=(Stop,))
        self.assertLessEqual(len(rows), 20)
        self.assertTrue(rep.stopped.startswith('Stop'))
        self.assertEqual(rep.failed, 0)

    def test_a_failed_job_is_counted_not_fatal(self):
        def run_one(j):
            if j == 3:
                raise RuntimeError('bad job')
            return {'j': j}
        rows, rep = H.run_jobs(list(range(5)), run_one, str, 'unused', write=False)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rep.failed, 1)

    def test_resume_loads_existing_traces(self):
        with tempfile.TemporaryDirectory() as d:
            H.run_jobs([1, 2], lambda j: {'j': j}, lambda j: 'X__%d' % j, d)
            ran = []
            rows, _ = H.run_jobs([1, 2, 3], lambda j: ran.append(j) or {'j': j}, lambda j: 'X__%d' % j, d)
            self.assertEqual(ran, [3])
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(H.load_traces(d, 'X')), 3)


class TestHelpers(unittest.TestCase):
    def test_seed_is_stable(self):
        self.assertEqual(H.seed_of('a', 1), H.seed_of('a', 1))
        self.assertNotEqual(H.seed_of('a', 1), H.seed_of('a', 2))

    def test_mock_never_outputs_braces_or_the_pin(self):
        cell = LI.make_item('trip', 100, 'E1')
        text = LI.relay_input(cell, 'S', 'F1')
        out, meta = H.complete(H.MockLLM('m'), LI.messages_for('relay', 'F1', text), 1)
        self.assertNotIn('{', out)
        self.assertNotIn('Pinned identifiers', out)
        self.assertTrue(meta['mocked'])

    def test_write_predictions_returns_the_file_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'p.json')
            sha = H.write_predictions(path, {'b': 1, 'a': 2})
            self.assertEqual(sha, hashlib.sha256(open(path, 'rb').read()).hexdigest())

    def test_paid_session_refuses_without_go_file(self):
        with self.assertRaises(SystemExit):
            H.PaidSession('/nonexistent/GO.txt', H.MODELS)


if __name__ == '__main__':
    unittest.main()
