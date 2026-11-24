"""Synthetic two-value handoff sources for the S0 screen. Pure: no network, no key, no ledger."""
from __future__ import annotations

import copy
import json
import re

import perturb_gaps as PG
import templates_v3 as TV

TEMPLATES = ('trip', 'trip_long')
CONDITIONS = ('typo', 'clean')

RECOMMENDED_RE = re.compile(
    r'Planner recommendation for the executor:\s*set\s+(\S+)\s+to\s+(\S+)\s*\.',
    re.I,
)

LISTINGS_HEADER = 'Tool results already received (listings). Every id below is in the live catalog:'
REC_LINE = 'Planner recommendation for the executor: set {slot} to {value}.'
NOTES_HEADER = (
    'Planner notes (this is not a binding argument block; '
    'check values against the listings before acting):'
)


def make_annotated_cell(template, seed, condition):
    """C0 cell with frozen typo neighbor. condition is typo or clean."""
    if template not in TEMPLATES:
        raise ValueError('S0 template must be trip or trip_long, got %r' % template)
    if condition not in CONDITIONS:
        raise ValueError('S0 condition must be typo or clean, got %r' % condition)
    cell = TV.make_cell(template, int(seed), 'C0')
    cell = PG.annotate_cell(cell, 'complete')
    cell['s0_condition'] = condition
    cell['handoff_mode'] = 's0_synthetic'
    cell['synthetic_summary'] = False
    cell['executor_plan_only'] = True
    return cell


def recommended_value(cell):
    if cell.get('s0_condition') == 'clean':
        return cell.get('gap_true') or PG.true_value(cell)
    return cell.get('gap_neighbor') or PG.neighbor_value(cell)


def listings_payload(cell):
    """JSON listings with truth kept. Typo neighbor is added only on typo runs."""
    w = copy.deepcopy(cell.get('world') or {})
    truth = cell.get('gap_true') or PG.true_value(cell)
    neigh = cell.get('gap_neighbor') or PG.neighbor_value(cell)
    if cell.get('s0_condition') == 'typo':
        PG.inject_neighbor_into_world(w, cell)
    payload = {
        'flights': w.get('flights') or [],
        'hotels': w.get('hotels') or [],
    }
    text = json.dumps(payload, indent=1)
    if cell.get('s0_condition') == 'typo':
        # inject_neighbor already cloned the flight; also keep an explicit both-ids note
        if str(truth) not in text or str(neigh) not in text:
            text = PG.add_typo_keep_truth_in_listings(text, truth, neigh)
    return text


def notes_block(cell):
    vals = dict(TV.slot_values(cell.get('world') or {}))
    slot = cell.get('gap_slot') or PG.target_slot_key(cell)
    vals[slot] = recommended_value(cell)
    lines = [NOTES_HEADER]
    for k in TV.SLOT_KEYS.get(cell.get('template') or 'trip', vals.keys()):
        v = vals.get(k)
        if isinstance(v, (list, tuple)):
            v = ', '.join(str(x) for x in v)
        lines.append('- %s: %s' % (k, v))
    return '\n'.join(lines)


def build_source(cell):
    """Deterministic synthetic planner handoff. No 'copy these exactly' block."""
    slot = cell.get('gap_slot') or PG.target_slot_key(cell)
    rec = REC_LINE.format(slot=slot, value=recommended_value(cell))
    req = 'User request:\n%s\n' % ((cell.get('request') or '').rstrip())
    longc = ((cell.get('world') or {}).get('long_constraints') or '').strip()
    extra = ''
    if longc:
        extra = '\n\nAdditional binding constraints from the request notes:\n' + longc + '\n'
    flags = 'Flags raised so far (written into the handoff for the next role): none.'
    parts = [
        req.rstrip(),
        notes_block(cell),
        rec,
        LISTINGS_HEADER,
        listings_payload(cell),
    ]
    body = '\n\n'.join(parts)
    return body + extra + '\n\n' + flags + '\n'


def extract_verbatim_value(source):
    """Deterministic extract of the recommended target value. None if the line is missing."""
    m = RECOMMENDED_RE.search(source or '')
    if not m:
        return None
    return m.group(2).strip()


def extract_verbatim_channel(source, cell):
    """Short deterministic channel text for the recommended slot."""
    slot = (cell or {}).get('gap_slot') or 'flight_id'
    value = extract_verbatim_value(source)
    if value is None:
        return ''
    return 'Verbatim extract of the planner recommendation:\n- %s: %s\n' % (slot, value)


def source_ok(cell, source):
    """Harness checks on a built source. Returns (ok, reasons)."""
    reasons = []
    truth = cell.get('gap_true') or PG.true_value(cell)
    neigh = cell.get('gap_neighbor') or PG.neighbor_value(cell)
    rec = extract_verbatim_value(source)
    listings = ''
    idx = (source or '').find(LISTINGS_HEADER)
    if idx >= 0:
        listings = source[idx:]
    if not (source or '').strip():
        reasons.append('empty_source')
    if 'copy these exactly' in (source or '').lower():
        reasons.append('verbatim_copy_exactly_forbidden')
    if rec is None:
        reasons.append('missing_recommended_line')
    if cell.get('s0_condition') == 'typo':
        if rec != str(neigh):
            reasons.append('recommended_not_typo')
        if str(truth) not in listings:
            reasons.append('truth_missing_from_listings')
        if str(neigh) not in listings:
            reasons.append('typo_missing_from_listings')
        if not value_bookable(cell, truth) or not value_bookable(cell, neigh):
            reasons.append('ids_not_bookable')
    else:
        if rec != str(truth):
            reasons.append('recommended_not_truth')
        if str(neigh) in (source or '') and str(neigh) != str(truth):
            reasons.append('typo_leaked_into_clean_source')
        if not value_bookable(cell, truth):
            reasons.append('truth_not_bookable')
    return (not reasons), reasons


def value_bookable(cell, value):
    """True when the flight id is in the live catalog after typo injection (typo runs)."""
    w = copy.deepcopy(cell.get('world') or {})
    if cell.get('s0_condition') == 'typo':
        PG.inject_neighbor_into_world(w, cell)
    ids = {str(f.get('id')) for f in (w.get('flights') or [])}
    return str(value) in ids


def world_for_run(cell):
    w = copy.deepcopy(cell.get('world') or {})
    if cell.get('s0_condition') == 'typo':
        PG.inject_neighbor_into_world(w, cell)
    return w
