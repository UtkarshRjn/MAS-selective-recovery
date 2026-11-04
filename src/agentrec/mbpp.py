"""Pinned MBPP subset for the existing coding workflow; no inference on prepare."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from .programs import PROGRAMS

REVISION = '83f1e7b59e762648c953de751d31f75720683622'
SOURCE_URL = f'https://raw.githubusercontent.com/google-research/google-research/{REVISION}/mbpp/sanitized-mbpp.json'
SOURCE_SHA256 = 'ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9'

# Executed only inside an isolated container. Tests never enter agent messages.
WORKER = '''
import contextlib, io, json, sys
p=json.load(sys.stdin)
n=len(p['tests']); failures=[]
ns={}
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        exec(p['source'], ns)
        exec('\\n'.join(p['imports']), ns)
except BaseException as e:
    failures=[{'error':type(e).__name__} for _ in p['tests']]
else:
    for index,test in enumerate(p['tests']):
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exec(test, ns)
        except BaseException as e:
            failures.append({'test_index':index,'error':type(e).__name__})
print(json.dumps({'passed':not failures,'n':n,'n_passed':n-len(failures),'failures':failures}))
'''


def prepare(source: Path, count: int) -> dict:
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('MBPP source hash mismatch')
    if count < 1:
        raise ValueError('count must be positive')
    cases, excluded = [], []
    for row in sorted(json.loads(raw), key=lambda r: r['task_id']):
        try:
            tree = ast.parse(row['code'])
            functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
            if len(functions) != 1 or len(row['test_list']) < 3:
                raise ValueError('requires one function and at least three tests')
            # Only stdlib imports: preparation does not install dataset dependencies.
            import sys
            imports = ast.parse('\n'.join(row.get('test_imports', [])))
            for n in list(ast.walk(tree)) + list(ast.walk(imports)):
                if isinstance(n, ast.Import):
                    modules = [a.name.split('.')[0] for a in n.names]
                elif isinstance(n, ast.ImportFrom):
                    modules = [(n.module or '').split('.')[0]]
                else:
                    continue
                if any(m not in sys.stdlib_module_names for m in modules):
                    raise ValueError('non-stdlib import')
            fn = functions[0]
            cases.append({'task_id': row['task_id'], 'name': f"mbpp_{row['task_id']}",
                          'entry': fn.name, 'signature': f'{fn.name}({ast.unparse(fn.args)})',
                          'request': row['prompt'], 'reference': row['code'],
                          'imports': row.get('test_imports', []),
                          'feedback_tests': row['test_list'][:1],
                          'final_tests': row['test_list'][1:]})
        except (ValueError, SyntaxError) as exc:
            excluded.append({'task_id': row['task_id'], 'reason': str(exc)})
        if len(cases) == count:
            break
    if len(cases) != count:
        raise ValueError('Not enough eligible tasks')
    return {'schema_version': 1, 'dataset': 'sanitized-mbpp', 'revision': REVISION,
            'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
            'selection': 'first eligible tasks by ascending task_id; custom subset, not official split',
            'test_policy': 'first assertion feedback; remaining assertions final-only',
            'excluded_before_cutoff': excluded, 'cases': cases}


def load_manifest(path: Path, source: Path) -> dict:
    manifest = json.loads(path.read_text())
    if prepare(source, len(manifest['cases'])) != manifest:
        raise ValueError('Manifest differs from pinned source and selection policy')
    return manifest


def register(manifest: dict, image: str) -> None:
    for case in manifest['cases']:
        PROGRAMS[case['name']] = {**copy.deepcopy(case), 'benchmark': 'mbpp', 'image': image}


def evaluate(source: str, program: dict, split: str) -> dict:
    if split not in {'feedback', 'final'}:
        raise ValueError('Unknown test split')
    tests = program[f'{split}_tests']
    payload = {'source': source, 'imports': program['imports'], 'tests': tests}
    name = 'agentrec-eval-' + uuid.uuid4().hex
    command = ['docker', 'run', '--rm', '-i', '--name', name, '--network', 'none',
               '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
               '--memory=256m', '--cpus=1', '--pids-limit=64', '--user=65534:65534',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
               program['image'], 'python', '-I', '-c', WORKER]
    try:
        result = subprocess.run(command, input=json.dumps(payload), capture_output=True,
                                text=True, timeout=15)
    except subprocess.TimeoutExpired:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
        return {'passed': False, 'n': len(tests), 'n_passed': 0,
                'failures': [{'error': 'execution_timeout'}]}
    if result.returncode == 125:
        raise RuntimeError('Docker evaluation unavailable: ' + result.stderr[-500:])
    if result.returncode:
        return {'passed': False, 'n': len(tests), 'n_passed': 0,
                'failures': [{'error': f'container_exit_{result.returncode}'}]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError('Evaluator returned invalid output') from exc


def cmd_prepare(args):
    manifest = prepare(Path(args.source), args.count)
    path = Path(args.manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Prepared {len(manifest["cases"])} tasks without inference: {path}')


def cmd_validate(args):
    manifest = load_manifest(Path(args.manifest), Path(args.source))
    register(manifest, args.image)
    rows = []
    for c in manifest['cases']:
        program = PROGRAMS[c['name']]
        reference = {s: evaluate(c['reference'], program, s)['passed'] for s in ('feedback', 'final')}
        broken = f"def {c['signature']}:\n    return None\n"
        rejects_mutation = not evaluate(broken, program, 'final')['passed']
        rows.append({'task_id': c['task_id'], 'reference': reference,
                     'rejects_return_none': rejects_mutation})
        print(rows[-1], flush=True)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps({'image': args.image, 'rows': rows}, indent=2) + '\n')
    if not all(all(r['reference'].values()) and r['rejects_return_none'] for r in rows):
        raise RuntimeError('Dataset validation failed; inspect report before inference')


PROVIDERS = {
    'local': ('http://localhost:8080/v1', None),
    'nrp': ('https://ellm.nrp-nautilus.io/v1', 'NRP_API_KEY'),
    'triton': ('https://tritonai-api.ucsd.edu/v1', 'TRITON_API_KEY'),
    'custom': (None, None),
}


def resolve_connection(args):
    """Resolve credentials without placing their values in CLI/config metadata."""
    import os
    from urllib.parse import urlsplit
    default_url, default_env = PROVIDERS[args.provider]
    endpoint = getattr(args, 'api_base_url', None) or default_url
    if not endpoint:
        raise ValueError('custom provider requires --api-base-url')
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API base URL must be HTTP(S), without credentials, query, or fragment')
    key_file = getattr(args, 'api_key_file', None)
    key_env = getattr(args, 'api_key_env', None) or (None if key_file else default_env)
    if key_file:
        key = Path(key_file).expanduser().read_text().strip()
        if not key:
            raise ValueError('API key file is empty')
    elif key_env:
        key = os.environ.get(key_env, '').strip()
        if not key:
            raise ValueError(f'Set {key_env} or select --api-key-file')
    else:
        key = None
    return {'endpoint': endpoint.rstrip('/'), 'key': key, 'key_env': key_env}


def select_tasks(manifest, limit):
    if limit is None:
        return manifest
    if not 1 <= limit <= len(manifest['cases']):
        raise ValueError('limit must be between 1 and the dataset size')
    return {**manifest, 'cases': manifest['cases'][:limit]}


def cmd_run(args):
    """Reuse coding orchestration and recovery; final tests never drive revisions."""
    if not args.execute_model:
        raise ValueError('Model calls require --execute-model')
    if args.workers < 1:
        raise ValueError('workers must be positive')
    manifest = load_manifest(Path(args.manifest), Path(args.source))
    limit = getattr(args, 'limit', None)
    manifest = select_tasks(manifest, limit)
    connection = resolve_connection(args)
    args.stateful_task_id = manifest['cases'][0]['task_id']
    if getattr(args, 'include_authorization', False) and args.phase != 'full':
        raise ValueError('--include-authorization requires --phase full')
    # Resolve local immutable image ID and check the engine before any inference.
    subprocess.run(['docker', 'info'], capture_output=True, check=True, timeout=15)
    image = subprocess.check_output(['docker', 'image', 'inspect', args.image,
                                     '--format', '{{.Id}}'], text=True).strip()
    register(manifest, image)
    if args.phase == 'full':
        from .phase1 import qualify_report_fixture
        for case in manifest['cases']:
            qualify_report_fixture(case)
    out = Path(args.trial_out)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    config = {k: v for k, v in vars(args).items() if k != 'fn'}
    config.update(image_id=image, endpoint=connection['endpoint'], resolved_key_env=connection['key_env'])
    if args.phase == 'full':
        config.update(protocol='phase1-full-v1',
                      effective_methods=['clean', 'truthful_control', 'none', 'verify', 'restart', 'suffix', 'coarse'],
                      effective_fault_types=['bad_patch', 'drop_requirement', 'requirement_hijack',
                                             'semantic_mutation', 'tester_timeout', 'false_test_report', 'stale_report'])
    (out / 'config.json').write_text(json.dumps(config, indent=2))
    if getattr(args, 'resume_from', None):
        from .resume import import_previous
        import_previous(args.resume_from, out, config, manifest, getattr(args, 'resume_errors', None))
    execute_cases(manifest['cases'], args.workers, out,
                  lambda case, task_out, stop: run_case(
                      case, args, connection, task_out, stop))


def error_metadata(exc):
    """Useful provider diagnostics without response bodies, URLs, headers or secrets."""
    result = {'error_type': type(exc).__name__}
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        result['http_status'] = exc.response.status_code
    return result


class TaskCancelled(Exception):
    """Cooperative cancellation before the next model request."""


def execute_cases(cases, workers, out, worker):
    """Bound submissions; isolate task failures; preserve and merge every completed row."""
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    from threading import Event
    import time

    if workers < 1:
        raise ValueError('workers must be positive')
    names = [case['name'] for case in cases]
    if len(names) != len(set(names)):
        raise ValueError('Task names must be unique')
    stop = Event()
    states = {name: {'status': 'not_started'} for name in names}
    pending = {}
    remaining = iter(cases)
    started = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=workers)

    def save():
        # Only this coordinator writes aggregate files. Order follows the manifest.
        lines = []
        for name in names:
            path = out / 'tasks' / name / 'rows.jsonl'
            if path.exists() and states[name]['status'] != 'running':
                lines.extend(path.read_text().splitlines())
        temp = out / 'rows.jsonl.tmp'
        temp.write_text(''.join(line + '\n' for line in lines))
        temp.replace(out / 'rows.jsonl')
        state = {'workers': workers, 'batch_wall_s': round(time.perf_counter()-started, 3),
                 'tasks': states}
        temp = out / 'run_status.json.tmp'
        temp.write_text(json.dumps(state, indent=2) + '\n')
        temp.replace(out / 'run_status.json')

    def submit():
        case = next(remaining, None)
        if case is None or stop.is_set():
            return
        task_out = out / 'tasks' / case['name']
        task_out.mkdir(parents=True, exist_ok=True)
        states[case['name']] = {'status': 'running'}
        pending[pool.submit(worker, case, task_out, stop)] = case['name']

    def collect(future):
        name = pending.pop(future)
        try:
            future.result()
        except TaskCancelled:
            states[name] = {'status': 'cancelled'}
        except Exception as exc:
            states[name] = {'status': 'failed', **error_metadata(exc)}
        else:
            rows = out / 'tasks' / name / 'rows.jsonl'
            errors = sum(json.loads(line).get('status') == 'error' for line in rows.read_text().splitlines()) if rows.exists() else 0
            states[name] = {'status': 'completed_with_errors' if errors else 'completed', 'condition_errors': errors}

    try:
        for _ in range(min(workers, len(cases))):
            submit()
        save()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                collect(future)
            # A failed task does not cancel unrelated running or queued tasks.
            if not stop.is_set():
                for _ in done:
                    submit()
            save()
    except BaseException as exc:
        stop.set()
        (out / 'INTERRUPTED.json').write_text(json.dumps(error_metadata(exc)))
        raise
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        for future in list(pending):
            collect(future)
        save()
    if any(s['status'] in {'failed', 'cancelled', 'completed_with_errors'} for s in states.values()):
        raise RuntimeError('Batch finished with errors; inspect run_status.json and per-task results')


def run_case(case, args, connection, out, stop):
    """One independent task, including sequential fault/recovery conditions."""
    from .coding import make_coding_task, check_coding
    from .llm import ServerLLM
    from .runner import Recorder, run_workflow

    class CancellableLLM(ServerLLM):
        def complete(self, *a, **kw):
            if stop.is_set():
                raise TaskCancelled()
            return super().complete(*a, **kw)

    if stop.is_set():
        raise TaskCancelled()
    llm = CancellableLLM(base_url=connection['endpoint'], model=args.model,
                         api_key=connection['key'], temperature=args.temperature,
                         seed=args.llm_seed, max_tokens=args.max_tokens,
                         timeout_s=args.request_timeout,
                         send_seed=not getattr(args, 'omit_seed', False),
                         send_temperature=not getattr(args, 'omit_temperature', False),
                         send_json_mode=not getattr(args, 'no_json_mode', False),
                         token_limit_field=getattr(args, 'token_limit_field', 'max_tokens'))
    if getattr(args, 'resume_from', None):
        from .resume import ResumeRecorder
        rec = ResumeRecorder(out)
    else:
        rec = Recorder(out)
    try:
        task, _, catalog = make_coding_task(case['name'])
        from .conditions import attempt
        base = {'task_id': case['task_id'], 'seed': args.llm_seed, 'dataset': 'mbpp'}
        def run_clean():
            world, prov, _, stats = run_workflow(task, catalog, llm)
            clean = check_coding(world, task)
            if args.phase == 'full':
                from .phase1 import audit_coding
                stats.update(audit_coding(task, world, prov))
            rec.trace(f"{case['name']}_clean", prov, world)
            rec.row(**base, fault=None, method='clean', status='completed', **clean, **stats)
            print(f"{case['name']} clean success={clean['success']}", flush=True)
            return clean
        clean = attempt(rec, {**base, 'fault': None, 'method': 'clean'}, llm, run_clean)
        if args.phase == 'clean':
            return
        from .phase1 import run_conditions
        run_conditions(case, args, llm, rec, task, catalog, clean or {'success': None}, stop)
    except BaseException as exc:
        (out / 'INTERRUPTED.json').write_text(json.dumps(error_metadata(exc)))
        raise
    finally:
        rec.rows.close()
        llm.client.close()


def add_cli(sub):
    p = sub.add_parser('mbpp', help='MBPP expansion of the existing coding workflow')
    actions = p.add_subparsers(dest='mbpp_action', required=True)
    image = 'python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893'
    for action, fn in [('prepare', cmd_prepare), ('validate', cmd_validate), ('run', cmd_run)]:
        a = actions.add_parser(action)
        a.add_argument('--source', default='data/mbpp/sanitized-mbpp.json')
        a.add_argument('--manifest', default='evals/mbpp-50.json')
        a.set_defaults(fn=fn)
        if action == 'prepare':
            a.add_argument('--count', type=int, default=50)
        else:
            a.add_argument('--image', default=image)
        if action == 'validate':
            a.add_argument('--report', default='results/mbpp-validation.json')
        if action == 'run':
            a.add_argument('--resume-from', help='Stopped source run; writes a new continuation directory')
            a.add_argument('--resume-errors', help='Explicit failed-condition rows missing from a legacy source run')
            a.add_argument('--execute-model', action='store_true')
            a.add_argument('--workers', type=int, default=1, help='Concurrent task examples; stages stay sequential')
            a.add_argument('--phase', choices=['clean', 'full'], default='full')
            a.add_argument('--include-authorization', action='store_true', help='Add three travel authorization cases')
            a.add_argument('--limit', type=int, help='Run only the first N examples from the validated dataset')
            a.add_argument('--provider', choices=list(PROVIDERS), default='local')
            a.add_argument('--api-base-url', '--base-url', dest='api_base_url',
                           help='OpenAI-compatible base URL, including /v1 when required')
            credentials = a.add_mutually_exclusive_group()
            credentials.add_argument('--api-key-env', help='Name of environment variable containing the key')
            credentials.add_argument('--api-key-file', help='File containing only the API key, outside the repo')
            a.add_argument('--omit-seed', action='store_true')
            a.add_argument('--omit-temperature', action='store_true')
            a.add_argument('--no-json-mode', action='store_true', help='Omit response_format; still parse JSON replies')
            a.add_argument('--token-limit-field', choices=['max_tokens', 'max_completion_tokens'], default='max_tokens')
            a.add_argument('--model', required=True)
            a.add_argument('--max-tokens', type=int, default=2048)
            a.add_argument('--request-timeout', type=float, default=120)
            a.add_argument('--trial-out', required=True)
