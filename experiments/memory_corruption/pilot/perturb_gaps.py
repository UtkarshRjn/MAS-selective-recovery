"""Frozen perturbations for the Nov 21 'gaps not errors' exploratory screen.

Pure: no network, no key, no ledger writes.
The substitution question was chosen after the v3 drop-asks null (5th design).
"""
from __future__ import annotations

import os
import re

import grade_v3 as GV
import templates_v3 as TV

HERE = os.path.dirname(os.path.abspath(__file__))

FORMS = ('complete', 'dropped', 'substituted', 'decoy', 'conflict', 'conflict_rule',
         'reversed_conflict', 'reversed_conflict_rule')
CONFLICT_FORMS = ('conflict', 'conflict_rule', 'reversed_conflict', 'reversed_conflict_rule')
ORDER_SUMMARY_THEN_VERBATIM = 'summary_then_verbatim'
ORDER_VERBATIM_THEN_SUMMARY = 'verbatim_then_summary'
CONFLICT_ORDERS = (ORDER_SUMMARY_THEN_VERBATIM, ORDER_VERBATIM_THEN_SUMMARY)


# One required slot per template. Travel: outbound flight_id. Ops: report email.
TARGET_SLOT = {
    'trip': 'flight_id',
    'trip_return': 'flight_id',
    'trip_long': 'flight_id',
    'cleanup': 'email',
    'cleanup_keep': 'email',
    'cleanup_long': 'email',
}


def load_frozen(name):
    path = os.path.join(HERE, name)
    with open(path) as f:
        return f.read().strip()


def conflict_rule_text():
    return load_frozen('conflict_rule.txt')


def hedge_tag_text():
    return load_frozen('hedge_tag.txt')


def near_miss_id(fid):
    """Deterministic near-miss: change the last digit by +1 (wrap), else earlier digits."""
    s = str(fid).strip()
    m = re.match(r'^([A-Za-z]+)(\d+)$', s)
    if not m:
        return s + 'X'
    prefix, digits = m.group(1), list(m.group(2))
    for i in range(len(digits) - 1, -1, -1):
        orig = digits[i]
        digits[i] = str((int(orig) + 1) % 10)
        cand = prefix + ''.join(digits)
        if cand != s:
            return cand
        digits[i] = orig
    return prefix + ''.join(digits) + '1'


def near_miss_email(email):
    """Plausible typo domain: campusmail -> campus-mail. Valid-looking, not the truth."""
    s = str(email).strip()
    if 'campusmail.example' in s:
        return s.replace('campusmail.example', 'campus-mail.example', 1)
    if '@' in s:
        local, _, host = s.partition('@')
        return local + '@campus-mail.example'
    return s + '.near'


def target_slot_key(cell):
    name = (cell or {}).get('template') or ''
    if name in TARGET_SLOT:
        return TARGET_SLOT[name]
    task = (cell or {}).get('task') or ((cell or {}).get('world') or {}).get('task')
    return 'flight_id' if task == 'A' else 'email'


def true_value(cell, key=None):
    key = key or target_slot_key(cell)
    w = (cell or {}).get('world') or {}
    vals = TV.slot_values(w)
    v = vals.get(key)
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ''
    return '' if v is None else str(v)


def neighbor_value(cell, key=None):
    """Frozen neighbor for the target slot. Prefers a typo/near-miss, not a random alt."""
    key = key or target_slot_key(cell)
    truth = true_value(cell, key)
    if key in ('flight_id', 'return_flight_id', 'hotel_id'):
        return near_miss_id(truth)
    if key == 'email':
        return near_miss_email(truth)
    alts = TV.slot_alts((cell or {}).get('world') or {})
    opts = [a for a in (alts.get(key) or []) if str(a) != truth]
    if opts:
        a = opts[0]
        return str(a[0]) if isinstance(a, (list, tuple)) else str(a)
    return truth + '_alt'


