"""Held-out damage types (plan_v3 freeze step 3). Written AFTER gates.py and damage.py were frozen.
HO1: partial overwrite: the field's line (and the line after it) replaced by well-formed unrelated text, no placeholder. Post-write.
HO2: wrong value carrying a wrong but well-formed NEWER date, plus a nearby undated note restating the correct value. At-write.
"""
import copy
import tasks as T
import damage as D

UNRELATED = {'A': ["- Loyalty numbers are in the password manager, not here", "- Likes hotels within walking distance of transit"],
             'B': ["- The CI runner caches dependencies between jobs", "- Code review happens on Tuesdays and Thursdays"]}

def make_heldout(task, seed, cell):
    w = T.TASKS[task]['make'](seed); clean_mem = T.TASKS[task]['memory'](w); clean = T.render(clean_mem)
    mem = copy.deepcopy(clean_mem); v = w['vals']; wrong = w['wrong']; LINE = T.TASKS[task]['line']
    field = D.f_field(task, seed, cell); si, li, line = D._loc(mem, task, field, v)
    lines = mem['sections'][si][1]
    if cell == 'HO1':
        lines[li:li + 2] = UNRELATED[task]
        text = T.render(mem); trusted = clean; wp = 'post'
    elif cell == 'HO2':
        lines.insert(li + 1, D.dated(LINE[field](wrong[field]), '2026-11-12'))
        lines.insert(li + 2, f"- Note: {D.undated(line)[2:]} (still current)")
        text = T.render(mem); trusted = text; wp = 'at'
    else:
        raise ValueError(cell)
    return {'task': task, 'seed': seed, 'cell': cell, 'field': field, 'mem': text, 'trusted': trusted, 'clean': clean,
            'world': w, 'write_path': wp}

def make_any(task, seed, cell):
    return make_heldout(task, seed, cell) if cell in ('HO1', 'HO2') else D.make_cell(task, seed, cell)
