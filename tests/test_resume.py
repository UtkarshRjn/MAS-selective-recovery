import json

import pytest

from agentrec.resume import ResumeRecorder, import_previous


def source_run(tmp_path, status):
    source = tmp_path / 'source'
    task = source / 'tasks' / 'mbpp_2'
    task.mkdir(parents=True)
    manifest = {'cases': [{'name': 'mbpp_2', 'task_id': 2}]}
    (source / 'config.json').write_text('{}')
    (source / 'manifest.json').write_text(json.dumps(manifest))
    (source / 'run_status.json').write_text(json.dumps({
        'tasks': {'mbpp_2': {'status': status}}}))
    destination = tmp_path / 'continued'
    destination.mkdir()
    return source, destination, manifest


def test_stale_running_status_requires_recorded_stop_confirmation(tmp_path):
    source, destination, manifest = source_run(tmp_path, 'running')
    row = {'dataset': 'mbpp', 'task_id': 2, 'method': 'clean', 'success': True}
    rows = source / 'tasks' / 'mbpp_2' / 'rows.jsonl'
    rows.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='resume-source-stopped'):
        import_previous(source, destination, {}, manifest)
    import_previous(source, destination, {}, manifest, source_stopped=True)
    assert (destination / 'tasks' / 'mbpp_2' / 'rows.jsonl').read_bytes() == rows.read_bytes()
    assert json.loads((destination / 'resume.json').read_text())['source_stopped_confirmed']
    assert json.loads((source / 'run_status.json').read_text())['tasks']['mbpp_2']['status'] == 'running'


def test_unstarted_task_does_not_block_saved_task_import(tmp_path):
    source, destination, manifest = source_run(tmp_path, 'cancelled')
    saved = source / 'tasks' / 'mbpp_3'
    saved.mkdir()
    row = {'dataset': 'mbpp', 'task_id': 3, 'method': 'clean', 'success': False}
    (saved / 'rows.jsonl').write_text(json.dumps(row) + '\n')
    import_previous(source, destination, {}, manifest)
    rec = ResumeRecorder(destination / 'tasks' / 'mbpp_2')
    try:
        assert rec.previous == {}
    finally:
        rec.rows.close()
    assert (destination / 'tasks' / 'mbpp_3' / 'rows.jsonl').read_bytes() == (saved / 'rows.jsonl').read_bytes()
    assert not (source / 'tasks' / 'mbpp_2' / 'rows.jsonl').exists()
