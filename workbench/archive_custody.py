"""Whole-package custody: sealed packages dispatched to offline cabinets, reconciled by voucher."""
import re

from .archive_stock_plan import cell_key
from .codec import dataset, decode, encode, identifier, sha
from .errors import Problem

ZERO_TOTALS = {'held': 0, 'pending': 0, 'verified': 0, 'issued': 0, 'restored': 0}


def _text(value, maximum, code):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip() or not value.isprintable():
        raise Problem(code)
    return value


def _label(value, code):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value) is None:
        raise Problem(code)
    return value


def _grams(value):
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise Problem('custody_mass')
    return value


def _parse_cell(item):
    if not isinstance(item, dict):
        raise Problem('custody_cell')
    specimen, bounds = item['specimen'], item['bounds_um']
    if not isinstance(specimen, str) or not specimen.strip() or not isinstance(bounds, list) or len(bounds) != 2:
        raise Problem('custody_cell')
    if any(n is not None and (type(n) is not int or not 1 <= n <= 1_000_000) for n in bounds):
        raise Problem('custody_cell')
    return cell_key(specimen, bounds)


def _cell_fields(cell):
    specimen, bounds = decode(cell)
    return {'specimen': specimen, 'bounds_um': bounds}


def _opening(db, name):
    row = db.execute('SELECT opening FROM archive_stock WHERE dataset=?', (name,)).fetchone()
    if row is None:
        raise Problem('stock_not_open', 409)
    return decode(row['opening'])


def cell_totals(db, name):
    totals = {}
    for row in db.execute('''SELECT cell, state, SUM(mass_g) AS grams FROM archive_custody_packages
                             WHERE dataset=? GROUP BY cell, state''', (name,)):
        totals.setdefault(row['cell'], dict(ZERO_TOTALS))[row['state']] = row['grams']
    return totals


def blocked_g(totals):
    """Grams withheld from the main library: issued plus every package not yet restored."""
    return totals['issued'] + totals['held'] + totals['pending'] + totals['verified']


def _cover(packages, target):
    """One exact subset of whole packages summing to target grams, or None."""
    mask = (1 << (target + 1)) - 1
    prefixes = [1]
    reachable = 1
    for _, mass in packages:
        reachable = (reachable | (reachable << mass)) & mask
        prefixes.append(reachable)
    if not (reachable >> target) & 1:
        return None
    chosen, rest = [], target
    for index in range(len(packages) - 1, -1, -1):
        seal, mass = packages[index]
        if rest >= mass and (prefixes[index] >> (rest - mass)) & 1:
            chosen.append(seal)
            rest -= mass
    return chosen


def _dispatch_receipt(db, row):
    orders = [{'order': o['order_name'], 'kind': o['kind'], 'acceptable': o['acceptable'],
               'mass_g': o['mass_g'], **_cell_fields(o['cell'])}
              for o in db.execute('SELECT * FROM archive_custody_orders WHERE round=? ORDER BY order_name', (row['id'],))]
    packages = [{'seal': p['seal'], 'mass_g': p['mass_g'], **_cell_fields(p['cell'])}
                for p in db.execute('SELECT * FROM archive_custody_packages WHERE round=? ORDER BY seal', (row['id'],))]
    return {'round': row['id'], 'dataset': row['dataset'], 'cabinet': row['cabinet'],
            'ticket': row['ticket'], 'created': row['created'], 'packages': packages, 'orders': orders}


