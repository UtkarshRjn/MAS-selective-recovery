"""Offline 16-worker sandbox check. No LLM or external API requests."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from threading import Barrier
import time

from agentrec.mbpp import load_manifest, register, evaluate
from agentrec.programs import PROGRAMS, broken

root = Path.cwd()
inventory = json.loads((root.parent / 'source-manifest.json').read_text())
assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == sha for p, sha in inventory.items())
manifest = load_manifest(Path('evals/mbpp-50.json'), Path('data/mbpp/sanitized-mbpp.json'))
image = 'python@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893'
register(manifest, image)
barrier = Barrier(16)
def check(case):
    barrier.wait(timeout=30)
    program = PROGRAMS[case['name']]
    reference = evaluate(case['reference'], program, 'final')
    mutant = evaluate(broken(case['name']), program, 'final')
    return {'task_id': case['task_id'], 'reference_passed': reference['passed'],
            'mutation_rejected': not mutant['passed']}
start = time.perf_counter()
with ThreadPoolExecutor(max_workers=16) as executor:
    rows = list(executor.map(check, manifest['cases'][:16]))
result = {'workers': 16, 'model_calls': 0, 'image': image,
          'wall_s': time.perf_counter() - start, 'source_hashes_verified': len(inventory), 'rows': rows}
Path('results').mkdir(exist_ok=True)
Path('results/aws-host-validation.json').write_text(json.dumps(result, indent=2) + '\n')
assert all(r['reference_passed'] and r['mutation_rejected'] for r in rows), result
print(json.dumps({'workers': 16, 'reference_passed': 16, 'mutation_rejected': 16,
                  'model_calls': 0, 'wall_s': result['wall_s']}))
