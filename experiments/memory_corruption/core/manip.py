"""Manipulation check (Amendment 2). A run passes when:
 1. loaded: the memory text in the run equals the file the damage generator produces for (task, seed, cell);
 2. reached: every line the damage ADDED (or changed) appears in the planner's and the executor's first prompt;
 3. removed: no line the damage REMOVED appears in those prompts (for deletion and truncation types);
 4. for damaged cells, at least one line differs from the clean file (the damage was applied).
Runs that fail are dropped from analysis."""
import tasks as T

def diff(clean, mem):
    c, m = clean.splitlines(), mem.splitlines()
    return [l for l in m if l not in c and l.strip()], [l for l in c if l not in m and l.strip()]

def check(task, seed, cell, mem, prompts, regen_mem=None):
    clean = T.render(T.TASKS[task]['memory'](T.TASKS[task]['make'](seed)))
    added, removed = diff(clean, mem)
    out = {'loaded': (regen_mem is None or regen_mem == mem) and all(mem in p for p in prompts) and bool(prompts),
           'applied': cell == 'C0' or bool(added or removed),
           'added_lines': len(added), 'removed_lines': len(removed),
           'added_reached': all(all(l in p for p in prompts) for l in added),
           'removed_absent': all(all(l.strip() not in {x.strip() for x in p.splitlines()} for p in prompts) for l in removed)}
    out['pass'] = out['loaded'] and out['applied'] and out['added_reached'] and out['removed_absent']
    return out

def memory_roles(trace):
    """Which roles must contain MEMORY.md for the manipulation check.
    Amendment 8/9 scoped pipelines: the planner only.
    Single-agent S: the S role only.
    Legacy Amendment 7 (no arm): planner and executor, who both saw memory."""
    arm = (trace or {}).get('base_arm') or (trace or {}).get('arm')
    if arm in ('S', 'S_full', 'S_scoped'):
        return ('S',)
    if arm in ('P', 'P_V_plan', 'P_V_raw',
               'P_full', 'P_scoped',
               'P_full_Vplan', 'P_scoped_Vplan',
               'P_full_Vraw', 'P_scoped_Vraw'):
        return ('planner',)
    return ('planner', 'executor')

def prompts_of(trace):
    """User prompts that are required to carry MEMORY.md, depending on the arm."""
    ps = []
    for role in memory_roles(trace):
        c = next((c for c in trace['calls'] if c['role'] == role), None)
        if c: ps.append('\n'.join(m['content'] for m in c['messages'] if m['role'] == 'user'))
    return ps
