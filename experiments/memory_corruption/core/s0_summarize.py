"""S0 summarizer hops. Uses a caller-supplied LLM-like object. Never logs or prints a key.

Composition uses temperature 0.0. Channel-probe i.i.d. samples use temperature 1.0.
json_object is always False. max_tokens is 4000.
"""
from __future__ import annotations

import os
import time

import agent as A
import handoff_summary as HS
import s0_grade as G
import paths as P

PROMPT_P1_PATH = P.prompt('summarizer_prompt.txt')
PROMPT_P2_PATH = P.prompt('summarizer_prompt_s0_alt.txt')
SYSTEM = 'You compress a plan for another agent. Reply with plain text only, never JSON.'
MAX_TOKENS = 4000
COMPOSE_TEMPERATURE = 0.0
PROBE_TEMPERATURE = 1.0


def load_prompt(name):
    if name in ('p1', 'prompt1', 'naive', None, ''):
        with open(PROMPT_P1_PATH) as f:
            return f.read(), 'p1'
    if name in ('p2', 'prompt2', 'alt'):
        with open(PROMPT_P2_PATH) as f:
            return f.read(), 'p2'
    raise KeyError(name)


class MockLLM:
    """Scripted summarizer. cost always 0. Does not read the env or touch the ledger."""

    def __init__(self, script=None, model='mock'):
        self.model = model
        self.script = list(script or [])
        self.calls = []

    def complete_s0(self, messages, max_tokens=MAX_TOKENS, temperature=0.0, seed=0, json_object=False):
        if json_object:
            raise ValueError('S0 forbids json_object=True')
        text = self.script.pop(0) if self.script else 'Mock summary with no identifiers.'
        meta = {
            'tin': 0, 'tout': 0, 'cost': 0.0, 'latency_s': 0.0,
            'retries': 0, 'finish': 'stop', 'message_keys': ['content'],
            'temperature': temperature, 'seed': seed, 'max_tokens': max_tokens,
            'json_object': False, 'mocked': True,
        }
        self.calls.append({'messages': messages, 'meta': meta, 'response': text})
        return text, meta


