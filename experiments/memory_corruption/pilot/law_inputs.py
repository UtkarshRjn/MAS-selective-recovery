"""L1 inputs: worlds, error types, start states, fixes, and edge prompts. Pure: no network, no key.

Error types (the wrong value that enters the handoff):
    E1 typo       the frozen edit-distance-1 neighbor of the true flight id (made bookable)
    E4 plausible  a different real flight from the same listing (already bookable)

Fixes (how a handoff edge is built):
    F0 plain       the existing 40-100 word summarizer prompt
    F1 pinned      the harness copies the plan's identifier lines verbatim into a pinned block
                   and re-attaches that block after the summary; no agent can edit it
    F2 provenance  a prompt that tags each identifier with its source and writes CONFLICT lines
    F3 reground    analytic only, no calls: the receiver checks the value against an
                   authoritative record by pointer (see law_model.reground_matrix)
"""
from __future__ import annotations

import os
import re

import perturb_gaps as PG
import s0_source as S0
import templates_v3 as TV

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = ('trip', 'trip_long')
ERRORS = ('E1', 'E4')
LLM_FIXES = ('F0', 'F1', 'F2')
RELAY_STATES = ('V', 'S', 'T', 'N')
# Unordered input-state pairs for the merge edge. Pairs not listed fall back to the union rule.
MERGE_PAIRS = (('V', 'T'), ('S', 'T'), ('S', 'S'), ('T', 'T'), ('V', 'S'), ('S', 'N'))

PROMPT_FILES = {
    ('relay', 'F0'): 'summarizer_prompt.txt',
    ('relay', 'F1'): 'summarizer_prompt.txt',
    ('relay', 'F2'): 'law_prompt_provenance.txt',
    ('compress', 'F0'): 'summarizer_prompt.txt',
    ('compress', 'F1'): 'summarizer_prompt.txt',
    ('compress', 'F2'): 'law_prompt_provenance.txt',
    ('merge', 'F0'): 'law_prompt_merge.txt',
    ('merge', 'F1'): 'law_prompt_merge.txt',
    ('merge', 'F2'): 'law_prompt_merge_provenance.txt',
}
SYSTEM = 'You compress a plan for another agent. Reply with plain text only, never JSON.'
PIN_HEADER = 'Pinned identifiers (copied by the harness from the plan; agents cannot edit this block):'
PIN_RE = re.compile(re.escape(PIN_HEADER) + r'\n((?:- .*\n?)*)')


def load_prompt(edge, fix):
    with open(os.path.join(HERE, PROMPT_FILES[(edge, fix)])) as f:
        return f.read()


# ---------------------------------------------------------------- worlds and errors

def plausible_wrong_id(cell, seed):
    """A different real outbound flight from the same listing, chosen by seed."""
    truth = str(cell.get('gap_true') or PG.true_value(cell))
    ids = sorted(str(f.get('id')) for f in (cell['world'].get('flights') or [])
                 if str(f.get('id')) != truth)
    if not ids:
        raise ValueError('no other flight in the listing')
    return ids[int(seed) % len(ids)]


def make_item(template, seed, error):
    """One world with a true value and a wrong value of the given error type."""
    if error not in ERRORS:
        raise ValueError(error)
    cell = S0.make_annotated_cell(template, seed, 'typo')
    if error == 'E4':
        cell['gap_neighbor'] = plausible_wrong_id(cell, seed)
    cell['law_error'] = error
    truth, wrong = str(cell['gap_true']), str(cell['gap_neighbor'])
    if truth == wrong:
        raise ValueError('wrong value equals truth')
    return cell


def item_id(template, seed, error):
    return '%s__%d__%s' % (template, seed, error)


# ---------------------------------------------------------------- pinned block (F1)

def pin_block(values):
    """values: list of (slot, value). Deterministic."""
    return PIN_HEADER + '\n' + '\n'.join('- %s: %s' % (k, v) for k, v in values) + '\n'