def dispatch(db, data):
    name = dataset(data['dataset'])
    ticket = dataset(data['ticket'])
    cabinet = _text(data['cabinet'], 64, 'custody_cabinet')
    raw_packages, raw_orders = data['packages'], data['orders']
    if not isinstance(raw_packages, list) or not 1 <= len(raw_packages) <= 100:
        raise Problem('custody_packages')
    if (not isinstance(raw_orders, dict) or not 1 <= len(raw_orders) <= 40
            or any(not isinstance(key, str) or not key.strip() for key in raw_orders)):
        raise Problem('custody_orders')
    packages, seen = [], set()
    for item in raw_packages:
        if not isinstance(item, dict):
            raise Problem('custody_package')
        seal = _label(item['seal'], 'custody_seal')
        if seal in seen:
            raise Problem('custody_seal_duplicate')
        seen.add(seal)
        packages.append({'seal': seal, 'cell': _parse_cell(item), 'mass_g': _grams(item['mass_g'])})
    packages.sort(key=lambda package: package['seal'])
    orders = {}
    for order_name, item in raw_orders.items():
        _text(order_name, 128, 'custody_order')
        if not isinstance(item, dict):
            raise Problem('custody_order')
        if item['kind'] not in ('guaranteed', 'standby'):
            raise Problem('custody_order_kind')
        orders[order_name] = {'cell': _parse_cell(item), 'mass_g': _grams(item['mass_g']), 'kind': item['kind']}
    request = encode({'cabinet': cabinet, 'packages': packages,
                      'orders': {key: orders[key] for key in sorted(orders)}})
    round_id = sha(encode(['archive-custody', name, ticket]))
    prior = db.execute('SELECT * FROM archive_custody_rounds WHERE dataset=? AND ticket=?', (name, ticket)).fetchone()
    if prior:
        if prior['request'] != request:
            raise Problem('custody_ticket_conflict', 409)
        return _dispatch_receipt(db, prior)
    opening = _opening(db, name)
    for package in packages:
        if db.execute('SELECT 1 FROM archive_custody_packages WHERE dataset=? AND seal=?',
                      (name, package['seal'])).fetchone():
            raise Problem('custody_seal_reused', 409)
    used = {row['cell']: row['net_g'] for row in db.execute(
        'SELECT cell, SUM(delta_g) AS net_g FROM archive_movements WHERE dataset=? GROUP BY cell', (name,))}
    totals = cell_totals(db, name)
    need = {}
    for package in packages:
        if package['cell'] not in opening:
            raise Problem('custody_cell_unknown', 409)
        need[package['cell']] = need.get(package['cell'], 0) + package['mass_g']
    for cell, grams in need.items():
        free = opening[cell] - used.get(cell, 0) - blocked_g(totals.get(cell, ZERO_TOTALS))
        if grams > free:
            raise Problem('custody_stock_short', 409)
    remaining = {}
    for package in packages:
        remaining.setdefault(package['cell'], []).append((package['seal'], package['mass_g']))
    acceptable = {}
    for kind in ('guaranteed', 'standby'):
        for order_name in sorted(orders):
            order = orders[order_name]
            if order['kind'] != kind:
                continue
            chosen = _cover(remaining.get(order['cell'], []), order['mass_g'])
            acceptable[order_name] = chosen is not None
            if chosen is None:
                if kind == 'guaranteed':
                    raise Problem('custody_guaranteed_uncoverable', 409)
            else:
                take = set(chosen)
                remaining[order['cell']] = [p for p in remaining[order['cell']] if p[0] not in take]
    db.execute('INSERT INTO archive_custody_rounds(id, dataset, cabinet, ticket, request) VALUES(?, ?, ?, ?, ?)',
               (round_id, name, cabinet, ticket, request))
    for package in packages:
        db.execute('INSERT INTO archive_custody_packages(dataset, round, seal, cell, mass_g) VALUES(?, ?, ?, ?, ?)',
                   (name, round_id, package['seal'], package['cell'], package['mass_g']))
    for order_name in sorted(orders):
        order = orders[order_name]
        db.execute('''INSERT INTO archive_custody_orders(round, order_name, cell, mass_g, kind, acceptable)
                      VALUES(?, ?, ?, ?, ?, ?)''',
                   (round_id, order_name, order['cell'], order['mass_g'], order['kind'],
                    1 if acceptable[order_name] else 0))
    _event(db, name, round_id, ticket, 'dispatch', None, None, sum(p['mass_g'] for p in packages), request)
    return _dispatch_receipt(db, db.execute('SELECT * FROM archive_custody_rounds WHERE id=?', (round_id,)).fetchone())


def _event(db, name, round_id, ticket, kind, seal, order_name, mass_g, request):
    cursor = db.execute('''INSERT INTO archive_custody_events(dataset, round, ticket, kind, seal, order_name, mass_g, request)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?)''',
                        (name, round_id, ticket, kind, seal, order_name, mass_g, request))
    return cursor.lastrowid