def complete_s0(llm, messages, max_tokens=MAX_TOKENS, temperature=0.0, seed=0, json_object=False):
    """Plain-text completion. MockLLM or a thin wrapper around agent.LLM's client.

    Never puts the API key in messages, traces, or meta.
    """
    if json_object:
        raise ValueError('S0 forbids json_object=True')
    if hasattr(llm, 'complete_s0'):
        return llm.complete_s0(messages, max_tokens=max_tokens, temperature=temperature, seed=seed, json_object=False)
    # Real path: reuse agent.LLM.client without changing agent.LLM.complete defaults.
    if A.over_budget(A.token_spend(), A.server_delta(), A.STOP_AT):
        raise A.Budget('stop at $%.2f' % A.STOP_AT)
    body = {
        'messages': messages,
        'max_tokens': int(max_tokens),
        'temperature': float(temperature),
        'seed': int(seed),
        'model': llm.model,
    }
    # Do not set response_format. S0 is plain text only.
    status = None
    retries = 0
    data = None
    t0 = time.perf_counter()
    for attempt in range(5):
        llm.rl.wait()
        try:
            r = llm.client.post('/chat/completions', json=body)
            status = r.status_code
            if status in (429, 500, 502, 503, 504):
                retries += 1
                time.sleep(4 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except (A.httpx.TimeoutException, A.httpx.TransportError) as e:
            status = type(e).__name__
            retries += 1
            time.sleep(4 * (attempt + 1))
            continue
    else:
        raise A.Transport('failed after retries, last status %s' % status)
    u = data.get('usage') or {}
    tin = u.get('prompt_tokens') or 0
    tout = u.get('completion_tokens') or 0
    with A._L:
        m = A._ledger['models'].setdefault(llm.model, {'tin': 0, 'tout': 0, 'calls': 0})
        m['tin'] += tin
        m['tout'] += tout
        m['calls'] += 1
        A._ledger['calls'] += 1
    price = A.PRICE[llm.model]
    cost = tin / 1e6 * price[0] + tout / 1e6 * price[1]
    msg = data['choices'][0]['message']
    content = (msg.get('content') or '')
    meta = {
        'tin': tin, 'tout': tout, 'cost': cost,
        'latency_s': round(time.perf_counter() - t0, 2),
        'retries': retries,
        'finish': data['choices'][0].get('finish_reason'),
        'message_keys': sorted(msg.keys()),
        'temperature': float(temperature),
        'seed': int(seed),
        'max_tokens': int(max_tokens),
        'json_object': False,
        'mocked': False,
    }
    return content, meta


def one_hop(llm, prompt_text, source, temperature, seed, trace, attempt):
    messages = [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': prompt_text + '\n\n' + source},
    ]
    raw, meta = complete_s0(
        llm, messages, max_tokens=MAX_TOKENS,
        temperature=temperature, seed=seed, json_object=False,
    )
    rec = {
        'role': 'summarizer',
        'attempt': attempt,
        'response': raw,
        'raw_response': raw,
        'tin': meta.get('tin', 0),
        'tout': meta.get('tout', 0),
        'cost': meta.get('cost', 0),
        'latency_s': meta.get('latency_s'),
        'retries': meta.get('retries'),
        'finish': meta.get('finish'),
        'temperature': meta.get('temperature'),
        'seed': meta.get('seed'),
        'json_object': False,
        'max_tokens': MAX_TOKENS,
        'mocked': bool(meta.get('mocked')),
    }
    # Never store Authorization or env keys. Messages are the prompt plus source only.
    rec['messages'] = messages
    trace.setdefault('calls', []).append(rec)
    return (raw or '').strip(), meta, rec


def run_hops(cell, source, llm, n_hops, prompt_name='p1', temperature=COMPOSE_TEMPERATURE, seed=0, trace=None):
    """Chain n_hops plain-text summaries starting from source. Grade each hop."""
    trace = {} if trace is None else trace
    prompt, pname = load_prompt(prompt_name)
    current = source or ''
    hops = []
    truth = cell.get('gap_true')
    typo = cell.get('gap_neighbor') if cell.get('s0_condition') != 'clean' else None
    if cell.get('s0_condition') == 'clean':
        typo = cell.get('gap_neighbor')  # known, but should not appear
    known = G.known_ids_of(cell)
    total_cost = 0.0
    for i in range(1, int(n_hops) + 1):
        hop_seed = int(seed) * 100 + i
        text, meta, rec = one_hop(llm, prompt, current, temperature, hop_seed, trace, i)
        grade = G.code_text(text, truth, typo, known, cell=cell, source=source)
        hop = {
            'hop': i,
            'prompt': pname,
            'text': text,
            'grade': grade,
            'cost': meta.get('cost', 0),
            'tin': meta.get('tin', 0),
            'tout': meta.get('tout', 0),
            'temperature': temperature,
            'seed': hop_seed,
            'valid': grade.get('valid'),
            'invalid_reason': grade.get('invalid_reason'),
        }
        hops.append(hop)
        total_cost += float(meta.get('cost') or 0)
        # An invalid hop still becomes the next input so the chain is complete; the grader marks it.
        current = text if text else current
        if not grade.get('valid'):
            # Stop chaining a JSON/empty hop; later depths are invalid.
            for j in range(i + 1, int(n_hops) + 1):
                hops.append({
                    'hop': j, 'prompt': pname, 'text': '',
                    'grade': G.code_text('', truth, typo, known, cell=cell, source=source),
                    'cost': 0, 'valid': False, 'invalid_reason': 'prev_hop_invalid',
                    'temperature': temperature, 'seed': int(seed) * 100 + j,
                })
            break
    trace['summarizer_hop_log'] = hops
    trace['handoff_summary'] = (hops[-1]['text'] if hops else '')[:8000]
    trace['cost'] = total_cost
    trace['n_hops'] = int(n_hops)
    return hops, trace
