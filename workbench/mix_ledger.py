"""Commitment ledger for certified mix buckets.

Admission (a bucket exists with evidence-backed composition) is separate from
commitment (an order is promised material) and from weighing (the promise is
redeemed against one concrete bucket).  Every new promise must leave a set of
non-conflicting fulfilments for *all* open commitments; a new ticket can never
be accepted by withdrawing an earlier one.
"""
from fractions import Fraction

from aggregate import mix_ledger as domain

from .codec import dataset, decode, encode
from .errors import Problem


def _domain_envelope(stored):
    """Convert JSON-safe [sieve, [low_bp, high_bp]] rows to Fraction pairs."""
    return [(sieve, (Fraction(low, 10_000), Fraction(high, 10_000)), (low, high))
            for sieve, (low, high) in stored]


def _name(value):
    return dataset(value)


def _domain_error(exc):
    code = str(exc)
    raise Problem(code if code.isascii() else 'mix_domain_input', 422) from exc


def _ticket(db, name, ticket):
    return db.execute('SELECT * FROM mix_movements WHERE dataset=? AND ticket=?', (name, ticket)).fetchone()


def _replay(row, kind, request):
    if row is None:
        return None
    if row['kind'] != kind or row['request'] != request:
        raise Problem('mix_ticket_conflict', 409)
    return _movement(row)


def _movement(row):
    item = dict(row)
    item['request'] = decode(item['request'])
    return item


def _buckets(db, name):
    return {row['bucket']: decode(row['record'])
            for row in db.execute('SELECT * FROM mix_buckets WHERE dataset=?', (name,))}


def _orders(db, name):
    """Orders ready for domain checks: envelopes are Fraction pairs."""
    rows = db.execute('SELECT * FROM mix_orders WHERE dataset=?', (name,)).fetchall()
    orders = []
    for row in rows:
        item = dict(row)
        item['allowed'] = decode(item['allowed']) if item['allowed'] else None
        item['rework_only'] = bool(item['rework_only'])
        item['envelope'] = _domain_envelope(decode(row['envelope']))
        orders.append(item)
    return orders


def admit(db, data, kind):
    name = _name(data['dataset'])
    ticket = _name(data['ticket'])
    try:
        record = domain.rework_bucket(data) if kind == 'rework' else domain.fresh_bucket(data)
    except ValueError as exc:
        _domain_error(exc)
    request = encode({'kind': kind, 'record': record})
    replay = _replay(_ticket(db, name, ticket), 'admit', request)
    if replay is not None:
        return replay
    if db.execute('SELECT 1 FROM mix_buckets WHERE dataset=? AND bucket=?',
                  (name, record['bucket'])).fetchone():
        raise Problem('mix_bucket_exists', 409)
    db.execute('INSERT INTO mix_buckets(dataset, bucket, kind, record) VALUES(?, ?, ?, ?)',
               (name, record['bucket'], kind, encode(record)))
    db.execute('''INSERT INTO mix_movements(dataset, ticket, kind, bucket, "order", mass_g, request)
                  VALUES(?, ?, 'admit', ?, ?, ?, ?)''',
               (name, ticket, record['bucket'], record['bucket'], record['net_g'], request))
    return _movement(_ticket(db, name, ticket))


def _eligible(bucket, order):
    if bucket['kind'] != 'rework' and order['rework_only']:
        return False
    if order['allowed'] is not None and bucket['bucket'] not in order['allowed']:
        return False
    return domain.certified(bucket, order['envelope'])


def _assay_map(buckets, order, limits_bp):
    result = {}
    for code, bucket in buckets.items():
        if order['allowed'] is not None and code not in order['allowed']:
            continue
        if order['rework_only'] and bucket['kind'] != 'rework':
            continue
        result[code] = domain.assay_bucket(bucket, order['envelope'], limits_bp)
    return result


def _feasibility(buckets, orders):
    """Capacity-aware bipartite match: each open order draws from one bucket.

    Locked (and already weighed) orders reserve mass first; pool orders are
    placed by backtracking so every partial assignment always respects the
    bucket's net mass.
    """
    reserves = {code: 0 for code in buckets}
    pool = []
    for order in orders:
        if order['state'] == 'rejected':
            continue
        if order['mode'] == 'locked' or order['state'] == 'weighed':
            code = order['locked_bucket']
            if code not in buckets or not _eligible(buckets[code], order):
                return False, reserves
            reserves[code] += order['dry_g']
        else:
            pool.append(order)
    if any(reserves[code] > buckets[code]['net_g'] for code in reserves):
        return False, reserves

    needs = {order['order']: order['dry_g'] for order in pool}
    candidates = {}
    for order in pool:
        fits = [code for code, bucket in buckets.items() if _eligible(bucket, order)]
        if not fits:
            return False, reserves
        candidates[order['order']] = fits

    load = dict(reserves)
    ordered = sorted(pool, key=lambda item: (len(candidates[item['order']]), item['order']))

    def place(index):
        if index == len(ordered):
            return True
        order_id = ordered[index]['order']
        for code in candidates[order_id]:
            if load[code] + needs[order_id] <= buckets[code]['net_g']:
                load[code] += needs[order_id]
                if place(index + 1):
                    return True
                load[code] -= needs[order_id]
        return False

    if not place(0):
        return False, reserves
    return True, load


