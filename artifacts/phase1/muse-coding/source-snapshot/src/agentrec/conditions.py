"""Isolate experimental conditions; never retry or replace a failed observation."""
import time
import traceback
from pathlib import Path


def attempt(rec, fields, llm, operation):
    from .mbpp import TaskCancelled, error_metadata
    from .resume import key
    if hasattr(rec, 'previous') and key(fields) in rec.previous:
        return rec.restore(fields)
    before = llm.usage.copy()
    start = time.perf_counter()
    try:
        return operation()
    except TaskCancelled:
        raise
    except Exception as exc:
        # Preserve diagnostic locations, but never exception text, provider bodies or headers.
        frames = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                  for f in traceback.extract_tb(exc.__traceback__)]
        rec.row(**fields, status='error', success=False, failure_kind='execution_error',
                **error_metadata(exc), error_frames=frames,
                partial_usage=llm.usage.since(before), wall_s=time.perf_counter() - start)
        print(fields.get('scenario', 'clean'), fields['method'], 'error:', type(exc).__name__, flush=True)
        return None


def skip_recovery(rec, fields, methods):
    from .resume import key
    for method in methods:
        if hasattr(rec, 'previous') and key({**fields, 'method': method}) in rec.previous:
            continue
        rec.row(**fields, method=method, status='skipped', success=None,
                skip_reason='faulty_execution_unavailable')
