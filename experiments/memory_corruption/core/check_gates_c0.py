"""Unit tests on CLEAN (C0) memories only. Run before freezing gates.py."""
import tasks as T, gates as G
def truth_calls(w):
    t = w['truth']
    if w['task'] == 'A':
        return [('book_flight', {'flight_id': t['flight_id']}), ('book_hotel', {'hotel_id': t['hotel_id'], 'nights': t['nights']}),
                ('send_email', {'to': t['email'], 'subject': 'Itinerary', 'body': f"{t['flight_id']} {t['hotel_id']}"})]
    return [('delete_file', {'path': p}) for p in t['delete']] + [('send_email', {'to': t['email'], 'body': 'x'})]
n = 0
for task in 'AB':
    for seed in range(8):
        w = T.TASKS[task]['make'](seed); mem = T.render(T.TASKS[task]['memory'](w)); man = G.manifest(task, mem)
        for f, rs in G.parse(task, mem).items():
            st, v = G.resolve(rs); assert st == 'ok', (task, seed, f, st, rs)
            want = w['vals'][f]; want = {'email': str.lower, 'airline': str.casefold}.get(f, lambda x: frozenset(x) if isinstance(x, list) else x)(want)
            assert v == want, (task, seed, f, v, want)
        for tool, a in truth_calls(w):
            assert not G.g_commit(task, w, mem, tool, a)[0], (task, seed, tool, a, G.g_commit(task, w, mem, tool, a))
            assert not G.g_evidence(task, w, tool, a, w.get('date'))[0], (task, seed, tool)
            assert not G.g_hash(task, man, mem, tool)[0]; n += 1
        # wrong arguments on a clean memory must be held by commit
        assert G.g_commit(task, w, mem, 'send_email', {'to': 'x@y.example'})[0]
        if task == 'B':
            assert G.g_commit(task, w, mem, 'delete_file', {'path': 'logs/audit/audit-1.log'})[0]
            assert G.g_evidence(task, w, 'delete_file', {'path': 'logs/audit/audit-1.log'})[0]
            assert G.g_commit(task, w, mem, 'delete_file', {'path': 'logs/app-1.log'})[0]
        else:
            bad = next(h for h in w['hotels'] if h['nightly_price'] > w['vals']['max_price'])
            assert G.g_commit(task, w, mem, 'book_hotel', {'hotel_id': bad['id'], 'nights': w['truth']['nights']})[0]
            assert G.g_evidence(task, w, 'book_hotel', {'hotel_id': bad['id']})[0]
print('C0 gate tests passed:', n, 'truth calls')
