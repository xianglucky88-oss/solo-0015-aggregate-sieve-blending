"""One physical archive per dataset; admission and actual movements are independent."""
from . import catalog
from .archive_stock_plan import handoff
from .codec import dataset, decode, encode, generation, identifier
from .errors import Problem


def _current(db, name, revision, activation):
    if catalog.head(db, name) != {'generation': generation(activation), 'revision': revision}:
        raise Problem('stock_activation_changed', 409)


def _opening(db, name):
    row = db.execute('SELECT * FROM archive_stock WHERE dataset=?', (dataset(name),)).fetchone()
    if row is None:
        raise Problem('stock_not_open', 409)
    return _opened(row), decode(row['opening'])


def _opened(row):
    return {key: row[key] for key in ('dataset', 'generation', 'run', 'opened')}


def open_stock(db, run_id, activation):
    generation(activation)
    name, revision, state, measured, _, _ = handoff(db, run_id)
    prior = db.execute('SELECT * FROM archive_stock WHERE dataset=?', (name,)).fetchone()
    if prior:
        if (prior['run'], prior['generation']) == (run_id, activation):
            return _opened(prior)
        raise Problem('stock_already_open', 409)
    _current(db, name, revision, activation)
    if state != 'ready':
        raise Problem('stock_handoff_withheld', 409)
    db.execute('INSERT INTO archive_stock(dataset, generation, run, opening) VALUES(?, ?, ?, ?)',
               (name, activation, run_id, encode(measured)))
    return _opening(db, name)[0]


def _grams(value, minimum=1):
    if type(value) is not int or not minimum <= value <= 10_000_000:
        raise Problem('stock_mass')
    return value


def _text(value, maximum, code):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not value.strip() or not value.isprintable():
        raise Problem(code)
    return value


def _ticket(db, name, ticket):
    return db.execute('SELECT * FROM archive_movements WHERE dataset=? AND ticket=?', (name, ticket)).fetchone()


def _receipt(row):
    item = dict(row)
    specimen, bounds = decode(item.pop('cell'))
    return {**item, 'request': decode(item['request']), 'specimen': specimen, 'bounds_um': bounds}


def _retry(db, name, ticket, kind, request):
    row = _ticket(db, name, ticket)
    if row:
        if row['kind'] != kind or row['request'] != request:
            raise Problem('stock_ticket_conflict', 409)
        return _receipt(row)
    return None


def _append(db, name, ticket, kind, parent, cell, order, delta, request):
    db.execute('''INSERT INTO archive_movements(dataset,ticket,kind,issue_ticket,cell,order_name,delta_g,request)
                  VALUES(?,?,?,?,?,?,?,?)''', (name, ticket, kind, parent, cell, order, delta, request))
    return _receipt(_ticket(db, name, ticket))


def _used(db, name, cell, order=None):
    sql = 'SELECT COALESCE(SUM(delta_g), 0) FROM archive_movements WHERE dataset=? AND cell=?'
    if order is None:
        return db.execute(sql, (name, cell)).fetchone()[0]
    return db.execute(sql + ' AND order_name=?', (name, cell, order)).fetchone()[0]


def issue(db, data):
    run_id, activation = identifier(data['run']), generation(data['generation'])
    ticket = dataset(data['ticket'])
    order = _text(data['order'], 128, 'stock_order')
    grams = _grams(data['mass_g'])
    name, revision, state, measured, holds, orders = handoff(db, run_id)
    request = encode({'run': run_id, 'generation': activation, 'order': order, 'mass_g': grams})
    replay = _retry(db, name, ticket, 'issue', request)
    if replay is not None:
        return replay
    _, opening = _opening(db, name)
    _current(db, name, revision, activation)
    if state != 'ready' or order not in orders:
        raise Problem('stock_order_not_released', 409)
    cell, quota = orders[order]
    if cell not in opening or opening[cell] != measured.get(cell):
        raise Problem('stock_cell_changed', 409)
    if db.execute('''SELECT 1 FROM archive_movements WHERE dataset=? AND order_name=? AND cell<>? LIMIT 1''',
                  (name, order, cell)).fetchone():
        raise Problem('stock_order_retargeted', 409)
    if _used(db, name, cell, order) + grams > quota:
        raise Problem('stock_order_quota', 409)
    if _used(db, name, cell) + holds[cell] + grams > opening[cell]:
        raise Problem('stock_hold_or_balance', 409)
    return _append(db, name, ticket, 'issue', None, cell, order, grams, request)