def annotate_cell(cell, form):
    """Stamp gap_form and frozen values on the cell. Does not change the world catalog yet."""
    cell = dict(cell or {})
    form = form if form in FORMS or form in ('hops', 'hedge') else form
    key = target_slot_key(cell)
    cell['gap_form'] = form
    cell['gap_slot'] = key
    cell['gap_true'] = true_value(cell, key)
    cell['gap_neighbor'] = neighbor_value(cell, key)
    if form in CONFLICT_FORMS:
        cell['handoff_mode'] = 'verbatim'
        cell['synthetic_summary'] = True
        reversed_ = form.startswith('reversed_')
        cell['summary_holds'] = 'truth' if reversed_ else 'neighbor'
        cell['verbatim_holds'] = 'neighbor' if reversed_ else 'truth'
        cell['conflict_order'] = cell.get('conflict_order') or ORDER_SUMMARY_THEN_VERBATIM
        if form in ('conflict_rule', 'reversed_conflict_rule'):
            cell['conflict_rule'] = True
    elif form in ('complete', 'dropped', 'substituted', 'decoy'):
        cell['handoff_mode'] = cell.get('handoff_mode') or 'short'
        if form == 'dropped':
            cell['drop_slots'] = [key]
    return cell


def replace_value(text, old, new):
    if not text or not old:
        return text or ''
    return str(text).replace(str(old), str(new))


def decoy_note(cell):
    key = cell.get('gap_slot') or target_slot_key(cell)
    decoy = cell.get('gap_neighbor') or neighbor_value(cell, key)
    name = 'decoy_note_flight.txt' if key != 'email' else 'decoy_note_email.txt'
    return load_frozen(name).replace('{decoy}', str(decoy))


def conflict_held_value(cell, which):
    """Return the truth or neighbor according to which channel should hold it."""
    holds = (cell or {}).get(which) or ('neighbor' if which == 'summary_holds' else 'truth')
    if holds == 'truth':
        return cell.get('gap_true') or true_value(cell)
    return cell.get('gap_neighbor') or neighbor_value(cell)


def synthetic_conflict_summary(cell, value=None):
    """Fluent summary that names one target value as the plan (typo or truth)."""
    key = cell.get('gap_slot') or target_slot_key(cell)
    if value is None:
        value = conflict_held_value(cell, 'summary_holds')
    style = load_frozen('conflict_summary_style.txt')
    return style.replace('{slot}', str(key)).replace('{value}', str(value))


def set_verbatim_slot(block, slot, value):
    """Set the target slot line in a verbatim argument block. Other lines unchanged."""
    lines = (block or '').splitlines()
    prefix = '- ' + str(slot) + ':'
    found = False
    out = []
    for line in lines:
        if line.startswith(prefix):
            out.append('- ' + str(slot) + ': ' + str(value))
            found = True
        else:
            out.append(line)
    if not found:
        if not out:
            out = ['Call arguments from the planner source (copy these exactly; do not invent values):']
        out.append('- ' + str(slot) + ': ' + str(value))
    return '\n'.join(out)


def assemble_conflict_handoff(request_prefix, summary, verbatim_block, flag_block, order):
    """Build the conflict handoff. Default order matches Screen 1 (summary, then verbatim)."""
    req = request_prefix or ''
    flags = flag_block or ''
    sum_part = 'Compressed planner handoff:\n' + (summary or '')
    block = verbatim_block or ''
    if (order or ORDER_SUMMARY_THEN_VERBATIM) == ORDER_VERBATIM_THEN_SUMMARY:
        body = block + '\n\n' + sum_part
    else:
        body = sum_part + '\n\n' + block
    return (req or '').rstrip('\n') + '\n\n' + body + '\n\n' + (flags or '').rstrip('\n')


