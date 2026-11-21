"""Zero-cost dry run: render conflict 2x2 handoffs. No network, no key, no ledger."""
import os
import sys
import json

# Refuse if someone tries to turn this into a paid run.
if any(k.startswith('OPENAI') or k.endswith('API_KEY') or 'SECRET' in k for k, v in os.environ.items() if v):
    # do not print values; just do not load or use a client
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perturb_gaps as PG
import templates_v3 as TV
import test_conflict_v3_nov21 as T

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'dry_run_conflict_v3.txt')

ARMS = list(T.PRIMARY) + [
    ('P_conflict_rule', 'conflict_rule', PG.ORDER_SUMMARY_THEN_VERBATIM),
    ('P_reversed_conflict_rule', 'reversed_conflict_rule', PG.ORDER_SUMMARY_THEN_VERBATIM),
]


def main():
    lines = []
    def p(s=''):
        lines.append(s)
    p('ZERO-COST DRY RUN of the reversed-conflict 2x2. No API calls. 21 November 2026, Pacific Time.')
    p('Each arm is rendered from the same plan and tool results. Only the channel values and the order change.')
    p('')
    n = 0
    for tmpl in TV.TEMPLATES:
        for seed in (0, 1):
            p('=' * 78)
            p('%s seed %s' % (tmpl, seed))
            for name, form, order in ARMS:
                cell = T._cell(tmpl, seed, form, order)
                hand, tr = T._prepare(cell)
                parts = PG.parse_conflict_handoff(hand)
                n += 1
                p('')
                p('--- %s  form=%s  order=%s  summary_holds=%s  verbatim_holds=%s ---' % (
                    name, form, order, tr.get('summary_holds'), tr.get('verbatim_holds')))
                p('truth=%s  neighbor=%s  slot=%s  parse_ok=%s' % (
                    cell['gap_true'], cell['gap_neighbor'], cell['gap_slot'], parts.get('ok')))
                p(hand)
            # identity reminder for the four primary arms of this cell
            p('')
    p('Rendered %d handoffs (6 templates x 2 seeds x 6 arms). No API spend.' % n)
    text = '\n'.join(lines) + '\n'
    open(OUT, 'w').write(text)
    print(text)
    print('wrote', OUT)


if __name__ == '__main__':
    main()