def _recipe_possible(buckets, order):
    return any(_eligible(bucket, order) for bucket in buckets.values())


def commit(db, data, *, lock):
    name = _name(data['dataset'])
    ticket = _name(data['ticket'])
    try:
        order = domain.parse_order(data)
    except ValueError as exc:
        _domain_error(exc)
    locked = data.get('bucket') if lock else None
    if lock and (not isinstance(locked, str) or not locked.strip()):
        raise Problem('mix_locked_bucket_required')
    if not lock and data.get('bucket') is not None:
        raise Problem('mix_pool_has_no_locked_bucket')
    mode = 'locked' if lock else 'pool'
    request_order = {**order, 'envelope': domain.stored_envelope(order['envelope'])}
    request = encode({'mode': mode, 'order': request_order, 'locked_bucket': locked})
    replay = _replay(_ticket(db, name, ticket), 'commit', request)
    if replay is not None:
        return replay
    buckets = _buckets(db, name)
    if lock and locked not in buckets:
        raise Problem('mix_bucket_unknown', 404)
    if db.execute('SELECT 1 FROM mix_orders WHERE dataset=? AND "order"=?', (name, order['order'])).fetchone():
        raise Problem('mix_order_exists', 409)
    if lock and not _eligible(buckets[locked], order):
        raise Problem('mix_bucket_not_certified', 422)
    if not _recipe_possible(buckets, order):
        raise Problem('mix_recipe_infeasible', 422)
    candidate = {**order, 'state': 'committed', 'mode': mode, 'locked_bucket': locked}
    ok, _ = _feasibility(buckets, [*_orders(db, name), candidate])
    if not ok:
        raise Problem('mix_material_committed', 409)
    db.execute('''INSERT INTO mix_orders(dataset, "order", dry_g, envelope, allowed, rework_only,
                                         state, mode, locked_bucket)
                  VALUES(?, ?, ?, ?, ?, ?, 'committed', ?, ?)''',
               (name, order['order'], order['dry_g'], encode(domain.stored_envelope(order['envelope'])),
                encode(order['allowed']) if order['allowed'] is not None else None,
                int(order['rework_only']), mode, locked))
    db.execute('''INSERT INTO mix_movements(dataset, ticket, kind, bucket, "order", mass_g, request)
                  VALUES(?, ?, 'commit', ?, ?, ?, ?)''',
               (name, ticket, locked, order['order'], order['dry_g'], request))
    return _movement(_ticket(db, name, ticket))


def _stored_order(row):
    return {'order': row['order'], 'dry_g': row['dry_g'],
            'envelope': _domain_envelope(decode(row['envelope'])),
            'allowed': decode(row['allowed']) if row['allowed'] else None,
            'rework_only': bool(row['rework_only'])}


def weigh(db, data):
    name = _name(data['dataset'])
    ticket = _name(data['ticket'])
    order_id, code = _name(data['order']), _name(data['bucket'])
    request = encode({'order': order_id, 'bucket': code})
    replay = _replay(_ticket(db, name, ticket), 'weigh', request)
    if replay is not None:
        return replay
    row = db.execute('SELECT * FROM mix_orders WHERE dataset=? AND "order"=?', (name, order_id)).fetchone()
    if row is None:
        raise Problem('mix_order_unknown', 404)
    if row['state'] == 'weighed':
        raise Problem('mix_order_already_weighed', 409)
    if row['state'] == 'rejected':
        raise Problem('mix_order_rejected', 409)
    buckets = _buckets(db, name)
    if code not in buckets:
        raise Problem('mix_bucket_unknown', 404)
    order = _stored_order(row)
    if row['mode'] == 'locked' and row['locked_bucket'] != code:
        raise Problem('mix_locked_bucket_mismatch', 409)
    if not _eligible(buckets[code], order):
        raise Problem('mix_bucket_not_certified', 422)
    candidate = {**order, 'state': 'weighed', 'mode': 'locked', 'locked_bucket': code}
    others = [item for item in _orders(db, name) if item['order'] != order_id]
    ok, _ = _feasibility(buckets, [*others, candidate])
    if not ok:
        raise Problem('mix_weigh_starves_commitment', 409)
    db.execute('''UPDATE mix_orders SET state='weighed', mode='locked', locked_bucket=?
                  WHERE dataset=? AND "order"=?''', (code, name, order_id))
    db.execute('''INSERT INTO mix_movements(dataset, ticket, kind, bucket, "order", mass_g, request)
                  VALUES(?, ?, 'weigh', ?, ?, ?, ?)''',
               (name, ticket, code, order_id, row['dry_g'], request))
    return _movement(_ticket(db, name, ticket))


