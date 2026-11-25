"""S0 grader: value codebook, composition predictions, channel correlation, kill rules.

Pure: no network, no key, no ledger writes.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import handoff_summary as HS
import perturb_gaps as PG

N_BOOT = 5000
BOOT_SEED = 20261121
# Predictive interval plus the 2x rule. Floor for the 0-vs-positive case.
RATIO_KILL = 2.0
ZERO_RATE_FLOOR = 0.10
FALSE_ALARM_KILL = 0.20
SURVIVAL_CEILING = 0.90
IDENTIFIED_P_LO = 0.05
IDENTIFIED_P_HI = 0.95
INDEP_RATIO_LO = 0.80
INDEP_RATIO_HI = 1.25


def is_valid_summary(text):
    s = (text or '').strip()
    if not s:
        return False, 'empty'
    if not HS.is_plain_text(s):
        return False, 'not_plain'
    return True, None


def value_present(text, value):
    """Whole-token match so near-miss IDs (FL472 vs FL473) do not collide."""
    if value is None or str(value).strip() == '':
        return False
    s = str(value).strip()
    return re.search(r'(?i)\b' + re.escape(s) + r'\b', text or '') is not None


FLIGHT_ID_RE = re.compile(r'\bFL\d+\b', re.I)


def _up(x):
    return str(x).upper() if x is not None and str(x).strip() != '' else None


def named_target_ids(text):
    """Every FL id in the text. Not the kill-rule set; see restrict_named_ids."""
    return sorted({m.upper() for m in FLIGHT_ID_RE.findall(text or '')})


def return_flight_ids(cell):
    w = ((cell or {}).get('world') or {})
    return {_up(f.get('id')) for f in (w.get('return_flights') or []) if f.get('id')}


def catalog_ids(cell, known_ids=None, truth=None, typo=None):
    """Ids in the live catalog: outbound flights, return flights, hotels, plus truth and typo."""
    w = ((cell or {}).get('world') or {})
    ids = set()
    for key in ('flights', 'return_flights'):
        for f in (w.get(key) or []):
            if f.get('id'):
                ids.add(_up(f.get('id')))
    for h in (w.get('hotels') or []):
        if h.get('id'):
            ids.add(_up(h.get('id')))
    for x in (known_ids or []):
        ids.add(_up(x))
    if cell:
        truth = truth or cell.get('gap_true')
        typo = typo if typo is not None else cell.get('gap_neighbor')
    if truth:
        ids.add(_up(truth))
    if typo:
        ids.add(_up(typo))
    ids.discard(None)
    return ids


def outbound_candidate_ids(cell, truth=None, typo=None):
    """Outbound target-slot candidates: truth, typo, and world['flights'] ids, excluding return_flights and hotels."""
    w = ((cell or {}).get('world') or {})
    returns = return_flight_ids(cell)
    out = set()
    if cell:
        truth = truth or cell.get('gap_true')
        typo = typo if typo is not None else cell.get('gap_neighbor')
    if truth:
        out.add(_up(truth))
    if typo:
        out.add(_up(typo))
    for f in (w.get('flights') or []):
        i = _up(f.get('id')) if f.get('id') else None
        if i and i not in returns:
            out.add(i)
    out.discard(None)
    return out


def restrict_named_ids(all_named, cell=None, truth=None, typo=None, source=None, known_ids=None):
    """Restricted named set used for clean false alarms and omission flags.

    Outbound candidates (truth, typo, outbound catalog flights), plus any invented
    FL id that is in neither the source nor the catalog. Return flights and hotels
    are excluded unless they somehow equal the truth or typo.
    """
    found = {_up(x) for x in (all_named or [])}
    found.discard(None)
    outbound = outbound_candidate_ids(cell, truth=truth, typo=typo)
    if not cell:
        if truth:
            outbound.add(_up(truth))
        if typo:
            outbound.add(_up(typo))
    catalog = catalog_ids(cell, known_ids=known_ids, truth=truth, typo=typo)
    source_ids = {_up(m) for m in FLIGHT_ID_RE.findall(source or '')}
    source_ids.discard(None)
    invented = found - catalog - source_ids
    return sorted((found & outbound) | invented)


def named_id_set(c):
    """Restricted named ids, used for omission and the clean false-alarm kill."""
    ids = (c or {}).get('named_ids')
    if ids is None:
        return set()
    return {str(x).upper() for x in ids}


def all_named_id_set(c):
    ids = (c or {}).get('all_named_ids')
    if ids is None:
        return named_id_set(c)
    return {str(x).upper() for x in ids}


def names_a_target_id(c):
    return bool(named_id_set(c))


def _pair_field(c1, c2, key):
    v = (c1 or {}).get(key)
    if v is None or str(v).strip() == '':
        v = (c2 or {}).get(key)
    return _up(v) if v is not None and str(v).strip() != '' else None


def invented_named_ids(named, cell=None, truth=None, typo=None, source=None, known_ids=None):
    """Invented FL ids in a restricted named set: in neither the source nor the catalog."""
    found = {_up(x) for x in (named or [])}
    found.discard(None)
    catalog = catalog_ids(cell, known_ids=known_ids, truth=truth, typo=typo)
    source_ids = {_up(m) for m in FLIGHT_ID_RE.findall(source or '')}
    source_ids.discard(None)
    return sorted(found - catalog - source_ids)


def channel_has_truth(c, truth=None):
    t = _up((c or {}).get('truth') or truth)
    return bool(t and t in named_id_set(c))


def channel_truth_plus_competing(c, truth=None, typo=None):
    """True when a valid channel names the truth together with the typo or an invented id."""
    if not (c or {}).get('valid'):
        return False
    t = _up((c or {}).get('truth') or truth)
    typ = _up((c or {}).get('typo') or typo)
    s = named_id_set(c)
    if not t or t not in s:
        return False
    if typ and typ != t and typ in s:
        return True
    invented = {_up(x) for x in ((c or {}).get('invented_named_ids') or [])}
    invented.discard(None)
    return bool(s & invented)


def is_truth_plus_competing_pair(c1, c2):
    """Secondary: either channel names the truth together with the typo or an invented id. Not in the kill."""
    truth = _pair_field(c1, c2, 'truth')
    typo = _pair_field(c1, c2, 'typo')
    return channel_truth_plus_competing(c1, truth, typo) or channel_truth_plus_competing(c2, truth, typo)


def is_clean_false_alarm_pair(c1, c2):
    """Clean false alarm, decided on whether each restricted set contains the truth.

    Both channels must be valid and both restricted sets non-empty. Let t_i = (truth in s_i).
    FA if t1 != t2 (one names the truth, the other names only non-truth ids).
    FA if not t1 and not t2 and s1 != s2.
    NOT FA if both channels name the truth, whatever the extra mentions.
    An omission (empty restricted set) is not a false alarm.
    Anthropic's count of FA pairs in which both sets contain the truth is zero by construction.
    """
    if not ((c1 or {}).get('valid') and (c2 or {}).get('valid')):
        return False
    s1, s2 = named_id_set(c1), named_id_set(c2)
    if not s1 or not s2:
        return False
    truth = _pair_field(c1, c2, 'truth')
    if not truth:
        return False
    t1, t2 = truth in s1, truth in s2
    if t1 and t2:
        return False
    if t1 != t2:
        return True
    return s1 != s2


def is_any_id_difference(c1, c2):
    """Secondary: all FL ids differ. Never drives the 20% kill."""
    if not ((c1 or {}).get('valid') and (c2 or {}).get('valid')):
        return False
    s1, s2 = all_named_id_set(c1), all_named_id_set(c2)
    return s1 != s2


def typo_visible_conflict(c1, c2):
    """Typo-run visible conflict from the codebook only, not the generic set rule.

    True when one channel is in the disagreement state, or the two channels
    together contain both the typo and the truth.
    """
    if not ((c1 or {}).get('valid') and (c2 or {}).get('valid')):
        return False
    if c1.get('disagreement') or c2.get('disagreement') or c1.get('state') == 'disagreement' or c2.get('state') == 'disagreement':
        return True
    typo_any = bool(c1.get('typo_present') or c2.get('typo_present'))
    truth_any = bool(c1.get('truth_present') or c2.get('truth_present'))
    return typo_any and truth_any


def code_text(text, truth, typo, known_ids=None, cell=None, source=None):
    valid, reason = is_valid_summary(text)
    typo_p = value_present(text, typo) if typo and typo != truth else False
    truth_p = value_present(text, truth)
    invented = []
    if known_ids is not None:
        invented = PG.invented_ids(text or '', known_ids)
    all_named = named_target_ids(text) if valid else []
    named = restrict_named_ids(all_named, cell=cell, truth=truth, typo=typo, source=source, known_ids=known_ids) if valid else []
    inv_named = invented_named_ids(named, cell=cell, truth=truth, typo=typo, source=source, known_ids=known_ids) if valid else []
    tpc = False
    if valid:
        tpc = channel_truth_plus_competing({
            'valid': True,
            'truth': _up(truth) if truth else None,
            'typo': _up(typo) if typo else None,
            'named_ids': named,
            'invented_named_ids': inv_named,
        })
    if not valid:
        state = 'invalid'
    elif typo_p and truth_p:
        state = 'disagreement'
    elif typo_p and not truth_p:
        state = 'typo_only'
    elif truth_p and not typo_p:
        state = 'truth_only'
    else:
        state = 'neither'
    return {
        'valid': valid,
        'invalid_reason': reason,
        'plain': HS.is_plain_text((text or '').strip()) if (text or '').strip() else False,
        'empty': not bool((text or '').strip()),
        'words': HS.word_count(text or ''),
        'typo_present': typo_p,
        'truth_present': truth_p,
        'disagreement': bool(valid and typo_p and truth_p),
        'typo_only': bool(valid and typo_p and not truth_p),
        'truth_only': bool(valid and truth_p and not typo_p),
        'neither': bool(valid and not typo_p and not truth_p),
        'invented_ids': invented,
        'invented_named_ids': inv_named,
        'named_ids': named,
        'all_named_ids': all_named,
        'truth': _up(truth) if truth else None,
        'typo': _up(typo) if typo else None,
        'truth_plus_competing': tpc,
        'state': state,
    }


def known_ids_of(cell):
    w = cell.get('world') or {}
    ids = set(PG.known_world_ids(w))
    truth = cell.get('gap_true') or PG.true_value(cell)
    neigh = cell.get('gap_neighbor') or PG.neighbor_value(cell)
    if truth:
        ids.add(str(truth))
    if neigh:
        ids.add(str(neigh))
    return ids


def wilson(k, n, z=1.96):
    if n <= 0:
        return {'p': None, 'lo': None, 'hi': None, 'k': 0, 'n': 0}
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {
        'p': p, 'lo': max(0.0, centre - half), 'hi': min(1.0, centre + half),
        'k': int(k), 'n': int(n),
    }


def _rng(seed):
    # Independent of numpy so the grader stays lightweight.
    import random
    return random.Random(seed)


def cluster_bootstrap(rows, flag_fn, n_boot=N_BOOT, seed=BOOT_SEED):
    """Resample seeds within template. rows are dicts with 'template'."""
    by = defaultdict(list)
    for r in rows:
        by[r.get('template')].append(r)
    templates = sorted(by)
    if not templates:
        return {'p': None, 'lo': None, 'hi': None, 'n': 0, 'n_templates': 0}

    def rate(groups):
        flags = []
        for g in groups:
            for r in g:
                v = flag_fn(r)
                if v is None:
                    continue
                flags.append(float(v))
        if not flags:
            return None
        return sum(flags) / len(flags)

    groups0 = [by[t] for t in templates]
    p = rate(groups0)
    rng = _rng(seed)
    draws = []
    for _ in range(int(n_boot)):
        sampled = []
        for g in groups0:
            if not g:
                continue
            sampled.append([g[rng.randrange(len(g))] for _ in g])
        q = rate(sampled)
        if q is not None:
            draws.append(q)
    if not draws or p is None:
        return {'p': p, 'lo': None, 'hi': None, 'n': sum(len(by[t]) for t in templates), 'n_templates': len(templates)}
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return {
        'p': p, 'lo': lo, 'hi': hi,
        'n': sum(len(by[t]) for t in templates),
        'n_templates': len(templates),
        'n_boot': len(draws),
    }


def valid_rows(rows):
    return [r for r in rows if (r.get('grade') or {}).get('valid')]


def survival_flag(row, metric):
    g = row.get('grade') or {}
    if not g.get('valid'):
        return None
    if metric == 'typo_survival':
        return 1.0 if g.get('typo_present') else 0.0
    if metric == 'disagreement_survival':
        return 1.0 if g.get('disagreement') else 0.0
    if metric == 'truth_survival':
        return 1.0 if g.get('truth_present') else 0.0
    raise KeyError(metric)


def measure_survival(rows, metric):
    use = valid_rows(rows)
    k = sum(1 for r in use if survival_flag(r, metric) == 1.0)
    w = wilson(k, len(use))
    b = cluster_bootstrap(use, lambda r: survival_flag(r, metric))
    by_t = {}
    for t in sorted(set(r.get('template') for r in use)):
        sub = [r for r in use if r.get('template') == t]
        kk = sum(1 for r in sub if survival_flag(r, metric) == 1.0)
        by_t[t] = wilson(kk, len(sub))
    return {
        'metric': metric,
        'k': k,
        'n_valid': len(use),
        'n_rows': len(rows),
        'n_invalid': len(rows) - len(use),
        'wilson': w,
        'bootstrap': b,
        'by_template': by_t,
    }


def predict_from_s1(s1_rec, depths=(2, 4)):
    """Lock predictions from a depth-1 survival record. s^k using the point estimate and bootstrap draws."""
    p = (s1_rec.get('bootstrap') or {}).get('p')
    if p is None:
        p = (s1_rec.get('wilson') or {}).get('p')
    out = {'s1': p, 's1_wilson': s1_rec.get('wilson'), 's1_bootstrap': s1_rec.get('bootstrap'), 'predictions': {}}
    # Rebuild bootstrap draws from the reported interval only if we do not have draws.
    # Predictions use p**k plus a conservative interval: [lo**k, min(1, hi**k)] from the s1 bootstrap CI,
    # which is slightly conservative when lo,hi are themselves percentile limits.
    lo = (s1_rec.get('bootstrap') or {}).get('lo')
    hi = (s1_rec.get('bootstrap') or {}).get('hi')
    for k in depths:
        pred = None if p is None else (p ** k)
        plo = None if lo is None else (lo ** k)
        phi = None if hi is None else (hi ** k)
        out['predictions'][str(k)] = {'k': k, 's1_pow_k': pred, 'lo': plo, 'hi': phi, 'family': 's^k'}
    return out


def fit_saturating(s1, s2):
    """Solve s_k = s_inf + (1-s_inf) * a^k from depths 1 and 2. None if no valid fit."""
    if s1 is None or s2 is None:
        return None
    # s1 = s_inf + (1-s_inf) a
    # s2 = s_inf + (1-s_inf) a^2
    # Let u = 1-s_inf. Then s1 = 1 - u(1-a), s2 = 1 - u(1-a^2)
    # 1-s2 = u(1-a)(1+a), 1-s1 = u(1-a)
    # if 1-s1 != 0, (1-s2)/(1-s1) = 1+a, so a = (1-s2)/(1-s1) - 1
    if abs(1.0 - s1) < 1e-12:
        if abs(s2 - s1) < 1e-12:
            return {'s_inf': s1, 'a': 0.0, 'note': 's1_at_ceiling'}
        return None
    a = (1.0 - s2) / (1.0 - s1) - 1.0
    # s1 = s_inf + (1-s_inf)a => s1 - a = s_inf(1-a) => s_inf = (s1-a)/(1-a) if a!=1
    if abs(1.0 - a) < 1e-12:
        return None
    s_inf = (s1 - a) / (1.0 - a)
    if not (0.0 <= a <= 1.0 and 0.0 <= s_inf <= 1.0):
        return None
    return {'s_inf': s_inf, 'a': a, 'note': 'fit_on_depths_1_and_2'}


def saturating_value(fit, k):
    if not fit:
        return None
    return fit['s_inf'] + (1.0 - fit['s_inf']) * (fit['a'] ** k)


def prediction_miss(observed, predicted, pred_lo, pred_hi):
    """True when the simple law misses: outside bootstrap PI or more than 2x."""
    reasons = []
    if observed is None or predicted is None:
        return True, ['missing_value']
    if pred_lo is not None and pred_hi is not None:
        if observed < pred_lo or observed > pred_hi:
            reasons.append('outside_bootstrap_pi')
    if predicted > 0 and observed > 0:
        ratio = max(observed / predicted, predicted / observed)
        if ratio > RATIO_KILL:
            reasons.append('ratio_gt_2')
    else:
        # One side is zero. Count as a 2x-style miss when the other side is at least the floor.
        if max(observed, predicted) >= ZERO_RATE_FLOOR and min(observed, predicted) == 0:
            reasons.append('zero_vs_floor')
    return (bool(reasons), reasons)


def law_result(s1_rec, obs_by_depth, use_depths=(2, 4)):
    """Compare locked s^k predictions to observed survival at depths 2 and 4."""
    locked = predict_from_s1(s1_rec, depths=use_depths)
    tests = []
    any_miss = False
    for k in use_depths:
        pred = locked['predictions'][str(k)]
        obs = obs_by_depth.get(k) or obs_by_depth.get(str(k)) or {}
        op = (obs.get('bootstrap') or {}).get('p')
        if op is None:
            op = (obs.get('wilson') or {}).get('p')
        miss, reasons = prediction_miss(op, pred.get('s1_pow_k'), pred.get('lo'), pred.get('hi'))
        any_miss = any_miss or miss
        tests.append({
            'depth': k,
            'observed': op,
            'predicted': pred.get('s1_pow_k'),
            'pred_lo': pred.get('lo'),
            'pred_hi': pred.get('hi'),
            'miss': miss,
            'reasons': reasons,
            'observed_wilson': obs.get('wilson'),
            'observed_bootstrap': obs.get('bootstrap'),
        })
    s1 = locked.get('s1')
    s2 = None
    rec2 = obs_by_depth.get(2) or obs_by_depth.get('2') or {}
    s2 = (rec2.get('bootstrap') or {}).get('p')
    if s2 is None:
        s2 = (rec2.get('wilson') or {}).get('p')
    fit = fit_saturating(s1, s2)
    backup = {'fit': fit, 'depth4_predicted': saturating_value(fit, 4) if fit else None}
    rec4 = obs_by_depth.get(4) or obs_by_depth.get('4') or {}
    o4 = (rec4.get('bootstrap') or {}).get('p')
    if o4 is None:
        o4 = (rec4.get('wilson') or {}).get('p')
    backup['depth4_observed'] = o4
    if fit and o4 is not None and backup['depth4_predicted'] is not None:
        # Backup hits if observed is within 0.10 absolute, or within the depth-4 Wilson CI if present.
        ci = rec4.get('wilson') or {}
        pred4 = backup['depth4_predicted']
        if ci.get('lo') is not None and ci.get('hi') is not None:
            backup['hits_depth4'] = ci['lo'] <= pred4 <= ci['hi']
        else:
            backup['hits_depth4'] = abs(o4 - pred4) <= 0.10
    else:
        backup['hits_depth4'] = None
    ceiling = (s1 is not None and s1 >= 1.0 - 1e-12)
    d4 = next((x for x in tests if x.get('depth') == 4), None)
    simple_hit_d4 = bool(d4 is not None and not d4.get('miss'))
    backup_hit = backup.get('hits_depth4')
    if ceiling:
        family_claim = 'ceiling_at_depth_1'
    elif simple_hit_d4 and backup_hit:
        family_claim = 'not_discriminated'
    elif simple_hit_d4 and backup_hit is False:
        family_claim = 'simple_s_k'
    elif (not simple_hit_d4) and backup_hit:
        family_claim = 'saturating'
    else:
        family_claim = 'neither_hits'
    return {
        'simple_predictions': locked,
        'tests': tests,
        'simple_miss': any_miss,
        'backup': backup,
        'ceiling_at_depth_1': ceiling,
        'simple_hit_depth4': simple_hit_d4,
        'family_claim': family_claim,
    }


def phenomenon_dead(obs4_typo, obs4_disagree, ceiling=SURVIVAL_CEILING):
    """True when both survivals stay above the ceiling at depth 4 (nothing to model)."""
    def p_of(rec):
        if not rec:
            return None
        p = (rec.get('bootstrap') or {}).get('p')
        if p is None:
            p = (rec.get('wilson') or {}).get('p')
        return p
    t = p_of(obs4_typo)
    d = p_of(obs4_disagree)
    if t is None or d is None:
        return False, {'typo': t, 'disagreement': d}
    return (t > ceiling and d > ceiling), {'typo': t, 'disagreement': d}


# ---- channel correlation ----

def pair_codes(c1, c2, condition='clean'):
    """c1,c2 are codebook dicts. condition is 'clean' or 'typo'."""
    s1, s2 = named_id_set(c1), named_id_set(c2)
    both_valid = bool(c1.get('valid') and c2.get('valid'))
    c1_omit = bool(c1.get('valid') and not s1)
    c2_omit = bool(c2.get('valid') and not s2)
    if condition == 'typo':
        vis = typo_visible_conflict(c1, c2)
    else:
        vis = is_clean_false_alarm_pair(c1, c2)
    return {
        'both_valid': both_valid,
        'both_typo_only': bool(c1.get('typo_only') and c2.get('typo_only')),
        'c1_typo_only': bool(c1.get('typo_only')),
        'c2_typo_only': bool(c2.get('typo_only')),
        'disagree_slot': bool(both_valid and c1.get('state') != c2.get('state')),
        'visible_conflict': vis,
        'c1_omission': c1_omit,
        'c2_omission': c2_omit,
        'omission_either': bool(c1_omit or c2_omit),
        'named_ids_c1': sorted(s1),
        'named_ids_c2': sorted(s2),
        'any_id_difference': is_any_id_difference(c1, c2),
        'truth_plus_competing_id': is_truth_plus_competing_pair(c1, c2),
        'condition': condition,
    }


def correlation_from_pairs(pair_rows):
    """pair_rows: list of dicts with c1, c2 codebooks and template."""
    use = [r for r in pair_rows if (r.get('c1') or {}).get('valid') and (r.get('c2') or {}).get('valid')]
    n = len(use)
    if n == 0:
        return {'n': 0, 'p1': None, 'p2': None, 'p_both': None, 'p_indep': None, 'excess': None}
    k1 = sum(1 for r in use if r['c1'].get('typo_only'))
    k2 = sum(1 for r in use if r['c2'].get('typo_only'))
    kb = sum(1 for r in use if r['c1'].get('typo_only') and r['c2'].get('typo_only'))
    kd = sum(1 for r in use if r['c1'].get('state') != r['c2'].get('state'))
    p1, p2, p_both = k1 / n, k2 / n, kb / n
    p_indep = p1 * p2
    excess = p_both - p_indep
    identified = (IDENTIFIED_P_LO < p1 < IDENTIFIED_P_HI) and (IDENTIFIED_P_LO < p2 < IDENTIFIED_P_HI)

    def flag_both(r):
        return 1.0 if r['c1'].get('typo_only') and r['c2'].get('typo_only') else 0.0

    boot = cluster_bootstrap(use, flag_both)

    def flag_ex(r):
        # seed-level contribution is not linear; report bootstrap on p_both only.
        return flag_both(r)

    near_zero = False
    near_reasons = []
    if identified:
        ratio = None if p_indep <= 0 else (p_both / p_indep)
        # CI for excess: use p_both CI minus p_indep (conservative; p1,p2 also random).
        lo = boot.get('lo')
        hi = boot.get('hi')
        excess_lo = None if lo is None else lo - p_indep
        excess_hi = None if hi is None else hi - p_indep
        ci_includes_zero = (excess_lo is not None and excess_hi is not None and excess_lo <= 0 <= excess_hi)
        ratio_near = (ratio is not None and INDEP_RATIO_LO <= ratio <= INDEP_RATIO_HI)
        if ci_includes_zero and ratio_near:
            near_zero = True
            near_reasons = ['excess_ci_includes_0', 'ratio_near_1']
        rec_interval = {'excess_lo': excess_lo, 'excess_hi': excess_hi, 'ratio': ratio}
    else:
        rec_interval = {'reason': 'not_identified', 'p1': p1, 'p2': p2}

    by_template = {}
    for tmpl in sorted(set(r.get('template') for r in use)):
        sub = [r for r in use if r.get('template') == tmpl]
        ns = len(sub)
        kk1 = sum(1 for r in sub if r['c1'].get('typo_only'))
        kk2 = sum(1 for r in sub if r['c2'].get('typo_only'))
        kkb = sum(1 for r in sub if r['c1'].get('typo_only') and r['c2'].get('typo_only'))
        pp1, pp2 = (kk1 / ns, kk2 / ns) if ns else (None, None)
        pb = (kkb / ns) if ns else None
        pin = (pp1 * pp2) if (pp1 is not None and pp2 is not None) else None
        by_template[tmpl] = {
            'n': ns, 'p1': pp1, 'p2': pp2, 'p_both': pb,
            'p_indep': pin, 'excess': (None if pb is None or pin is None else pb - pin),
        }
    return {
        'n': n,
        'p1': p1, 'p2': p2,
        'p_both': p_both, 'p_indep': p_indep, 'excess': excess,
        'p_disagree': kd / n,
        'wilson_both': wilson(kb, n),
        'bootstrap_both': boot,
        'identified': identified,
        'near_zero_correlation': near_zero,
        'near_zero_reasons': near_reasons,
        'interval': rec_interval,
        'k1': k1, 'k2': k2, 'k_both': kb, 'k_disagree': kd,
        'by_template': by_template,
    }


def false_alarm_rate(pair_rows):
    """Clean-run false alarm: both valid, both restricted sets non-empty, decided on truth membership.

    FA if exactly one set contains the truth, or if neither contains the truth and the sets differ.
    NOT FA if both contain the truth, whatever the extra mentions. An omission is not a false alarm.
    Secondaries (not in the 20% kill): state-difference, any-id difference, omission rates,
    and truth-plus-competing-id (a channel names the truth together with the typo or an invented id).
    FA pairs in which both sets contain the truth are zero by construction.
    """
    use = [r for r in pair_rows if (r.get('c1') or {}).get('valid') and (r.get('c2') or {}).get('valid')]
    n = len(use)
    k = sum(1 for r in use if is_clean_false_alarm_pair(r['c1'], r['c2']))
    k_state = sum(1 for r in use if r['c1'].get('state') != r['c2'].get('state'))
    k_om1 = sum(1 for r in use if r['c1'].get('valid') and not named_id_set(r['c1']))
    k_om2 = sum(1 for r in use if r['c2'].get('valid') and not named_id_set(r['c2']))
    k_om_either = sum(1 for r in use if (not named_id_set(r['c1'])) or (not named_id_set(r['c2'])))
    k_any = sum(1 for r in use if is_any_id_difference(r['c1'], r['c2']))
    k_tpc = sum(1 for r in use if is_truth_plus_competing_pair(r['c1'], r['c2']))
    k_fa_both_truth = sum(
        1 for r in use
        if is_clean_false_alarm_pair(r['c1'], r['c2'])
        and channel_has_truth(r['c1']) and channel_has_truth(r['c2'])
    )
    w = wilson(k, n)
    boot = cluster_bootstrap(use, lambda r: 1.0 if is_clean_false_alarm_pair(r['c1'], r['c2']) else 0.0) if use else {'p': None, 'lo': None, 'hi': None}
    return {
        'k': k, 'n': n,
        'rate': (k / n) if n else None,
        'wilson': w,
        'bootstrap': boot,
        'kill': bool(n and (k / n) > FALSE_ALARM_KILL),
        'state_diff': {'k': k_state, 'n': n, 'rate': (k_state / n) if n else None, 'wilson': wilson(k_state, n)},
        'any_id_difference': {'k': k_any, 'n': n, 'rate': (k_any / n) if n else None, 'wilson': wilson(k_any, n)},
        'truth_plus_competing_id': {'k': k_tpc, 'n': n, 'rate': (k_tpc / n) if n else None, 'wilson': wilson(k_tpc, n)},
        'fa_both_contain_truth': {
            'k': k_fa_both_truth, 'n': n,
            'rate': (k_fa_both_truth / n) if n else None,
            'note': 'zero by construction under the v4 truth rule',
        },
        'omission_c1': {'k': k_om1, 'n': n, 'rate': (k_om1 / n) if n else None, 'wilson': wilson(k_om1, n)},
        'omission_c2': {'k': k_om2, 'n': n, 'rate': (k_om2 / n) if n else None, 'wilson': wilson(k_om2, n)},
        'omission_either': {'k': k_om_either, 'n': n, 'rate': (k_om_either / n) if n else None, 'wilson': wilson(k_om_either, n)},
    }


def correlation_kill(corr_by_pair, fa_by_pair):
    """Kill if any intended pair has clean false-alarm > 20%, or every well-identified typo pair is near-independent."""
    fa_kill = []
    for name, rec in (fa_by_pair or {}).items():
        if rec.get('kill'):
            fa_kill.append(name)
    identified = []
    near = []
    for name, rec in (corr_by_pair or {}).items():
        if rec.get('identified'):
            identified.append(name)
            if rec.get('near_zero_correlation'):
                near.append(name)
    law_kill = False
    law_reason = None
    if identified and len(near) == len(identified):
        law_kill = True
        law_reason = 'all_identified_pairs_near_independence'
    if not identified and corr_by_pair:
        # No identified pairs: do not claim a correlation law, but this is not the 'redundancy works' kill by itself.
        law_reason = law_reason or 'no_identified_pairs'
    return {
        'false_alarm_kill': bool(fa_kill),
        'false_alarm_pairs': fa_kill,
        'correlation_law_kill': law_kill,
        'correlation_reason': law_reason,
        'identified_pairs': identified,
        'near_zero_pairs': near,
    }


def hopwise_conditional(rows, metric='typo_survival'):
    """Descriptive P(survive hop j | survived hop j-1) from depth-4 hop logs. Not a primary."""
    out = {}
    for j in (1, 2, 3, 4):
        num = den = 0
        for r in rows:
            hops = r.get('hops') or []
            by = {int(h.get('hop')): h for h in hops if h.get('hop') is not None}
            if j == 1:
                h = by.get(1)
                if not h or not (h.get('grade') or {}).get('valid'):
                    continue
                den += 1
                if survival_flag({'grade': h['grade']}, metric) == 1.0:
                    num += 1
            else:
                prev, cur = by.get(j - 1), by.get(j)
                if not prev or not cur:
                    continue
                pg = prev.get('grade') or {}
                if not pg.get('valid') or survival_flag({'grade': pg}, metric) != 1.0:
                    continue
                den += 1
                cg = cur.get('grade') or {}
                if cg.get('valid') and survival_flag({'grade': cg}, metric) == 1.0:
                    num += 1
        out[str(j)] = wilson(num, den)
    return out


def law_claim_across_models(claims):
    """A law claim needs one family to hit and the other to miss, on both models, with the same winner."""
    vals = [c for c in claims if c]
    if not vals:
        return 'no_result'
    if any(c == 'ceiling_at_depth_1' for c in vals):
        if all(c == 'ceiling_at_depth_1' for c in vals):
            return 'ceiling_at_depth_1'
        return 'mixed'
    if all(c == 'not_discriminated' for c in vals):
        return 'not_discriminated'
    if all(c == 'simple_s_k' for c in vals):
        return 'simple_s_k'
    if all(c == 'saturating' for c in vals):
        return 'saturating'
    if all(c == 'neither_hits' for c in vals):
        return 'neither_hits'
    return 'mixed'

