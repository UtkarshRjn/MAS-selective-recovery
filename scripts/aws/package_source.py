"""Build an explicit deployment snapshot; never package credentials or run traces."""
from pathlib import Path
import hashlib
import json
import tarfile

root = Path(__file__).resolve().parents[2]
out = root / 'results/aws-deployment'
out.mkdir(parents=True, exist_ok=True)
paths = [root / p for p in ['pyproject.toml', 'README.md', 'data/mbpp/sanitized-mbpp.json']]
if (root / 'uv.lock').exists():
    paths.append(root / 'uv.lock')
for directory, patterns in [
    ('src', ['*.py']), ('tests', ['*.py', '*.json']),
    ('evals', ['*.json', '*.md']), ('scripts/aws', ['*.sh', '*.py', '*.md']),
]:
    for pattern in patterns:
        paths.extend(sorted((root / directory).rglob(pattern)))
with tarfile.open(out / 'source.tar.gz', 'w:gz') as archive:
    for path in paths:
        if path.is_symlink():
            raise ValueError('Unexpected deployment symlink')
        archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
inventory = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
(out / 'source-manifest.json').write_text(json.dumps(inventory, indent=2) + '\n')
print(f'Packaged {len(paths)} explicitly selected source/test/dataset files.')