def reject(db, data):
    name = _name(data['dataset'])
    ticket = _name(data['ticket'])
    order_id = _name(data['order'])
    try:
        order = domain.parse_order(data)
    except ValueError as exc:
        _domain_error(exc)
    if order_id != order['order']:
        raise Problem('mix_order_name')
    request = encode({'reject': {**order, 'envelope': domain.stored_envelope(order['envelope'])}})
    replay = _replay(_ticket(db, name, ticket), 'reject', request)
    if replay is not None:
        return replay
    if db.execute('SELECT 1 FROM mix_orders WHERE dataset=? AND "order"=?', (name, order_id)).fetchone():
        raise Problem('mix_order_exists', 409)
    buckets = _buckets(db, name)
    assays = _assay_map(buckets, order, data['limits_bp'])
    if not _recipe_possible(buckets, order):
        reason = 'recipe_infeasible'
    else:
        ok, _ = _feasibility(buckets, [*_orders(db, name),
                                       {**order, 'state': 'committed', 'mode': 'pool',
                                        'locked_bucket': None}])
        reason = 'material_committed' if not ok else 'would_be_feasible'
    db.execute('''INSERT INTO mix_orders(dataset, "order", dry_g, envelope, allowed, rework_only,
                                         state, mode, locked_bucket, reason)
                  VALUES(?, ?, ?, ?, ?, ?, 'rejected', NULL, NULL, ?)''',
               (name, order_id, order['dry_g'], encode(domain.stored_envelope(order['envelope'])),
                encode(order['allowed']) if order['allowed'] is not None else None,
                int(order['rework_only']), reason))
    db.execute('''INSERT INTO mix_movements(dataset, ticket, kind, bucket, "order", mass_g, request)
                  VALUES(?, ?, 'reject', NULL, ?, ?, ?)''',
               (name, ticket, order_id, order['dry_g'], request))
    return _movement(_ticket(db, name, ticket))

def explain(db, data):
    """Classify a prospective order without consuming any material."""
    name = _name(data['dataset'])
    try:
        order = domain.parse_order(data)
    except ValueError as exc:
        _domain_error(exc)
    buckets = _buckets(db, name)
    assays = _assay_map(buckets, order, data['limits_bp'])
    if not _recipe_possible(buckets, order):
        decision = 'reject_recipe_infeasible'
    else:
        ok, _ = _feasibility(buckets, [*_orders(db, name),
                                       {**order, 'state': 'committed', 'mode': 'pool',
                                        'locked_bucket': None}])
        decision = 'accept_pool' if ok else 'reject_material_committed'
    return {'order': order['order'], 'decision': decision, 'bucket_assays': assays}


def status(db, name):
    name = _name(name)
    buckets = _buckets(db, name)
    orders = _orders(db, name)
    ok, load = _feasibility(buckets, orders)
    rows = []
    for code, bucket in sorted(buckets.items()):
        rows.append({**bucket, 'reserved_g': load.get(code, 0),
                     'remaining_g': bucket['net_g'] - load.get(code, 0)})
    return {'dataset': name, 'commitments_feasible': ok, 'buckets': rows,
            'orders': [{'order': item['order'], 'dry_g': item['dry_g'], 'state': item['state'],
                        'mode': item['mode'], 'locked_bucket': item['locked_bucket'],
                        'reason': item['reason'], 'rework_only': item['rework_only'],
                        'allowed': item['allowed'],
                        'limits_bp': [[sieve, list(bp)] for sieve, _, bp in item['envelope']]}
                       for item in sorted(orders, key=lambda item: item['order'])]}


def history(db, name, after):
    name = _name(name)
    rows = db.execute('SELECT * FROM mix_movements WHERE dataset=? AND seq>? ORDER BY seq LIMIT 101',
                      (name, after)).fetchall()
    return {'dataset': name, 'events': [_movement(row) for row in rows[:100]],
            'next_after': rows[99]['seq'] if len(rows) > 100 else None}