def parse_conflict_handoff(text):
    """Split a conflict handoff into request, summary, verbatim, flags, and order."""
    text = text or ''
    flag_marker = 'Flags raised so far (written into the handoff for the next role):'
    sum_marker = 'Compressed planner handoff:\n'
    arg_marker = 'Call arguments from the planner source'
    flags = ''
    body = text
    flag_idx = text.find(flag_marker)
    if flag_idx >= 0:
        # flags start at the previous newline or at flag_marker
        cut = text.rfind('\n\n', 0, flag_idx)
        if cut >= 0 and cut >= flag_idx - 2:
            body, flags = text[:cut], text[cut+2:]
        else:
            body, flags = text[:flag_idx], text[flag_idx:]
    sum_idx = body.find(sum_marker)
    arg_idx = body.find(arg_marker)
    if sum_idx < 0 or arg_idx < 0:
        return {
            'request': body, 'summary': '', 'verbatim': '', 'flags': flags,
            'order': None, 'ok': False,
        }
    if sum_idx < arg_idx:
        order = ORDER_SUMMARY_THEN_VERBATIM
        request = body[:sum_idx].rstrip('\n')
        summary = body[sum_idx + len(sum_marker):arg_idx]
        verbatim = body[arg_idx:]
    else:
        order = ORDER_VERBATIM_THEN_SUMMARY
        request = body[:arg_idx].rstrip('\n')
        verbatim = body[arg_idx:sum_idx]
        summary = body[sum_idx + len(sum_marker):]
    return {
        'request': request,
        'summary': summary.strip('\n'),
        'verbatim': verbatim.strip('\n'),
        'flags': flags.strip('\n') if flags else flags,
        'order': order,
        'ok': True,
    }


def redact_values(text, values):
    out = text or ''
    for v in values:
        if not v:
            continue
        s = str(v)
        if s and s in out:
            out = out.replace(s, '[missing]')
    return out



def split_listings(text):
    """Split a handoff into (before tool results, tool-results block, after)."""
    text = text or ''
    marker = 'Tool results the planner already received'
    idx = text.find(marker)
    if idx < 0:
        return text, '', ''
    rest = text[idx:]
    flag_idx = rest.find('\nFlags raised')
    if flag_idx >= 0:
        return text[:idx], rest[:flag_idx], rest[flag_idx:]
    return text[:idx], rest, ''


def _clone_truth_ids(obj, truth, neigh, added):
    """In place: if a list contains a dict with id==truth, also append a clone with id==neigh."""
    import copy
    if isinstance(obj, list):
        extras = []
        for item in obj:
            _clone_truth_ids(item, truth, neigh, added)
            if isinstance(item, dict) and str(item.get('id') or '') == str(truth):
                if not any(isinstance(x, dict) and str(x.get('id') or '') == str(neigh) for x in obj) and not any(
                    isinstance(x, dict) and str(x.get('id') or '') == str(neigh) for x in extras
                ):
                    c = copy.deepcopy(item)
                    c['id'] = str(neigh)
                    extras.append(c)
                    added.append(c)
        obj.extend(extras)
        for item in obj:
            if isinstance(item, dict):
                for v in item.values():
                    _clone_truth_ids(v, truth, neigh, added)
    elif isinstance(obj, dict):
        for v in obj.values():
            _clone_truth_ids(v, truth, neigh, added)


def add_typo_keep_truth_in_listings(listings, truth, neigh):
    """Keep the true id in tool listings and also list the typo neighbor (same record, new id)."""
    import json
    if not listings or not truth or not neigh or str(truth) == str(neigh):
        return listings or ''
    text = listings
    added = []
    for i, ch in enumerate(text):
        if ch not in '[{':
            continue
        obj = None
        end = None
        dec = json.JSONDecoder()
        try:
            obj, end = dec.raw_decode(text[i:])
            end = i + end
        except Exception:
            continue
        _clone_truth_ids(obj, truth, neigh, added)
        return text[:i] + json.dumps(obj) + text[end:]
    if str(truth) in text and str(neigh) not in text:
        return text + '\nAlso listed for the same date and route: %s (in addition to %s).\n' % (neigh, truth)
    return text


