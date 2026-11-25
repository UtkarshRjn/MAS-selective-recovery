"""L1 transmission law: a 4-state Markov model of how one value survives agent handoffs.

Pure Python. No network, no key, no numpy.

States of the target value in a handoff text:
    V  visible error  both the wrong value and the true value are present (a visible conflict)
    S  silent error   only the wrong value is present
    T  truth          only the true value is present
    N  neither        the value was dropped

One handoff edge (one agent reads a text and writes the next one) is a 4x4 matrix
P[i][j] = P(output state j | input state i). Edge types are measured separately:
'compress' (long source to short handoff), 'relay' (short handoff to short handoff), and
'merge' (two handoffs to one).

The law makes three kinds of prediction from calibrated edge matrices only:
1. Chains: the sink distribution is the start vector times the product of edge matrices.
2. Merges: inputs are assumed independent; the merge table maps each input-state pair to an
   output distribution.
3. Fan-out trees: each node relays to b children independently. The expected number of nodes
   that carry the error (state V or S) at depth k grows or dies by the spectral radius of the
   error-carrying block Q = P[{V,S}][{V,S}]. R = b * rho(Q). R > 1 means errors snowball;
   R < 1 means they die out. A structural fix works if it pushes R below 1.

Executor harm: a terminal agent with P(wrong act | state) = a[state] turns a sink
distribution into P(harm). Defaults come from this repo's v3 grid (asks on about 95% of
visible conflicts) and are overridable.
"""
from __future__ import annotations

import math
import random

STATES = ('V', 'S', 'T', 'N')
IDX = {s: i for i, s in enumerate(STATES)}
ERROR_STATES = ('V', 'S')

GRADE_TO_STATE = {
    'disagreement': 'V',
    'typo_only': 'S',
    'truth_only': 'T',
    'neither': 'N',
}

# P(wrong irreversible act | state at the executor). Planning defaults, not data from this line.
DEFAULT_ACT = {'V': 0.05, 'S': 0.95, 'T': 0.0, 'N': 0.0}


def state_of_grade(grade):
    """Map an s0_grade.code_text result to V, S, T, N, or None for an invalid text."""
    if not grade or not grade.get('valid'):
        return None
    return GRADE_TO_STATE.get(grade.get('state'))


def state_from_values(has_wrong, has_truth):
    if has_wrong and has_truth:
        return 'V'
    if has_wrong:
        return 'S'
    if has_truth:
        return 'T'
    return 'N'


def carries(state):
    """(has_wrong, has_truth) for a state."""
    return state in ('V', 'S'), state in ('V', 'T')


# ---------------------------------------------------------------- matrices

def identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def fit_matrix(pairs, alpha=0.5, rows=STATES):
    """Fit P from (in_state, out_state) pairs, with an add-alpha prior on each observed row.

    Rows with no data stay None so that predictions never silently use an unmeasured row.
    Invalid outputs (out_state None) are dropped and counted.
    """
    counts = {r: [0.0] * 4 for r in rows}
    seen = {r: 0 for r in rows}
    dropped = 0
    for a, b in pairs:
        if b is None:
            dropped += 1
            continue
        if a not in counts:
            continue
        counts[a][IDX[b]] += 1
        seen[a] += 1
    P = []
    for s in STATES:
        if s not in counts or seen[s] == 0:
            P.append(None)
            continue
        row = [c + alpha for c in counts[s]]
        tot = sum(row)
        P.append([x / tot for x in row])
    return {'P': P, 'n': seen, 'dropped_invalid': dropped}


def matmul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def vecmat(v, P):
    out = [0.0] * 4
    for i in range(4):
        if v[i] == 0:
            continue
        if P[i] is None:
            raise ValueError('row %s is unmeasured but has mass %.3f' % (STATES[i], v[i]))
        for j in range(4):
            out[j] += v[i] * P[i][j]
    return out


def onehot(state):
    v = [0.0] * 4
    v[IDX[state]] = 1.0
    return v


def as_dict(v):
    return {s: v[i] for i, s in enumerate(STATES)}


# ---------------------------------------------------------------- predictions

def predict_chain(start, matrices):
    """start: a state or a distribution; matrices: list of 4x4 edge matrices in order."""
    v = onehot(start) if isinstance(start, str) else list(start)
    for P in matrices:
        v = vecmat(v, P)
    return v


def union_merge(a, b):
    """Lossless merge baseline: the output keeps every value either input had."""
    wa, ta = carries(a)
    wb, tb = carries(b)
    return state_from_values(wa or wb, ta or tb)


