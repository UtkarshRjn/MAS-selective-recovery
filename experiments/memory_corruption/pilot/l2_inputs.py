"""L2 inputs: handoffs with a controlled compression ratio. Pure: no network, no key.

H1 says that the rate at which an edge loses the true value of a conflicting pair depends on
the edge's compression ratio c = input words / output budget words, not on its position.
L1 relays were about 80 words in and 80 out (c near 1), and they never changed state.

This module builds the same V, S, T or N core (from law_inputs.relay_input) inside neutral,
travel-related padding, to a target input length. The padding never contains a flight id, so
the state of the input is the state of the core. Tests check this for every item.

Padding sources, in order: the trip's hotel listing and profile, the binding trip
constraints (generated for the trip_long world of the same seed), then notes about earlier
completed trips (requests and hotel listings from other seeds, with no flight ids).

Placement of the two values in a V input:
    adjacent  the plan line and the listing line sit together at the top
    spread    the plan line is at the top and the listing line is at the end
"""
from __future__ import annotations

import json
import os
import re

import law_inputs as LI
import templates_v3 as TV

HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = (80, 300, 800, 1500)          # target input words
CAL_LEVELS = (80, 300, 1500)           # used to fit; 800 is held out for the interpolation test
BUDGETS = {'b40': 40, 'b80': 80, 'b250': 250}
BUDGET_PROMPTS = {'b40': 'l2_prompt_40.txt', 'b80': 'summarizer_prompt.txt', 'b250': 'summarizer_prompt_250.txt'}
FL_RE = re.compile(r'\bFL\d+\b')
PIN_ARMS = ('pin_wrong', 'pin_truth')


def words(text):
    return len((text or '').split())


def design_c(level, budget_words=80):
    return float(level) / float(budget_words)


def _core_lines(cell, state):
    """Split law_inputs.relay_input into (head lines, listing line or '')."""
    text = LI.strip_pin(LI.relay_input(cell, state, 'F0')).strip()
    listing = ''
    marker = 'The flight listing the planner received showed'
    if marker in text:
        i = text.index(marker)
        listing = text[i:].strip()
        text = text[:i].strip()
    return text, listing


def padding_pool(cell):
    """Neutral travel padding paragraphs for this item, with every flight id removed."""
    w = cell['world']
    seed = int(w.get('seed') or 0)
    paras = [
        'Hotel listing received by the planner: ' + json.dumps(w.get('hotels') or []),
        'Account profile: ' + json.dumps(w.get('profile') or {}),
    ]
    long_cell = TV.make_cell('trip_long', seed, 'C0')
    lc = ((long_cell.get('world') or {}).get('long_constraints') or '').strip()
    if lc:
        paras.append(lc)
    for k in range(1, 12):
        other = TV.make_cell('trip', seed + 1000 * k, 'C0')
        ow = other.get('world') or {}
        paras.append('Earlier completed trip %d (already booked; not part of this task). Request was: %s '
                     'Hotels that were listed then: %s'
                     % (k, (other.get('request') or '').strip(), json.dumps(ow.get('hotels') or [])))
    return [FL_RE.sub('[flight]', p) for p in paras]


def pad_to(cell, core_head, core_tail, target):
    """core_head + padding + core_tail with about `target` words in total."""
    need = target - words(core_head) - words(core_tail)
    body = []
    if need > 0:
        pool = ' '.join(padding_pool(cell)).split()
        if len(pool) < need:
            raise ValueError('padding pool too small: %d < %d' % (len(pool), need))
        body = pool[:need]
    parts = [core_head]
    if body:
        parts.append('Additional context from the planner:\n' + ' '.join(body))
    if core_tail:
        parts.append(core_tail)
    return '\n\n'.join(parts) + '\n'


def l2_input(cell, state, level, placement='adjacent', pin=None):
    """A handoff in `state`, at about `level` words.

    pin: None, 'pin_wrong' or 'pin_truth'. A pin adds a harness-owned block with that value
    (H3, the authority test). It is only defined for V inputs.
    """
    head, listing = _core_lines(cell, state)
    if state == 'V' and placement == 'adjacent':
        head, tail = head + ' ' + listing, ''
    elif state == 'V' and placement == 'spread':
        tail = listing
    else:
        tail = ''
    text = pad_to(cell, head, tail, level)
    if pin:
        if state != 'V':
            raise ValueError('pin arms are defined for V inputs only')
        val = cell['gap_neighbor'] if pin == 'pin_wrong' else cell['gap_true']
        text = text.rstrip() + '\n\n' + LI.pin_block([(cell['gap_slot'], val)])
    return text


def load_budget_prompt(budget):
    with open(os.path.join(HERE, BUDGET_PROMPTS[budget])) as f:
        return f.read()


def messages(fix, text, budget='b80'):
    """F0 uses the budget prompt; F2 uses the provenance prompt (budget b80 only)."""
    if fix == 'F2':
        if budget != 'b80':
            raise ValueError('F2 is defined only at the 80-word budget')
        prompt = LI.load_prompt('relay', 'F2')
    else:
        prompt = load_budget_prompt(budget)
    return [{'role': 'system', 'content': LI.SYSTEM},
            {'role': 'user', 'content': prompt + '\n\n' + text}]