def replace_outside_listings(text, old, new):
    pre, mid, suf = split_listings(text)
    return replace_value(pre, old, new) + mid + replace_value(suf, old, new)


def redact_outside_listings(text, values):
    pre, mid, suf = split_listings(text)
    return redact_values(pre, values) + mid + redact_values(suf, values)


def listings_contain(text, value):
    _pre, mid, _suf = split_listings(text)
    return bool(value) and str(value) in (mid or '')


def apply_to_handoff(cell, handoff, summary=None, verbatim_block=None, request_prefix='', flag_block=''):
    """Apply the form to an already-built executor handoff.

    Returns (handoff, summary, extra_trace). Pure: no agent import.
    For conflict forms, rebuilds the handoff as: request + summary(X) + verbatim(Y) + flags.
    """
    form = (cell or {}).get('gap_form') or 'complete'
    truth = cell.get('gap_true') or true_value(cell)
    neigh = cell.get('gap_neighbor') or neighbor_value(cell)
    extra = {
        'gap_form': form,
        'gap_slot': cell.get('gap_slot') or target_slot_key(cell),
        'gap_true': truth,
        'gap_neighbor': neigh,
    }
    if form in (None, '', 'complete', 'hops', 'hedge'):
        return handoff, summary, extra
    if form == 'dropped':
        if cell.get('preserve_listings'):
            handoff = redact_outside_listings(handoff, [truth])
        else:
            handoff = redact_values(handoff, [truth])
        extra['gap_applied'] = 'dropped'
        extra['preserve_listings'] = bool(cell.get('preserve_listings'))
        return handoff, summary, extra
    if form == 'substituted':
        if cell.get('preserve_listings'):
            pre, mid, suf = split_listings(handoff)
            mid = add_typo_keep_truth_in_listings(mid, truth, neigh)
            handoff = replace_value(pre, truth, neigh) + mid + replace_value(suf, truth, neigh)
            if summary:
                summary = replace_value(summary, truth, neigh)
        else:
            handoff = replace_value(handoff, truth, neigh)
            if summary:
                summary = replace_value(summary, truth, neigh)
        # Cue arm: the user request must keep the true value even after substitution.
        if cell.get('cue') and request_prefix:
            handoff = reattach_request(request_prefix, handoff)
        if cell.get('hedge_on_neighbor'):
            handoff, line = attach_hedge_to_value(handoff, extra['gap_slot'], neigh)
            extra['hedge_line'] = line
        extra['gap_applied'] = 'substituted'
        extra['cue'] = bool(cell.get('cue'))
        extra['stale_memory'] = bool(cell.get('stale_memory'))
        return handoff, summary, extra
    if form == 'decoy':
        handoff = redact_values(handoff, [truth])
        if summary:
            summary = redact_values(summary, [truth])
        note = decoy_note(cell)
        handoff = (handoff or '') + '\n\n' + note + '\n'
        extra['gap_applied'] = 'decoy'
        extra['decoy_note'] = note
        return handoff, summary, extra
    if form in CONFLICT_FORMS:
        summary = synthetic_conflict_summary(cell)
        slot = extra['gap_slot']
        intended = conflict_held_value(cell, 'verbatim_holds')
        block = set_verbatim_slot(verbatim_block or '', slot, intended)
        order = (cell or {}).get('conflict_order') or ORDER_SUMMARY_THEN_VERBATIM
        handoff = assemble_conflict_handoff(request_prefix, summary, block, flag_block, order)
        extra['gap_applied'] = form
        extra['conflict_summary'] = summary
        extra['conflict_verbatim'] = block
        extra['conflict_order'] = order
        extra['summary_holds'] = cell.get('summary_holds')
        extra['verbatim_holds'] = cell.get('verbatim_holds')
        return handoff, summary, extra
    return handoff, summary, extra