def _receipt(row):
    return {'seq': row['seq'], 'dataset': row['dataset'], 'round': row['round'], 'ticket': row['ticket'],
            'kind': row['kind'], 'seal': row['seal'], 'order': row['order_name'], 'mass_g': row['mass_g'],
            'recorded': row['recorded']}


def _event_row(db, name, ticket):
    return db.execute('SELECT * FROM archive_custody_events WHERE dataset=? AND ticket=?', (name, ticket)).fetchone()


def _replay(db, name, ticket, kind, request):
    row = _event_row(db, name, ticket)
    if row:
        if row['kind'] != kind or row['request'] != request:
            raise Problem('custody_ticket_conflict', 409)
        return _receipt(row)
    return None


def _round(db, round_id, name):
    row = db.execute('SELECT * FROM archive_custody_rounds WHERE id=?', (round_id,)).fetchone()
    if row is None or row['dataset'] != name:
        raise Problem('custody_round_not_found', 404)
    return row


def _package(db, name, seal):
    row = db.execute('SELECT * FROM archive_custody_packages WHERE dataset=? AND seal=?', (name, seal)).fetchone()
    if row is None:
        raise Problem('custody_package_not_found', 404)
    return row


def _settle(db, policy, round_id, seq):
    """Restore verified packages per policy, then close a round whose packages are all final."""
    rows = db.execute('SELECT state FROM archive_custody_packages WHERE round=?', (round_id,)).fetchall()
    reconciled = all(row['state'] in ('issued', 'verified', 'restored') for row in rows)
    if policy == 'package' or (policy == 'round' and reconciled):
        db.execute('''UPDATE archive_custody_packages SET state='restored', restored_seq=?
                      WHERE round=? AND state='verified' ''', (seq, round_id))
    pending = db.execute('''SELECT 1 FROM archive_custody_packages
                            WHERE round=? AND state NOT IN ('issued', 'restored') LIMIT 1''', (round_id,)).fetchone()
    if pending is None:
        db.execute("UPDATE archive_custody_rounds SET state='closed' WHERE id=?", (round_id,))


def issue(db, policy, data):
    name = dataset(data['dataset'])
    ticket = dataset(data['ticket'])
    round_id = identifier(data['round'])
    seal = _label(data['seal'], 'custody_seal')
    order_name = _text(data['order'], 128, 'custody_order')
    request = encode({'round': round_id, 'seal': seal, 'order': order_name})
    replay = _replay(db, name, ticket, 'issue', request)
    if replay is not None:
        return replay
    _round(db, round_id, name)
    package = _package(db, name, seal)
    if package['round'] != round_id:
        raise Problem('custody_package_foreign', 409)
    if package['state'] != 'held':
        raise Problem('custody_package_spent', 409)
    order = db.execute('SELECT * FROM archive_custody_orders WHERE round=? AND order_name=?',
                       (round_id, order_name)).fetchone()
    if order is None:
        raise Problem('custody_order_unknown', 409)
    if order['cell'] != package['cell']:
        raise Problem('custody_cell_mismatch', 409)
    if not order['acceptable']:
        raise Problem('custody_order_not_accepted', 409)
    issued = db.execute('''SELECT COALESCE(SUM(mass_g), 0) FROM archive_custody_packages
                           WHERE round=? AND order_name=?''', (round_id, order_name)).fetchone()[0]
    if issued + package['mass_g'] > order['mass_g']:
        raise Problem('custody_order_quota', 409)
    seq = _event(db, name, round_id, ticket, 'issue', seal, order_name, package['mass_g'], request)
    db.execute("UPDATE archive_custody_packages SET state='issued', order_name=? WHERE dataset=? AND seal=?",
               (order_name, name, seal))
    _settle(db, policy, round_id, seq)
    return _receipt(_event_row(db, name, ticket))


