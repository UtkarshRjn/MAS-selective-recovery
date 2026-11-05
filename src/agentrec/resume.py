"""Resume immutable saved conditions into a new output directory, without retrying scores."""
import copy
import json
import shutil
from pathlib import Path

from .runner import Recorder


def key(row):
    return (row['dataset'], row['task_id'], row.get('scenario') or row.get('fault') or 'clean', row['method'])


class ResumeRecorder(Recorder):
    def __init__(self, out_dir):
        path = out_dir / 'rows.jsonl'
        rows = [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
        self.previous = {key(r): r for r in rows}
        if len(self.previous) != len(rows):
            raise ValueError('Duplicate condition in resume input')
        super().__init__(out_dir)

    def restore(self, fields):
        row = self.previous[key(fields)]
        if row.get('status') in {'error', 'skipped'}:
            return None
        if row['method'] != 'none':
            return copy.deepcopy(row)
        from .world import World
        from .provenance import Provenance, Node
        from .faults import Injector, FaultSpec
        if row['dataset'] == 'mbpp':
            label = f"mbpp_{row['task_id']}_{row['scenario']}_none"
            world = World([], [])
            kind, stage = row['fault'].split('@')
            spec = FaultSpec(kind, stage, False)
        else:
            from .authorization import make_authorization_case
            _, _, catalog = make_authorization_case(row['scenario'], row['task_id'])
            world = World(*catalog)
            label = f"authorization_{row['task_id']}_{row['scenario']}_none"
            spec = FaultSpec(row['scenario'], 'research_hotels', True)
        saved = json.loads((self.out_dir / 'traces' / (label + '.json')).read_text())
        for attr in ('files', 'bookings', 'outbox', 'action_history'):
            setattr(world, attr, copy.deepcopy(saved['world'].get(attr, [] if attr in ('outbox', 'action_history') else {})))
        world._next_booking = 1 + max((int(b[1:]) for b in world.bookings if b.startswith('B') and b[1:].isdigit()), default=0)
        prov = Provenance()
        prov.nodes = {n['id']: Node(**n) for n in saved['provenance']['nodes']}
        prov._seq = max((n.seq for n in prov.nodes.values()), default=0)
        prov.edges = [(e['src'], e['dst'], e['type']) for e in saved['provenance']['edges']]
        prov.model_calls = saved['provenance'].get('model_calls', [])
        prov.write_s = row.get('provenance_write_s', 0)
        injector = Injector(spec)
        injector.fired = int(row['fault_fired'])
        injector.events = row.get('injection_events', [])
        return world, prov, injector, copy.deepcopy(row)


def import_previous(source, destination, config, manifest, failures=None, *, source_stopped=False):
    """Only explicit legacy failures may supplement rows lacking condition-level errors."""
    source = Path(source)
    old = json.loads((source/'config.json').read_text())
    old_manifest = json.loads((source/'manifest.json').read_text())
    if old_manifest['cases'] != manifest['cases']:
        raise ValueError('Resume task selection differs')
    for name in ('model', 'provider', 'endpoint', 'phase', 'llm_seed', 'temperature', 'max_tokens',
                 'request_timeout', 'image_id', 'include_authorization', 'workers',
                 'omit_seed', 'omit_temperature', 'no_json_mode', 'token_limit_field'):
        if old.get(name) != config.get(name):
            raise ValueError(f'Resume configuration differs: {name}')
    source_status = json.loads((source/'run_status.json').read_text())
    if any(v['status'] == 'running' for v in source_status['tasks'].values()) and not source_stopped:
        raise ValueError('Source is marked running. Stop its process, then use --resume-source-stopped to confirm.')
    (destination/'tasks').mkdir(parents=True, exist_ok=True)
    for task in (source/'tasks').iterdir():
        if not task.is_dir():
            continue
        target = destination/'tasks'/task.name
        target.mkdir(parents=True)
        if (task/'rows.jsonl').exists():
            shutil.copyfile(task/'rows.jsonl', target/'rows.jsonl')
        else:
            (target/'rows.jsonl').touch()
        if (task/'traces').exists():
            shutil.copytree(task/'traces', target/'traces')
    additions = json.loads(Path(failures).read_text()) if failures else []
    for row in additions:
        if row.get('status') != 'error' or row.get('success') is not False:
            raise ValueError('Legacy supplements must be explicit failed conditions')
        target = destination/'tasks'/f"mbpp_{row['task_id']}"/'rows.jsonl'
        existing = [json.loads(s) for s in target.read_text().splitlines()]
        if key(row) in {key(r) for r in existing}:
            raise ValueError('Legacy failure would overwrite a completed result')
        with target.open('a') as f:
            f.write(json.dumps(row)+'\n')
    (destination/'resume.json').write_text(json.dumps({
        'source_run': str(source.resolve()), 'original_config': old,
        'source_stopped_confirmed': source_stopped,
        'legacy_failures': additions, 'policy': 'Preserve all existing results; rerun only unfinished conditions.'}, indent=2))
    # Validate row uniqueness and required faulty traces before any model calls.
    for task in (destination/'tasks').iterdir():
        rec = ResumeRecorder(task)
        try:
            for row in rec.previous.values():
                if row['method'] == 'none' and row.get('status') not in {'error', 'skipped'}:
                    rec.restore(row)
        finally:
            rec.rows.close()
