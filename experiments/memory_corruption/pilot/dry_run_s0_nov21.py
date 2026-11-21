"""Zero-cost dry run of S0 sources, mocked hops, codebook, and job counts. No API, no key, no ledger."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import s0_source as S
import s0_grade as G
import s0_summarize as SM
import run_s0_nov21 as R

OUT = os.path.join(HERE, 'dry_run_s0_nov21.txt')


def script_for(cell, pattern):
    """Build mock summaries that mention truth/typo according to a pattern of states."""
    truth, typo = cell['gap_true'], cell['gap_neighbor']
    pad = ' ' + 'brief ' * 20
    table = {
        'disagreement': 'Plan mentions %s and also %s in listings.%s' % (typo, truth, pad),
        'typo_only': 'Plan uses %s as the flight and omits the other id.%s' % (typo, pad),
        'truth_only': 'Plan uses %s as the flight and omits the typo.%s' % (truth, pad),
        'neither': 'Plan omits the target flight identifier entirely.%s' % pad,
        'invalid': '{"flight_id": "%s"}' % typo,
    }
    return [table[p] for p in pattern]


def main():
    lines = []
    def p(s=''):
        lines.append(s)

    counts = R.expected_counts()
    p('S0 zero-cost dry run (mocked summaries, no API calls)')
    p('jobs: %s' % counts)
    p('')

    # Render sources
    for tmpl in S.TEMPLATES:
        for seed in (30, 60):
            for cond in ('typo', 'clean'):
                cell = S.make_annotated_cell(tmpl, seed, cond)
                src = S.build_source(cell)
                ok, reasons = S.source_ok(cell, src)
                p('--- source %s seed=%s %s ok=%s reasons=%s chars=%d ---' % (
                    tmpl, seed, cond, ok, reasons, len(src)))
                p('truth=%s typo=%s verbatim=%s' % (cell['gap_true'], cell['gap_neighbor'], S.extract_verbatim_value(src)))
                # Do not dump full trip_long (long); show head and markers only for long
                if len(src) > 1500:
                    p(src[:700])
                    p('... [%d chars omitted] ...' % (len(src) - 1100))
                    p(src[-400:])
                else:
                    p(src)
                p('')

    # Mock composition chain on trip seed 30 typo
    cell = S.make_annotated_cell('trip', 30, 'typo')
    src = S.build_source(cell)
    llm = SM.MockLLM(script_for(cell, ['disagreement', 'typo_only', 'typo_only', 'typo_only']))
    hops, tr = SM.run_hops(cell, src, llm, n_hops=4, prompt_name='p1', temperature=0.0, seed=30)
    p('--- mocked depth-4 chain trip seed 30 typo, cost=$%.4f mocked=%s ---' % (tr['cost'], True))
    for h in hops:
        g = h['grade']
        p('  hop %d state=%s typo=%s truth=%s valid=%s words=%s' % (
            h['hop'], g['state'], g['typo_present'], g['truth_present'], g['valid'], g['words']))
    p('')

    # Mock channel pair: one typo_only, one truth_only => visible conflict
    llm2 = SM.MockLLM(script_for(cell, ['typo_only']))
    hops2, _ = SM.run_hops(cell, src, llm2, n_hops=1, prompt_name='p1', temperature=1.0, seed=600)
    llm3 = SM.MockLLM(script_for(cell, ['truth_only']))
    hops3, _ = SM.run_hops(cell, src, llm3, n_hops=1, prompt_name='p2', temperature=1.0, seed=601)
    vtext = S.extract_verbatim_channel(src, cell)
    vg = G.code_text(vtext, cell['gap_true'], cell['gap_neighbor'], cell=cell, source=src)
    p('--- mocked channels ---')
    p('verbatim state=%s value=%s' % (vg['state'], S.extract_verbatim_value(src)))
    p('ds_p1 state=%s' % hops2[0]['grade']['state'])
    p('ds_p2 state=%s' % hops3[0]['grade']['state'])
    pair = G.correlation_from_pairs([{
        'template': 'trip',
        'c1': hops2[0]['grade'], 'c2': hops3[0]['grade'],
    }])
    p('pair typo_only both=%s p1=%s p2=%s' % (pair.get('p_both'), pair.get('p1'), pair.get('p2')))
    p('')
    p('No API calls were made. Ledger was not written. Key was not read.')
    text = '\n'.join(lines) + '\n'
    open(OUT, 'w').write(text)
    print(text)
    print('wrote', OUT)


if __name__ == '__main__':
    main()
