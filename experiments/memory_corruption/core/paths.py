"""Every file location in the memory-corruption experiments. No other module builds a path.

Layout under experiments/memory_corruption/:
    core/       shared code: worlds, perturbations, grading, the model client
    inputs/     what the code reads: prompts, filler text, fixed data
    runtime/    local run state: spend ledger, approvals, key file (only run_config.json is in git)
    studies/    one folder per study, with its runner, tests, docs and results
"""
from __future__ import annotations

import os

CORE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CORE)
INPUTS = os.path.join(ROOT, 'inputs')
PROMPTS = os.path.join(INPUTS, 'prompts')
FILLER = os.environ.get('PILOT_FILLER_DIR', os.path.join(INPUTS, 'filler'))
SOURCES_CSV = os.path.join(INPUTS, 'sources.csv')
SUBSTITUTION_LIST = os.path.join(INPUTS, 'substitution_list_gaps_verify.json')

RUNTIME = os.environ.get('PILOT_RUNTIME_DIR', os.path.join(ROOT, 'runtime'))
RUN_CONFIG = os.path.join(RUNTIME, 'run_config.json')
LEDGER = os.path.join(RUNTIME, 'ledger.json')
CELL_SPEND = os.path.join(RUNTIME, 'cell_spend.jsonl')
DEFAULT_ENV_FILE = os.path.join(RUNTIME, '.env')
APPROVALS = os.path.join(RUNTIME, 'approvals')

STUDIES = os.path.join(ROOT, 'studies')


def prompt(name):
    """Path of a prompt or frozen text file in inputs/prompts/."""
    return os.path.join(PROMPTS, name)


def approval(name):
    """Path of a go file in runtime/approvals/."""
    return os.path.join(APPROVALS, name)


def study(name, *parts):
    """Path inside studies/<name>/."""
    return os.path.join(STUDIES, name, *parts)