def return_package(db, policy, data):
    name = dataset(data['dataset'])
    ticket = dataset(data['ticket'])
    round_id = identifier(data['round'])
    seal = _label(data['seal'], 'custody_seal')
    request = encode({'round': round_id, 'seal': seal})
    replay = _replay(db, name, ticket, 'return', request)
    if replay is not None:
        return replay
    _round(db, round_id, name)
    package = _package(db, name, seal)
    if package['round'] != round_id:
        raise Problem('custody_package_foreign', 409)
    if package['voucher_seq'] is not None:
        raise Problem('custody_return_duplicate', 409)
    if package['state'] in ('issued', 'restored'):
        raise Problem('custody_package_spent', 409)
    seq = _event(db, name, round_id, ticket, 'return', seal, None, package['mass_g'], request)
    state = 'verified' if package['scan_seq'] is not None else 'pending'
    db.execute('UPDATE archive_custody_packages SET state=?, voucher_seq=? WHERE dataset=? AND seal=?',
               (state, seq, name, seal))
    _settle(db, policy, round_id, seq)
    return _receipt(_event_row(db, name, ticket))


def verify(db, policy, data):
    name = dataset(data['dataset'])
    ticket = dataset(data['ticket'])
    seal = _label(data['seal'], 'custody_seal')
    request = encode({'seal': seal})
    replay = _replay(db, name, ticket, 'verify', request)
    if replay is not None:
        return replay
    package = _package(db, name, seal)
    if package['scan_seq'] is not None:
        raise Problem('custody_scan_duplicate', 409)
    if package['state'] in ('issued', 'restored'):
        raise Problem('custody_package_spent', 409)
    seq = _event(db, name, package['round'], ticket, 'verify', seal, None, package['mass_g'], request)
    state = 'verified' if package['voucher_seq'] is not None else 'pending'
    db.execute('UPDATE archive_custody_packages SET state=?, scan_seq=? WHERE dataset=? AND seal=?',
               (state, seq, name, seal))
    _settle(db, policy, package['round'], seq)
    return _receipt(_event_row(db, name, ticket))


def reconcile(db, policy, name):
    name = dataset(name)
    row = db.execute('SELECT * FROM archive_stock WHERE dataset=?', (name,)).fetchone()
    if row is None:
        raise Problem('stock_not_open', 409)
    stock = {key: row[key] for key in ('dataset', 'generation', 'run', 'opened')}
    rounds = []
    for round_row in db.execute('SELECT * FROM archive_custody_rounds WHERE dataset=? ORDER BY seq', (name,)):
        round_id = round_row['id']
        packages = [{'seal': p['seal'], 'mass_g': p['mass_g'], **_cell_fields(p['cell']),
                     'state': p['state'], 'order': p['order_name'],
                     'voucher_received': p['voucher_seq'] is not None,
                     'seal_verified': p['scan_seq'] is not None}
                    for p in db.execute('SELECT * FROM archive_custody_packages WHERE round=? ORDER BY seal',
                                        (round_id,))]
        issued = {r['order_name']: r['grams'] for r in db.execute(
            '''SELECT order_name, SUM(mass_g) AS grams FROM archive_custody_packages
               WHERE round=? AND state='issued' GROUP BY order_name''', (round_id,))}
        orders = [{'order': o['order_name'], 'kind': o['kind'], 'acceptable': o['acceptable'],
                   'mass_g': o['mass_g'], **_cell_fields(o['cell']),
                   'issued_g': issued.get(o['order_name'], 0),
                   'remaining_g': o['mass_g'] - issued.get(o['order_name'], 0)}
                  for o in db.execute('SELECT * FROM archive_custody_orders WHERE round=? ORDER BY order_name',
                                      (round_id,))]
        events = [_receipt(e) for e in db.execute(
            'SELECT * FROM archive_custody_events WHERE round=? ORDER BY seq', (round_id,))]
        rounds.append({'round': round_id, 'cabinet': round_row['cabinet'], 'state': round_row['state'],
                       'created': round_row['created'], 'packages': packages, 'orders': orders, 'events': events})
    cells = []
    for cell, totals in sorted(cell_totals(db, name).items()):
        cells.append({**_cell_fields(cell), 'held_g': totals['held'], 'pending_g': totals['pending'],
                      'verified_g': totals['verified'], 'issued_g': totals['issued'],
                      'restored_g': totals['restored']})
    return {'dataset': name, 'policy': policy, 'stock': stock, 'cells': cells, 'rounds': rounds}
