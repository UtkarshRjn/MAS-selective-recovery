"""Score the 21 November exploratory screen. Appendix only. Does not write MAIN_GO."""
import collections, glob, json, os
import analyze as AN

HERE = os.path.dirname(os.path.abspath(__file__))
TR = os.path.join(HERE, 'traces_screen_nov21')
SETUPS = ('S_full', 'S_match', 'P_short', 'P_long', 'P_comp', 'P_comp_flags')

def _mean(xs):
    xs = list(xs)
    return (sum(xs) / len(xs)) if xs else None

def main():
    rows, problems = [], []
    files = sorted(glob.glob(os.path.join(TR, '*.json')))
    for f in files:
        d = json.load(open(f))
        rec = {
            'file': os.path.basename(f),
            'setup': d.get('setup'), 'arm': d.get('arm'),
            'handoff_mode': d.get('handoff_mode') or d.get('handoff_mode'),
            'self_review': bool(d.get('self_review')),
            'task': d.get('task'), 'seed': d.get('seed'), 'cell': d.get('cell'),
            'status': d.get('status'),
            'manip_pass': bool((d.get('manip') or {}).get('pass')),
            'cost': d.get('cost') or 0,
            'ended_by': d.get('ended_by'),
            'n_flags': len(d.get('flags') or []),
            'flag_text_in_handoff': d.get('flag_text_in_handoff'),
            'handoff_chars': d.get('handoff_chars'),
            'handoff_compressed': d.get('handoff_compressed'),
            'n_calls': d.get('n_calls'),
            'n_length_retries': int(d.get('n_length_retries') or 0),
        }
        if d.get('status') == 'ok':
            try:
                s = AN.score(d)
                for k in ('ih', 'sh', 'asked', 'ts', 'mask', 'flagged', 'flag_rate_any',
                          'flag_reached_actor', 'vetoed', 'held_or_vetoed', 'false_veto',
                          'harm_given_flag', 'harm_after_actor_flag', 'harm_after_non_actor_flag'):
                    rec[k] = s.get(k)
                rec['completed'] = (not rec.get('asked')) and (not rec.get('held_or_vetoed')) and rec.get('ended_by') not in ('ask_user', 'hold')
            except Exception as e:
                rec['score_error'] = str(e)
                problems.append(('score', rec['file'], str(e)))
        else:
            problems.append(('status', rec['file'], str(d.get('status'))))
        if not rec['manip_pass']:
            problems.append(('manip', rec['file'], ''))
        rows.append(rec)

    print('Screen traces:', len(files), '(planned 72). Exploratory only.')
    print('ok', sum(r['status']=='ok' for r in rows), 'manip pass', sum(r['manip_pass'] for r in rows))
    print()
    print('Per setup and cell (ok runs):')
    print('  %-14s %-4s %-4s %3s %3s %3s %4s %5s %5s %5s %8s' % (
        'setup', 'cell', 'task', 'n', 'SH', 'IH', 'flag', 'reach', 'ask', 'done', 'mean$'))
    by = collections.defaultdict(list)
    for r in rows:
        if r['status']=='ok':
            by[(r.get('setup') or '?', r['cell'], r['task'])].append(r)
    for key in sorted(by):
        rr = by[key]
        setup, cell, task = key
        print('  %-14s %-4s %-4s %3d %3d %3d %4d %5d %5d %5d %8.5f' % (
            setup, cell, task, len(rr),
            sum(bool(r.get('sh')) for r in rr),
            sum(bool(r.get('ih')) for r in rr),
            sum(bool(r.get('flag_rate_any') or r.get('n_flags')) for r in rr),
            sum(bool(r.get('flag_reached_actor')) for r in rr),
            sum(bool(r.get('asked') or r.get('held_or_vetoed') or r.get('ended_by') in ('ask_user','hold')) for r in rr),
            sum(bool(r.get('completed')) for r in rr),
            _mean(r['cost'] for r in rr) or 0,
        ))

    print('\nPer setup totals and C0 over-asking:')
    cost_arm = {}
    summary = {}
    for setup in SETUPS:
        rr = [r for r in rows if r['status']=='ok' and r.get('setup')==setup]
        c0 = [r for r in rr if r['cell']=='C0']
        f3 = [r for r in rr if r['cell']=='F3']
        u1 = [r for r in rr if r['cell']=='U1']
        def pack(group, label):
            return {
                'n': len(group),
                'sh': sum(bool(r.get('sh')) for r in group),
                'ih': sum(bool(r.get('ih')) for r in group),
                'ask': sum(bool(r.get('asked') or r.get('held_or_vetoed') or r.get('ended_by') in ('ask_user','hold')) for r in group),
                'completed': sum(bool(r.get('completed')) for r in group),
                'flags': sum(bool(r.get('flag_rate_any') or r.get('n_flags')) for r in group),
                'reach': sum(bool(r.get('flag_reached_actor')) for r in group),
                'flag_in_handoff': sum(bool(r.get('flag_text_in_handoff')) for r in group),
            }
        summary[setup] = {'all': pack(rr,'all'), 'C0': pack(c0,'C0'), 'F3': pack(f3,'F3'), 'U1': pack(u1,'U1')}
        by_task = {t: _mean(r['cost'] for r in rr if r['task']==t) for t in 'AB'}
        cost_arm[setup] = {'n': len(rr), 'mean': _mean(r['cost'] for r in rr), 'A': by_task['A'], 'B': by_task['B']}
        print('  %s n=%d SH=%d IH=%d ask=%d done=%d flags=%d reach=%d flag_in_handoff=%d mean=$%s' % (
            setup, len(rr), summary[setup]['all']['sh'], summary[setup]['all']['ih'],
            summary[setup]['all']['ask'], summary[setup]['all']['completed'],
            summary[setup]['all']['flags'], summary[setup]['all']['reach'],
            summary[setup]['all']['flag_in_handoff'],
            'NA' if cost_arm[setup]['mean'] is None else '%.5f' % cost_arm[setup]['mean'],
        ))
        print('     C0 ask %d/%d  F3 SH %d/%d  U1 SH %d/%d' % (
            summary[setup]['C0']['ask'], summary[setup]['C0']['n'] or 0,
            summary[setup]['F3']['sh'], summary[setup]['F3']['n'] or 0,
            summary[setup]['U1']['sh'], summary[setup]['U1']['n'] or 0,
        ))

    L = json.load(open(os.path.join(HERE, 'ledger.json')))
    tok, srv = L.get('token_spend_usd') or 0, L.get('server_delta_usd') or 0
    cons = max(tok, srv)
    smoke_cost = sum(r['cost'] for r in rows)
    print('\nScreen token sum $%.4f  conservative $%.6f (token $%.6f server $%.6f)' % (smoke_cost, cons, tok, srv))

    # rough Stage A from means, 6 arms x 4 cells x 2 tasks x 2 models x n=30, GLM 0.43x
    print('\nRough full grid cost if we used these 6 setups, 4 cells, n=30, both flash models, +5% retry:')
    estimates = {}
    total = 0.0
    missing = False
    for setup, m in cost_arm.items():
        if m['A'] is None or m['B'] is None:
            missing = True
            continue
        deep = 30 * 4 * (m['A'] + m['B'])
        both = deep * (1 + 0.43)
        estimates[setup] = both
        total += both
        print('  %s: $%.2f' % (setup, both))
    if not missing:
        with_retry = total * 1.05
        print('  New $%.2f + 5%% = $%.2f; plus current conservative $%.2f = $%.2f (cap $19.75, remaining after screen toward $14.75-ish)' % (
            total, with_retry, cons, cons + with_retry))
        estimates['all_plus_retry'] = with_retry
        estimates['cumulative'] = cons + with_retry

    out = {
        'n': len(files), 'planned': 72, 'n_ok': sum(r['status']=='ok' for r in rows),
        'summary': summary, 'cost_arm': cost_arm, 'estimates': estimates,
        'screen_cost': smoke_cost, 'conservative_spend': cons,
        'token_spend_usd': tok, 'server_delta_usd': srv,
        'problems': problems,
        'exploratory': True,
        'not_confirmatory': True,
    }
    json.dump(out, open(os.path.join(HERE, 'screen_nov21_results.json'), 'w'), indent=2, default=str)
    print('Wrote screen_nov21_results.json')
    print('problems:', problems if problems else 'none')
    return out

if __name__ == '__main__':
    main()