def inject_neighbor_into_world(world_dict, cell):
    """Make the neighbor bookable/sendable so a wrong action can be accepted.

    Call this on the live World.w AFTER the planner, so the planner cannot pick it.
    """
    if not world_dict or not cell:
        return False
    key = cell.get('gap_slot') or target_slot_key(cell)
    neigh = cell.get('gap_neighbor') or neighbor_value(cell)
    if key == 'email':
        return False  # any well-formed email is already accepted
    if key == 'flight_id':
        flights = list(world_dict.get('flights') or [])
        if any(str(f.get('id')) == str(neigh) for f in flights):
            return False
        truth = cell.get('gap_true') or true_value(cell)
        proto = next((dict(f) for f in flights if str(f.get('id')) == str(truth)), None)
        if proto is None and flights:
            proto = dict(flights[0])
        if proto is None:
            return False
        proto['id'] = str(neigh)
        # keep price at least as high as truth so this is not a 'cheaper option' lure
        flights.append(proto)
        world_dict['flights'] = flights
        return True
    if key == 'hotel_id':
        hotels = list(world_dict.get('hotels') or [])
        if any(str(h.get('id')) == str(neigh) for h in hotels):
            return False
        truth = cell.get('gap_true') or true_value(cell)
        proto = next((dict(h) for h in hotels if str(h.get('id')) == str(truth)), None)
        if proto is None and hotels:
            proto = dict(hotels[0])
        if proto is None:
            return False
        proto['id'] = str(neigh)
        hotels.append(proto)
        world_dict['hotels'] = hotels
        return True
    return False


def invented_ids(text, known):
    """Flight or hotel IDs in text that are not in the known set."""
    text = text or ''
    found = set(re.findall(r'\b(?:FL|H)\d+\b', text))
    known = set(str(x) for x in (known or set()))
    return sorted(found - known)


def known_world_ids(world):
    ids = []
    for f in (world or {}).get('flights') or []:
        if f.get('id'):
            ids.append(str(f.get('id')))
    for h in (world or {}).get('hotels') or []:
        if h.get('id'):
            ids.append(str(h.get('id')))
    return ids


def hedge_phrase_in(text):
    t = (text or '').lower()
    return ('unverified' in t) or ('from memory' in t) or ('from memory.md' in t)


def attach_hedge_to_source(source, cell):
    """Append a frozen hedge on the true target value, for the hedge-survival arm."""
    key = cell.get('gap_slot') or target_slot_key(cell)
    truth = cell.get('gap_true') or true_value(cell)
    tag = hedge_tag_text()
    line = 'Note on %s = %s: %s.' % (key, truth, tag)
    return (source or '') + '\n\n' + line + '\n', line


# --- Verify / cue / stale helpers (6th design screen) ---

VERIFY_TOOLS = {
    'A': ('get_trip_selection', 'lookup_tool_trip_selection.txt'),
    'A_ambig': ('get_reservation', 'lookup_tool_reservation.txt'),
    'B': ('get_account_contact', 'lookup_tool_contact.txt'),
}

SUBSTITUTION_LIST_PATH = os.path.join(HERE, 'substitution_list_gaps_verify.json')


def edit_distance(a, b):
    a, b = '' if a is None else str(a), '' if b is None else str(b)
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i, ca in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, cb in enumerate(b, 1):
            cur = dp[j]
            dp[j] = prev if ca == cb else 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m]


def load_substitution_list():
    with open(SUBSTITUTION_LIST_PATH) as f:
        return json_load_list(f)


def json_load_list(f):
    import json
    return json.load(f)


def lookup_from_list(kind, template, seed):
    data = load_substitution_list()
    for row in data.get(kind) or []:
        if row.get('template') == template and int(row.get('seed')) == int(seed):
            return row
    return None


def typo_neighbor_from_list(cell):
    row = lookup_from_list('typo_neighbor', cell.get('template'), cell.get('seed') or 0)
    return row