def predict_merge(da, db, merge_table=None):
    """Merge two independent input distributions.

    merge_table: {(a, b): distribution list} with unordered keys (a <= b in STATES order).
    A missing pair falls back to the union merge, and the fallback mass is reported.
    """
    out = [0.0] * 4
    fallback = 0.0
    for i, a in enumerate(STATES):
        for j, b in enumerate(STATES):
            w = da[i] * db[j]
            if w == 0:
                continue
            key = tuple(sorted((a, b), key=IDX.get))
            dist = (merge_table or {}).get(key)
            if dist is None:
                fallback += w
                out[IDX[union_merge(a, b)]] += w
            else:
                for k in range(4):
                    out[k] += w * dist[k]
    return out, fallback


def error_block(P):
    """Q = P restricted to the error-carrying states V and S."""
    v, s = IDX['V'], IDX['S']
    return [[P[v][v], P[v][s]], [P[s][v], P[s][s]]]


def spectral_radius_2x2(Q):
    a, b = Q[0]
    c, d = Q[1]
    tr, det = a + d, a * d - b * c
    disc = tr * tr / 4 - det
    if disc >= 0:
        r = math.sqrt(disc)
        return max(abs(tr / 2 + r), abs(tr / 2 - r))
    return math.sqrt(det)  # complex pair: |lambda| = sqrt(det)


def reproduction_number(P, branching):
    """R = b * rho(Q). Errors snowball in a fan-out tree when R > 1."""
    return branching * spectral_radius_2x2(error_block(P))


def critical_branching(P):
    """Largest fan-out b that keeps R below 1 for this edge matrix (inf if rho is 0)."""
    rho = spectral_radius_2x2(error_block(P))
    return math.inf if rho == 0 else 1.0 / rho


def predict_tree(start_dist, P, branching, depth):
    """Expected number of nodes per state at each depth of a b-ary relay tree."""
    v = list(start_dist)
    out = [as_dict(v)]
    for _ in range(depth):
        v = [x * branching for x in vecmat(v, P)]
        out.append(as_dict(v))
    return out


def reground_matrix(P):
    """F3, analytic: after the edge, the receiver fetches the authoritative value by pointer.

    The fetched truth is added to the handoff: S becomes V and N becomes T. This is exact by
    construction, so F3 needs no calls. It applies only where an authoritative record exists.
    """
    G = {'V': 'V', 'S': 'V', 'T': 'T', 'N': 'T'}
    out = []
    for row in P:
        if row is None:
            out.append(None)
            continue
        new = [0.0] * 4
        for j, s in enumerate(STATES):
            new[IDX[G[s]]] += row[j]
        out.append(new)
    return out


def p_harm(dist, act=None):
    act = act or DEFAULT_ACT
    return sum(dist[i] * act[s] for i, s in enumerate(STATES))


# ---------------------------------------------------------------- baseline and scoring

def naive_survival_chain(p_wrong_start, s, k):
    """Two-state baseline: P(wrong value present) after k hops = p0 * s^k (ignores truth)."""
    return p_wrong_start * s ** k


def log_loss(pred, observed_states, eps=1e-6):
    """Mean negative log probability of observed sink states under pred."""
    if not observed_states:
        return None
    tot = 0.0
    for s in observed_states:
        tot -= math.log(max(eps, pred[IDX[s]]))
    return tot / len(observed_states)


def observed_dist(states):
    v = [0.0] * 4
    ok = [s for s in states if s is not None]
    for s in ok:
        v[IDX[s]] += 1
    n = len(ok)
    return [x / n for x in v] if n else None


def bootstrap_items(item_rows, fn, n_boot=1000, seed=20261121):
    """Percentile interval of fn(resampled rows) when rows are resampled by item.

    item_rows: {item_id: [row, ...]}. fn: list of rows -> float or list of floats.
    """
    rng = random.Random(seed)
    keys = sorted(item_rows)
    draws = []
    for _ in range(n_boot):
        rows = []
        for _k in keys:
            rows.extend(item_rows[keys[rng.randrange(len(keys))]])
        try:
            draws.append(fn(rows))
        except ValueError:
            continue  # a resample left a needed row unmeasured
    if not draws:
        return None
    if isinstance(draws[0], (int, float)):
        draws.sort()
        return {'lo': draws[int(0.025 * len(draws))], 'hi': draws[int(0.975 * len(draws)) - 1],
                'n_boot': len(draws)}
    cols = list(zip(*draws))
    out = []
    for c in cols:
        c = sorted(c)
        out.append({'lo': c[int(0.025 * len(c))], 'hi': c[int(0.975 * len(c)) - 1]})
    return {'per_state': dict(zip(STATES, out)), 'n_boot': len(draws)}
