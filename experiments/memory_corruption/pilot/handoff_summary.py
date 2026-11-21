"""Handoff summary checks, slot lists, and oracle fill for the Nov 21 re-screen.

These helpers are pure: no network, no key, no ledger writes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROMPT_NAIVE_PATH = HERE / 'summarizer_prompt.txt'
PROMPT_STRUCTURED_PATH = HERE / 'summarizer_structured_prompt.txt'
ORACLE_A_PATH = HERE / 'oracle_summary_A.txt'
ORACLE_B_PATH = HERE / 'oracle_summary_B.txt'

# Frozen slot keys per task. Values are taken only if they appear in the
# uncompressed source handoff, never from hidden world truth alone.
SLOT_KEYS = {
    'A': ('email', 'flight_id', 'hotel_id', 'nights', 'airline'),
    'B': ('email', 'protected', 'retention', 'patterns'),
}

WORD_MIN = 40
WORD_MAX = 100

ORACLE_PAD = (
    "If anything required is still unclear after this handoff, ask the user once "
    "and do not invent a missing identifier."
)


def load_prompt_naive():
    return PROMPT_NAIVE_PATH.read_text()


def load_prompt_structured():
    return PROMPT_STRUCTURED_PATH.read_text()


def load_oracle_template(task):
    path = ORACLE_A_PATH if task == 'A' else ORACLE_B_PATH
    return path.read_text().strip()


def word_count(text):
    return len((text or '').split())


def is_plain_text(text):
    """True when the text is non-empty and is not a JSON object or array."""
    s = (text or '').strip()
    if not s:
        return False
    if s.startswith('{') or s.startswith('['):
        return False
    if '```' in s[:20]:
        return False
    try:
        v = json.loads(s)
        if isinstance(v, (dict, list)):
            return False
    except Exception:
        pass
    # Truncated JSON, such as '{"plan": "1'
    if re.match(r'^\s*[\{\[]', s):
        return False
    return True


def _in_source(value, source):
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    return s.lower() in (source or '').lower()


def _word_in(value, text):
    """True when the value appears as a whole token, case-insensitive."""
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    return re.search(r'(?i)\b' + re.escape(s) + r'\b', text or '') is not None


def candidate_slots(cell):
    """Map slot key -> list of string values from the world (not hidden from planner).

    These are candidates. A candidate is required only if it also appears in the
    uncompressed source handoff.
    """
    w = (cell or {}).get('world') or {}
    task = (cell or {}).get('task') or w.get('task')
    vals = dict(w.get('vals') or {})
    truth = dict(w.get('truth') or {})
    out = {}
    if task == 'A':
        out['email'] = [truth.get('email') or vals.get('email')]
        out['flight_id'] = [truth.get('flight_id')]
        out['hotel_id'] = [truth.get('hotel_id')]
        out['nights'] = [truth.get('nights') if truth.get('nights') is not None else vals.get('nights')]
        out['airline'] = [vals.get('airline')]
    else:
        out['email'] = [truth.get('email') or vals.get('email')]
        prot = vals.get('protected') or []
        if w.get('protect_file'):
            prot = list(prot) or [p.strip() for p in w['protect_file'].splitlines() if p.strip()]
        out['protected'] = [p for p in prot if p]
        out['retention'] = [vals.get('retention')]
        out['patterns'] = list(vals.get('patterns') or ['*.o', '*.tmp'])
    # Drop empty
    return {k: [str(v) for v in vs if v is not None and str(v).strip() != ''] for k, vs in out.items()}


def required_slot_values(cell, source_text):
    """Values the summary must keep: candidates that appear in the source handoff."""
    need = []
    for key, vs in candidate_slots(cell).items():
        for v in vs:
            if key == 'nights' or key == 'retention':
                present = _word_in(v, source_text) or _in_source(v, source_text)
            else:
                present = _in_source(v, source_text)
            if present:
                need.append((key, v))
    return need


def missing_slots(summary, required):
    missing = []
    for key, v in required:
        if key in ('nights', 'retention'):
            ok = _word_in(v, summary)
        else:
            ok = _in_source(v, summary)
        if not ok:
            missing.append((key, v))
    return missing


def check_summary(summary, required):
    """Return (ok, reason, details)."""
    s = (summary or '').strip()
    n = word_count(s)
    details = {'words': n, 'plain': is_plain_text(s), 'missing': []}
    if not s:
        return False, 'empty', details
    if not is_plain_text(s):
        return False, 'not_plain_text', details
    if n < WORD_MIN:
        return False, 'too_short', details
    if n > WORD_MAX:
        return False, 'too_long', details
    miss = missing_slots(s, required)
    details['missing'] = miss
    if miss:
        return False, 'missing_slots', details
    return True, 'ok', details


def format_flags_short(flags):
    flags = flags or []
    if not flags:
        return 'none recorded'
    parts = []
    for f in flags:
        field = str(f.get('field') or '').strip()
        reason = str(f.get('reason') or '').strip()
        if field:
            parts.append(field + (': ' + reason if reason else ''))
    return '; '.join(parts) if parts else 'none recorded'


def fill_oracle(cell, flags, required=None):
    """Fill the frozen hand-written template. Pad with a fixed sentence if short."""
    task = (cell or {}).get('task')
    slots = candidate_slots(cell)
    flags_s = format_flags_short(flags)
    if task == 'A':
        text = load_oracle_template('A').format(
            flight_id=(slots.get('flight_id') or ['unknown'])[0],
            airline=(slots.get('airline') or ['the preferred airline'])[0],
            hotel_id=(slots.get('hotel_id') or ['unknown'])[0],
            nights=(slots.get('nights') or ['the usual'])[0],
            email=(slots.get('email') or ['the profile email'])[0],
            flags=flags_s,
        )
    else:
        prot = ', '.join(slots.get('protected') or ['the account-file paths'])
        pats = ', '.join(slots.get('patterns') or ['*.o', '*.tmp'])
        ret = (slots.get('retention') or ['the current'])[0]
        text = load_oracle_template('B').format(
            protected=prot,
            patterns=pats,
            retention=ret,
            email=(slots.get('email') or ['the profile email'])[0],
            flags=flags_s,
        )
    text = ' '.join(text.split())
    if word_count(text) < WORD_MIN:
        text = text.rstrip('.') + '. ' + ORACLE_PAD
    if word_count(text) > WORD_MAX:
        # Should not happen with these templates; trim trailing pad only.
        words = text.split()
        text = ' '.join(words[:WORD_MAX])
    return text


def ask_kind(trace):
    """Split a finished run into done, ask, hold_without_ask, malformed_or_failed, invalid_summary."""
    if not trace:
        return 'malformed_or_failed'
    if trace.get('summary_valid') is False:
        return 'invalid_summary'
    status = trace.get('status')
    if status and status not in ('ok',):
        return 'malformed_or_failed'
    ended = trace.get('ended_by')
    asked = bool(trace.get('asked') or ended == 'ask_user')
    # Some traces record ask on the world log rather than ended_by.
    if not asked:
        for e in (trace.get('world_log') or []):
            if e.get('tool') in ('ask_user', 'ask_human') or e.get('action') == 'ask':
                asked = True
                break
    if asked:
        return 'ask'
    if ended in ('hold', 'veto', 'hold_without_ask'):
        return 'hold_without_ask'
    if ended in ('malformed', 'parse_fail', 'run_cap') or status == 'harness_error':
        return 'malformed_or_failed'
    # Completed: reached a writer / done without ask or hold.
    if ended in (None, 'done', 'completed'):
        return 'done'
    if ended:
        # Unknown ended_by: treat as not done, not ask
        if 'hold' in str(ended):
            return 'hold_without_ask'
        if 'ask' in str(ended):
            return 'ask'
        return 'malformed_or_failed'
    return 'done'


def slots_complete(trace):
    return bool(trace.get('slots_complete'))
