from threading import Event
import json
import pytest
from agentrec.conditions import attempt
from agentrec.llm import MockLLM
from agentrec.runner import Recorder
from agentrec.mbpp import TaskCancelled, execute_cases


@pytest.mark.parametrize('error', [TaskCancelled, KeyboardInterrupt, SystemExit])
def test_explicit_cancellation_is_not_recorded_as_condition_failure(tmp_path, error):
    rec = Recorder(tmp_path)
    def cancel():
        raise error()
    try:
        with pytest.raises(error):
            attempt(rec, {'method': 'clean'}, MockLLM(), cancel)
    finally:
        rec.rows.close()
    assert not (tmp_path/'rows.jsonl').read_text()


def test_condition_errors_surface_only_after_all_tasks_finish(tmp_path):
    def worker(case, out, stop):
        assert not stop.is_set()
        (out/'rows.jsonl').write_text(json.dumps({'status': 'error' if case['name']=='bad' else 'completed'})+'\n')
    with pytest.raises(RuntimeError, match='Batch finished with errors'):
        execute_cases([{'name': 'bad'}, {'name': 'next'}], 1, tmp_path, worker)
    states = json.loads((tmp_path/'run_status.json').read_text())['tasks']
    assert states['bad']['status'] == 'completed_with_errors'
    assert states['next']['status'] == 'completed'


def test_null_model_content_is_explicit_error_not_unhandled_json_typeerror():
    from agentrec.llm import parse_json_object
    with pytest.raises(ValueError, match='non-text'):
        parse_json_object(None)