def extract_pin(text):
    m = PIN_RE.search(text or '')
    return m.group(0) if m else ''


def strip_pin(text):
    return PIN_RE.sub('', text or '').rstrip() + '\n'


def attach_pin(summary, pin):
    """Re-attach the input's pinned block after an agent's summary (F1 only)."""
    if not pin:
        return summary
    return strip_pin(summary).rstrip() + '\n\n' + pin


# ---------------------------------------------------------------- compress edge

def compress_input(cell, start, fix):
    """Long source. start V: the plan recommends the wrong value and the listing has the truth.
    start T: the clean source. F1 pins the plan's recommended value."""
    c = dict(cell)
    if start == 'T':
        c['s0_condition'] = 'clean'
    elif start != 'V':
        raise ValueError('compress start must be V or T')
    src = S0.build_source(c)
    if fix == 'F1':
        rec = S0.extract_verbatim_value(src)
        src = src.rstrip() + '\n\n' + pin_block([(c['gap_slot'], rec)])
    return src


# ---------------------------------------------------------------- relay edge

def _slots(cell):
    return dict(TV.slot_values(cell.get('world') or {}))


def relay_input(cell, state, fix):
    """Short (about 80 word) handoff in a given state of the target value.

    V: plan names the wrong value, and the listing note names the true value.
    S: only the wrong value.  T: only the true value.  N: the flight id was dropped.
    F1 pins whatever value the plan names (none for N).
    """
    if state not in RELAY_STATES:
        raise ValueError(state)
    v = _slots(cell)
    w = cell['world']
    truth, wrong = str(cell['gap_true']), str(cell['gap_neighbor'])
    plan_val = {'V': wrong, 'S': wrong, 'T': truth, 'N': None}[state]
    head = ('Task: book the trip from %s to %s on %s for the user, then email the itinerary.'
            % (w.get('origin'), w.get('dest'), w.get('date')))
    if plan_val:
        flight = 'Book flight_id %s on %s.' % (plan_val, v.get('airline'))
    else:
        flight = 'Book the cheapest %s flight that day (the flight id was not recorded).' % v.get('airline')
    rest = ('Book hotel_id %s for %s nights. Send the itinerary to %s, with the flight id and the hotel id.'
            % (v.get('hotel_id'), v.get('nights'), v.get('email')))
    lines = [head, flight, rest]
    if state == 'V':
        lines.append('The flight listing the planner received showed %s as the cheapest %s flight that day.'
                     % (truth, v.get('airline')))
    text = ' '.join(lines) + '\n'
    if fix == 'F1' and plan_val:
        text += '\n' + pin_block([(cell['gap_slot'], plan_val)])
    return text


# ---------------------------------------------------------------- merge edge

def merge_input(cell, a, b, fix):
    """Two relay handoffs, labelled teammate 1 and teammate 2. F1 merges both pinned blocks."""
    ta, tb = relay_input(cell, a, fix), relay_input(cell, b, fix)
    pins = []
    for t in (ta, tb):
        p = extract_pin(t)
        for line in p.splitlines()[1:]:
            if line.startswith('- ') and line not in pins:
                pins.append(line)
    body = ('Teammate 1:\n' + strip_pin(ta).strip() + '\n\nTeammate 2:\n' + strip_pin(tb).strip() + '\n')
    pin = (PIN_HEADER + '\n' + '\n'.join(pins) + '\n') if pins else ''
    if fix == 'F1' and pin:
        body += '\n' + pin
    return body, (pin if fix == 'F1' else '')


def messages_for(edge, fix, text):
    return [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': load_prompt(edge, fix) + '\n\n' + text},
    ]


def input_state(cell, text):
    """Grade an input text with the same coder used for outputs (harness check)."""
    import s0_grade as G
    import law_model as LM
    g = G.code_text(text, cell['gap_true'], cell['gap_neighbor'], G.known_ids_of(cell), cell=cell)
    return LM.state_of_grade(g)
