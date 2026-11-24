"""Zero-cost comparison of v2, v3-subset, and v4-truth clean false-alarm definitions. No API, no key, no ledger."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import _core  # noqa: F401  (puts core/ on the import path)

import s0_source as S
import s0_grade as G
from dry_run_summary_chain import script_for
import run_summary_chain as R

OUT = os.path.join(HERE, 'results', 's0_v4_fa_gap.txt')
V3_TRACES = os.path.join(HERE, '..', 'conflict_asking', 'traces')
PAD = ' word' * 40


def old_v2_false_alarm(c1, c2):
    """v2 rule: both valid, all FL id sets non-empty, and the all-id sets differ."""
    if not ((c1 or {}).get('valid') and (c2 or {}).get('valid')):
        return False
    s1, s2 = G.all_named_id_set(c1), G.all_named_id_set(c2)
    return bool(s1 and s2 and s1 != s2)


def v3_subset_false_alarm(c1, c2):
    """v3 rule: both valid, both restricted sets non-empty, neither is a subset of the other."""
    if not ((c1 or {}).get('valid') and (c2 or {}).get('valid')):
        return False
    s1, s2 = G.named_id_set(c1), G.named_id_set(c2)
    if not s1 or not s2:
        return False
    return (not s1.issubset(s2)) and (not s2.issubset(s1))


def v4_truth_false_alarm(c1, c2):
    return G.is_clean_false_alarm_pair(c1, c2)


def rate(pairs, fn):
    use = [p for p in pairs if (p['c1'] or {}).get('valid') and (p['c2'] or {}).get('valid')]
    n = len(use)
    k = sum(1 for p in use if fn(p['c1'], p['c2']))
    return k, n, (k / n if n else None)


def fmt(r):
    k, n, p = r
    if p is None:
        return 'k=%s n=%s rate=NA' % (k, n)
    return 'k=%s n=%s rate=%.4f' % (k, n, p)


def wrap_cell(world, truth, typo):
    return {'world': world or {}, 'gap_true': truth, 'gap_neighbor': typo}


def grade(text, truth, typo, cell=None, source=None):
    return G.code_text(text, truth, typo, cell=cell, source=source)


def truth_only_text(truth):
    return ('Book or send with flight_id set to %s. Do not invent other identifiers.' % truth) + PAD


def list_all_text(ids, truth=None):
    body = 'Identifier list: ' + ', '.join(ids)
    if truth:
        body += '. Recommended outbound flight is %s.' % truth
    return body + PAD


def section_rates(name, pairs, lines):
    r2, r3, r4 = rate(pairs, old_v2_false_alarm), rate(pairs, v3_subset_false_alarm), rate(pairs, v4_truth_false_alarm)
    lines.append('%s: v2 %s' % (name, fmt(r2)))
    lines.append('%s: v3-subset %s' % (name, fmt(r3)))
    lines.append('%s: v4-truth %s' % (name, fmt(r4)))
    if r3[2] is not None and r4[2] is not None:
        lines.append('%s: gap (v3-v4) %.4f  (v3 would have flagged %d more of %d valid pairs)' % (
            name, r3[2] - r4[2], r3[0] - r4[0], r3[1]))
    if r2[2] is not None and r4[2] is not None:
        lines.append('%s: gap (v2-v4) %.4f  (v2 would have flagged %d more of %d valid pairs)' % (
            name, r2[2] - r4[2], r2[0] - r4[0], r2[1]))
    lines.append('')
    return {'name': name, 'v2': r2, 'v3': r3, 'v4': r4}


def mocked_dry_run_pairs():
    pairs = []
    cell = S.make_annotated_cell('trip', 30, 'typo')
    src = S.build_source(cell)
    truth, typo = cell['gap_true'], cell['gap_neighbor']
    texts = script_for(cell, ['disagreement', 'typo_only', 'truth_only', 'neither'])
    grades = [grade(t, truth, typo, cell=cell, source=src) for t in texts]
    labels = ['disagreement', 'typo_only', 'truth_only', 'neither']
    for i in range(len(grades)):
        for j in range(i + 1, len(grades)):
            pairs.append({'c1': grades[i], 'c2': grades[j], 'kind': 'mocked_%s_vs_%s' % (labels[i], labels[j]),
                          'template': 'trip', 'seed': 30, 'condition': 'typo'})
    vtext = S.extract_verbatim_channel(src, cell)
    vg = grade(vtext, truth, typo, cell=cell, source=src)
    for lab, g in zip(labels, grades):
        pairs.append({'c1': vg, 'c2': g, 'kind': 'mocked_verbatim_vs_%s' % lab,
                      'template': 'trip', 'seed': 30, 'condition': 'typo'})
    for tmpl in S.TEMPLATES:
        for seed in (30, 60):
            c = S.make_annotated_cell(tmpl, seed, 'clean')
            s = S.build_source(c)
            t, typ = c['gap_true'], c['gap_neighbor']
            g_truth = grade(truth_only_text(t), t, typ, cell=c, source=s)
            all_ids = G.named_target_ids(s)
            g_list = grade(list_all_text(all_ids, truth=t), t, typ, cell=c, source=s)
            pairs.append({'c1': g_truth, 'c2': g_list, 'kind': 'dry_clean_truth_vs_all_source_ids',
                          'template': tmpl, 'seed': seed, 'condition': 'clean'})
            ret_ids = sorted(G.return_flight_ids(c))
            if ret_ids:
                g_ret = grade(('Book %s. Return flight is %s.' % (t, ret_ids[0])) + PAD,
                              t, typ, cell=c, source=s)
                pairs.append({'c1': g_truth, 'c2': g_ret, 'kind': 'dry_clean_truth_vs_truth_plus_return',
                              'template': tmpl, 'seed': seed, 'condition': 'clean'})
    return pairs


def s0_clean_probe_pairs():
    pairs = []
    for tmpl in S.TEMPLATES:
        for seed in R.PROBE_CLEAN_SEEDS:
            c = S.make_annotated_cell(tmpl, seed, 'clean')
            s = S.build_source(c)
            t, typ = c['gap_true'], c['gap_neighbor']
            g_truth = grade(truth_only_text(t), t, typ, cell=c, source=s)
            all_ids = G.named_target_ids(s)
            g_list = grade(list_all_text(all_ids, truth=t), t, typ, cell=c, source=s)
            pairs.append({'c1': g_truth, 'c2': g_list, 'kind': 's0_clean_truth_vs_all_source_ids',
                          'template': tmpl, 'seed': seed})
            alts = sorted(G.outbound_candidate_ids(c) - {str(t).upper(), str(typ).upper()} - G.return_flight_ids(c))
            if len(alts) >= 2:
                g_extra = grade(('Book %s, also listed %s and %s.' % (t, alts[0], alts[1])) + PAD,
                                t, typ, cell=c, source=s)
                pairs.append({'c1': g_truth, 'c2': g_extra, 'kind': 's0_clean_truth_vs_truth_plus_two_alts',
                              'template': tmpl, 'seed': seed})
                g_ta = grade(('Book %s, also listed %s.' % (t, alts[0])) + PAD, t, typ, cell=c, source=s)
                g_tb = grade(('Book %s, also listed %s.' % (t, alts[1])) + PAD, t, typ, cell=c, source=s)
                pairs.append({'c1': g_ta, 'c2': g_tb, 'kind': 's0_clean_truth_A_vs_truth_B',
                              'template': tmpl, 'seed': seed})
                g_a = grade(('Book %s only.' % alts[0]) + PAD, t, typ, cell=c, source=s)
                g_b = grade(('Book %s only.' % alts[1]) + PAD, t, typ, cell=c, source=s)
                pairs.append({'c1': g_a, 'c2': g_b, 'kind': 's0_clean_A_vs_B',
                              'template': tmpl, 'seed': seed})
            ret_ids = sorted(G.return_flight_ids(c))
            if ret_ids:
                g_ret = grade(('Book %s. Return flight is %s.' % (t, ret_ids[0])) + PAD,
                              t, typ, cell=c, source=s)
                pairs.append({'c1': g_truth, 'c2': g_ret, 'kind': 's0_clean_truth_vs_truth_plus_return',
                              'template': tmpl, 'seed': seed})
            catalog = G.catalog_ids(c, truth=t, typo=typ)
            inv = 'FL8888'
            if inv not in catalog and inv not in s.upper():
                g_inv = grade(('Book %s only.' % inv) + PAD, t, typ, cell=c, source=s)
                g_t_inv = grade(('Book %s, also %s.' % (t, inv)) + PAD, t, typ, cell=c, source=s)
                pairs.append({'c1': g_truth, 'c2': g_inv, 'kind': 's0_clean_truth_vs_invented',
                              'template': tmpl, 'seed': seed})
                pairs.append({'c1': g_truth, 'c2': g_t_inv, 'kind': 's0_clean_truth_vs_truth_plus_invented',
                              'template': tmpl, 'seed': seed})
    return pairs


def confirm_v3_pairs():
    pairs = []
    if not os.path.isdir(V3_TRACES):
        return pairs
    for name in sorted(os.listdir(V3_TRACES)):
        if not name.endswith('.json'):
            continue
        if 'trip_long' not in name and '__trip__' not in name:
            continue
        rec = json.load(open(os.path.join(V3_TRACES, name)))
        tmpl = rec.get('template')
        truth, typo = rec.get('gap_true'), rec.get('gap_neighbor')
        world = rec.get('world') or {}
        cell = wrap_cell(world, truth, typo)
        source = rec.get('executor_handoff') or rec.get('source_handoff') or ''
        text = rec.get('handoff_summary') or rec.get('handoff_summary_raw') or rec.get('conflict_summary') or ''
        if not text or not truth:
            continue
        g_sum = grade(text, truth, typo, cell=cell, source=source)
        g_truth = grade(truth_only_text(truth), truth, typo, cell=cell, source=source)
        pairs.append({'c1': g_truth, 'c2': g_sum, 'kind': 'v3_truth_vs_handoff_summary',
                      'template': tmpl, 'seed': rec.get('seed'), 'file': name})
        all_ids = G.named_target_ids(source)
        if all_ids:
            g_list = grade(list_all_text(all_ids, truth=truth), truth, typo, cell=cell, source=source)
            pairs.append({'c1': g_truth, 'c2': g_list, 'kind': 'v3_truth_vs_all_handoff_ids',
                          'template': tmpl, 'seed': rec.get('seed'), 'file': name})
            pairs.append({'c1': g_sum, 'c2': g_list, 'kind': 'v3_handoff_summary_vs_all_handoff_ids',
                          'template': tmpl, 'seed': rec.get('seed'), 'file': name})
    return pairs


def main():
    lines = []
    def p(s=''):
        lines.append(s)
    p('S0 v4 zero-cost false-alarm definition gap (v2 all-FL-id inequality vs v3 restricted subset vs v4 truth membership)')
    p('No API calls. No key. No ledger writes.')
    p('v2: both valid, all named FL id sets non-empty, and those all-id sets differ.')
    p('v3-subset: both valid, both restricted outbound sets non-empty, and neither set is a subset of the other.')
    p('v4-truth: both valid, both restricted sets non-empty; FA if t1 != t2, or if neither contains the truth and s1 != s2; NOT FA if both contain the truth.')
    p('')

    dry = mocked_dry_run_pairs()
    section_rates('A_all_mocked_dry_run_pairs', dry, lines)
    section_rates('A_dry_run_clean_synthetic', [x for x in dry if x.get('condition') == 'clean'], lines)
    section_rates('A_dry_run_truth_vs_all_source_ids', [x for x in dry if x.get('kind') == 'dry_clean_truth_vs_all_source_ids'], lines)

    s0 = s0_clean_probe_pairs()
    section_rates('B_s0_clean_seeds_80_91_all_pairs', s0, lines)
    for kind in ('s0_clean_truth_vs_all_source_ids', 's0_clean_truth_vs_truth_plus_two_alts',
                 's0_clean_truth_vs_truth_plus_return', 's0_clean_truth_A_vs_truth_B',
                 's0_clean_A_vs_B', 's0_clean_truth_vs_invented', 's0_clean_truth_vs_truth_plus_invented'):
        section_rates('B_' + kind, [x for x in s0 if x.get('kind') == kind], lines)
    for tmpl in S.TEMPLATES:
        section_rates('B_s0_clean_%s' % tmpl, [x for x in s0 if x.get('template') == tmpl], lines)

    v3 = confirm_v3_pairs()
    section_rates('C_confirm_v3_trip_and_trip_long_all_pairs', v3, lines)
    for kind in ('v3_truth_vs_handoff_summary', 'v3_truth_vs_all_handoff_ids',
                 'v3_handoff_summary_vs_all_handoff_ids'):
        section_rates('C_' + kind, [x for x in v3 if x.get('kind') == kind], lines)
    for tmpl in ('trip', 'trip_long'):
        section_rates('C_confirm_v3_%s' % tmpl, [x for x in v3 if x.get('template') == tmpl], lines)

    p('Headline. The v3 artifact NeurIPS blocked, on S0 clean probe seeds: {truth, alt A} versus {truth, alt B}.')
    for tmpl in S.TEMPLATES:
        sub = [x for x in s0 if x.get('kind') == 's0_clean_truth_A_vs_truth_B' and x.get('template') == tmpl]
        r2, r3, r4 = rate(sub, old_v2_false_alarm), rate(sub, v3_subset_false_alarm), rate(sub, v4_truth_false_alarm)
        p('  %s  v2 %s  v3-subset %s  v4-truth %s' % (tmpl, fmt(r2), fmt(r3), fmt(r4)))
    p('')
    p('Under v3 the subset rule flags every such pair. Under v4 both channels name the truth, so none are false alarms.')
    p('The other new cases: {A} vs {B} and {truth} vs invented stay false alarms under v3 and v4. {truth} vs {truth, invented} is not a false alarm under v3 or v4; the v4 truth-plus-competing-id secondary is 1 on those pairs.')
    p('The v2 extra-mention artifact is unchanged: truth-only versus a list of every FL id in the source is still 1.00 under v2 and 0.00 under v3 and v4.')
    p('')
    # Competing-id secondary on the invented-plus-truth pairs
    tpc = [x for x in s0 if x.get('kind') == 's0_clean_truth_vs_truth_plus_invented']
    k_tpc = sum(1 for x in tpc if G.is_truth_plus_competing_pair(x['c1'], x['c2']))
    p('truth-plus-competing-id secondary on {truth} vs {truth, invented} over clean seeds: k=%d n=%d rate=%.4f' % (
        k_tpc, len(tpc), (k_tpc / len(tpc) if tpc else float('nan'))))
    p('')
    text = '\n'.join(lines) + '\n'
    open(OUT, 'w').write(text)
    print(text)
    print('wrote', OUT)


if __name__ == '__main__':
    main()