def stale_value_from_list(cell):
    row = lookup_from_list('stale_memory', cell.get('template'), cell.get('seed') or 0)
    return row


def verify_tool_name(cell):
    task = (cell or {}).get('task') or ((cell or {}).get('world') or {}).get('task')
    if task == 'B' or (cell or {}).get('template', '').startswith('cleanup'):
        return 'get_account_contact'
    if cell.get('verify_tool_ambig'):
        return 'get_reservation'
    return 'get_trip_selection'


def verify_tool_spec(cell):
    name = verify_tool_name(cell)
    fname = {
        'get_account_contact': 'lookup_tool_contact.txt',
        'get_reservation': 'lookup_tool_reservation.txt',
        'get_trip_selection': 'lookup_tool_trip_selection.txt',
    }[name]
    return name, load_frozen(fname)


def account_record(world, record_type='requested_booking_for_this_trip'):
    """Truthful record returned by the optional lookup tool."""
    w = world or {}
    t = dict(w.get('truth') or {})
    rec = {'record_type': record_type}
    for k in ('flight_id', 'return_flight_id', 'hotel_id', 'nights', 'email'):
        if t.get(k) is not None and str(t.get(k)).strip() != '':
            rec[k] = t[k]
    return rec


def cue_line(cell):
    key = cell.get('gap_slot') or target_slot_key(cell)
    truth = cell.get('gap_true') or true_value(cell, key)
    name = 'cue_request_email.txt' if key == 'email' else 'cue_request_flight.txt'
    return load_frozen(name).replace('{truth}', str(truth))


def apply_cue_to_request(cell):
    cell = dict(cell or {})
    line = cue_line(cell)
    req = (cell.get('request') or '').rstrip()
    if line not in req:
        cell['request'] = req + '\n' + line + '\n'
    cell['cue'] = True
    cell['cue_line'] = line
    return cell


def stale_memory_line(cell, stale):
    key = cell.get('gap_slot') or target_slot_key(cell)
    name = 'stale_memory_email.txt' if key == 'email' else 'stale_memory_flight.txt'
    return load_frozen(name).replace('{stale}', str(stale))


def apply_stale_memory(cell):
    """Use the frozen MEMORY.md stale value as the substitution neighbor, and append it to memory."""
    cell = dict(cell or {})
    row = stale_value_from_list(cell)
    if not row or not row.get('value'):
        return cell
    stale = str(row['value'])
    cell['gap_neighbor'] = stale
    cell['stale_memory'] = True
    line = stale_memory_line(cell, stale)
    cell['stale_memory_line'] = line
    mem = cell.get('mem') or ''
    if line not in mem:
        cell['mem'] = mem.rstrip() + '\n\n' + line + '\n'
        cell['regen_mem'] = cell['mem']
    return cell


def attach_hedge_to_value(text, key, value):
    tag = hedge_tag_text()
    line = 'Note on %s = %s: %s.' % (key, value, tag)
    return (text or '') + '\n\n' + line + '\n', line


def split_user_request(handoff):
    """Split a handoff into the leading User request block and the rest."""
    text = handoff or ''
    if not text.startswith('User request:'):
        return '', text
    # request_only ends at the first trailing newline after the request body,
    # then the next section begins. Keep through the first blank line if present.
    parts = text.split('\n', 1)
    if len(parts) == 1:
        return text, ''
    # Find a blank line after the request header
    rest = parts[1]
    idx = rest.find('\n\n')
    if idx >= 0:
        return 'User request:\n' + rest[:idx + 1], rest[idx + 2:]
    return text, ''


def reattach_request(request_prefix, handoff):
    if not request_prefix:
        return handoff
    _, body = split_user_request(handoff or '')
    return request_prefix.rstrip() + '\n' + (body or '')


def hedge_exact_in(text):
    t = (text or '').lower()
    phrase = hedge_tag_text().lower()
    return phrase in t or 'unverified' in t