def adjust(db, data, kind):
    name, ticket, parent = (dataset(data[key]) for key in ('dataset', 'ticket', 'issue_ticket'))
    reason = _text(data['reason'], 240, 'stock_reason')
    correction = kind == 'correction'
    fields = {'issue_ticket': parent, 'reason': reason}
    if correction:
        basis = dataset(data['basis'])
        quantity = _grams(data['issued_g'], 0)
        fields.update(basis=basis, issued_g=quantity)
    else:
        quantity = _grams(data['mass_g'])
        fields['mass_g'] = quantity
    request = encode(fields)
    replay = _retry(db, name, ticket, kind, request)
    if replay is not None:
        return replay
    original = _ticket(db, name, parent)
    if original is None or original['kind'] != 'issue':
        raise Problem('stock_issue_not_found', 404)
    issued, returned, current_basis = original['delta_g'], 0, parent
    for row in db.execute('''SELECT ticket, kind, delta_g FROM archive_movements
                             WHERE dataset=? AND issue_ticket=? ORDER BY seq''', (name, parent)):
        if row['kind'] == 'correction':
            issued += row['delta_g']
            current_basis = row['ticket']
        else:
            returned -= row['delta_g']
    cell = original['cell']
    if correction:
        if basis != current_basis:
            raise Problem('stock_correction_stale', 409)
        delta = quantity - issued
        if delta == 0 or quantity < returned:
            raise Problem('stock_correction_amount', 409)
        _, opening = _opening(db, name)
        if _used(db, name, cell) + delta > opening[cell]:
            raise Problem('stock_physical_balance', 409)
    else:
        if quantity > issued - returned:
            raise Problem('stock_return_amount', 409)
        delta = -quantity
    return _append(db, name, ticket, kind, parent, cell, original['order_name'], delta, request)


def reconcile(db, run_id):
    name, revision, state, measured, holds, orders = handoff(db, run_id)
    stock, opening = _opening(db, name)
    usage, actual = {}, {}
    for row in db.execute('''SELECT cell, order_name, SUM(delta_g) AS net_g,
                             SUM(CASE WHEN kind='return' THEN -delta_g ELSE 0 END) AS returned_g
                             FROM archive_movements WHERE dataset=? GROUP BY cell, order_name''', (name,)):
        cell = row['cell']
        actual[(row['order_name'], cell)] = (row['net_g'], row['returned_g'])
        usage[cell] = usage.get(cell, 0) + row['net_g']
    planned = {(order, cell): grams for order, (cell, grams) in orders.items()}
    rows = []
    for order, cell in sorted(planned.keys() | actual.keys()):
        net, returned = actual.get((order, cell), (0, 0))
        quota = planned.get((order, cell), 0)
        status = 'planned' if quota else 'retargeted' if order in orders else 'removed'
        specimen, bounds = decode(cell)
        rows.append({'order': order, 'specimen': specimen, 'bounds_um': bounds, 'plan_status': status,
                     'planned_g': quota, 'issued_g': net + returned, 'returned_g': returned,
                     'net_used_g': net, 'difference_g': net - quota})
    cells = []
    for key in sorted(opening.keys() | holds.keys()):
        on_hand = opening[key] - usage.get(key, 0) if key in opening else None
        matches = key in opening and measured.get(key) == opening[key]
        specimen, bounds = decode(key)
        cells.append({'specimen': specimen, 'bounds_um': bounds, 'opening_g': opening.get(key),
                      'plan_measured_g': measured.get(key), 'measurement_matches': matches,
                      'on_hand_g': on_hand, 'plan_hold_g': holds.get(key, 0),
                      'unheld_g': on_hand - holds.get(key, 0) if matches else None})
    latest = db.execute('SELECT COALESCE(MAX(seq), 0) FROM archive_movements WHERE dataset=?', (name,)).fetchone()[0]
    return {'stock': stock, 'run': run_id, 'revision': revision, 'plan_state': state,
            'head': catalog.head(db, name), 'through_seq': latest, 'cells': cells, 'orders': rows}


def history(db, name, after):
    stock, _ = _opening(db, name)
    generation(after)
    rows = db.execute('''SELECT * FROM archive_movements WHERE dataset=? AND seq>?
                         ORDER BY seq LIMIT 101''', (name, after)).fetchall()
    return {'stock': stock, 'events': [_receipt(row) for row in rows[:100]],
            'next_after': rows[99]['seq'] if len(rows) > 100 else None}
