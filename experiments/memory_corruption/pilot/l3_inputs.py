"""L3 inputs: which mention of a conflicting value does a summarizer keep? Pure: no network, no key.

Every input has exactly TARGET_WORDS words. One item (template, seed) always uses the same
padding paragraphs in the same shuffled order. Across cells, only these things change:

    role      wrong_plan: the plan line names the wrong value, the evidence line the true one
              truth_plan: the plan line names the true value, the evidence line the wrong one
    position  where the evidence line sits: adjacent (right after the plan), middle, or end
    load      0 or 24 binding trip rules; with 0, neutral notes of the same length replace them
    budget    summary length asked for: 40, 80 or 160 words
"""
from __future__ import annotations

import json
import os
import random
import re

import law_inputs as LI
import templates_v3 as TV

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET_WORDS = 900
ROLES = ('wrong_plan', 'truth_plan')
POSITIONS = ('adjacent', 'middle', 'end')
LOADS = (0, 24)
BUDGETS = (40, 80, 160)
CHUNK_WORDS = 30
HEADER = 'Additional context from the planner:'
FL_RE = re.compile(r'\bFL\d+\b')
PROMPTS = {40: 'l2_prompt_40.txt', 80: 'summarizer_prompt.txt', 160: 'l3_prompt_160.txt',
           'F2': 'law_prompt_provenance.txt'}


def words(text):
    return len((text or '').split())


def role_values(cell, role):
    """(plan value, evidence value)."""
    truth, wrong = cell['gap_true'], cell['gap_neighbor']
    return (wrong, truth) if role == 'wrong_plan' else (truth, wrong)


def plan_core(cell, plan_value):
    v = dict(TV.slot_values(cell['world']))
    w = cell['world']
    return ('Task: book the trip from %s to %s on %s for the user, then email the itinerary. '
            'Book flight_id %s on %s. Book hotel_id %s for %s nights. Send the itinerary to %s, '
            'with the flight id and the hotel id.'
            % (w.get('origin'), w.get('dest'), w.get('date'), plan_value, v.get('airline'),
               v.get('hotel_id'), v.get('nights'), v.get('email')))


def evidence_line(cell, evidence_value):
    airline = dict(TV.slot_values(cell['world'])).get('airline')
    return ('The flight listing the planner received showed %s as the cheapest %s flight that day.'
            % (evidence_value, airline))


def binding_rules(cell, n):
    """The first n numbered rules from the trip_long world of the same seed (no flight ids)."""
    long_cell = TV.make_cell('trip_long', int(cell['world']['seed']), 'C0')
    rules = re.findall(r'^\d+\. .*$', long_cell['world'].get('long_constraints') or '', re.M)
    if len(rules) < n:
        raise ValueError('only %d rules' % len(rules))
    return ['Binding trip rule: ' + r.split('. ', 1)[1] for r in rules[:n]]


def neutral_words(cell):
    """Low-importance notes about earlier, finished trips, as one word list (no flight ids)."""
    seed = int(cell['world']['seed'])
    out = []
    for k in range(1, 30):
        other = TV.make_cell('trip', seed + 1000 * k, 'C0')
        note = ('Earlier completed trip %d (already booked; not part of this task). Request was: %s '
                'Hotels listed then: %s' % (k, (other.get('request') or '').strip(),
                                            json.dumps(other['world'].get('hotels') or [])))
        out += FL_RE.sub('[flight]', note).split()
    return out


def padding(cell, load, n_words):
    """Paragraphs with exactly n_words words: `load` rules plus neutral chunks, in an order that
    depends only on the item, so every cell of one item sees the same arrangement."""
    rules = binding_rules(cell, load) if load else []
    need = n_words - sum(words(r) for r in rules)
    pool = neutral_words(cell)
    if need < 0 or len(pool) < need:
        raise ValueError('cannot pad to %d words' % n_words)
    chunks = [' '.join(pool[i:i + CHUNK_WORDS]) for i in range(0, need, CHUNK_WORDS)]
    chunks[-1] = ' '.join(pool[(len(chunks) - 1) * CHUNK_WORDS:need])
    paras = rules + chunks
    rng = random.Random('%s|%s|%s' % (cell['template'], cell['world']['seed'], load))
    rng.shuffle(paras)
    return paras


def build(cell, top, load, evidence=None, position='end', target=TARGET_WORDS):
    """`top` text, then padding to `target` words in total, with the evidence line placed at
    `position`. The same padding is used for every position of one item and load."""
    fixed = words(top) + words(HEADER) + (words(evidence) if evidence else 0)
    paras = padding(cell, load, target - fixed)
    if evidence and position == 'adjacent':
        top = top + ' ' + evidence
    elif evidence:
        at = len(paras) // 2 if position == 'middle' else len(paras)
        paras = paras[:at] + [evidence] + paras[at:]
    return top + '\n\n' + HEADER + '\n' + '\n'.join(paras) + '\n'


def l3_input(cell, role, position, load):
    plan_value, evidence_value = role_values(cell, role)
    return build(cell, plan_core(cell, plan_value), load, evidence_line(cell, evidence_value), position)


def messages(budget, text):
    with open(os.path.join(HERE, PROMPTS[budget])) as f:
        prompt = f.read()
    return [{'role': 'system', 'content': LI.SYSTEM}, {'role': 'user', 'content': prompt + '\n\n' + text}]


def role_outcome(state, role):
    """Map a truth-based state (V, S, T, N) to a role-based outcome."""
    if state is None:
        return None
    if state == 'V':
        return 'both'
    if state == 'N':
        return 'none'
    wrong_kept = state == 'S'
    plan_is_wrong = role == 'wrong_plan'
    return 'evidence_lost' if wrong_kept == plan_is_wrong else 'plan_lost'
