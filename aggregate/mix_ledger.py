"""Certified physical buckets and feed commitments for the granular-mix bench.

Two questions are deliberately kept apart:

* ``assay_bucket`` asks whether *this bucket* can satisfy a sieve envelope on
  the evidence attached to the bucket.  A bucket is only certified when every
  documented constituent meets the envelope across its whole certified range.
* The ledger module (``workbench.mix_ledger``) asks whether *this order* has
  material it can actually draw, without stealing material already promised
  to another open order.

Leftover mixture is never returned to its original sieve shelf as fresh
material.  After weighing and a confirmed re-mix it is booked as a new
physical bucket whose certified composition is inferred from the original
mixture, the sieve evidence covering the consumed portion, and the returned
net mass.
"""
from fractions import Fraction


def _grams(value, code):
    if type(value) is not int or not 1 <= value <= 1_000_000_000:
        raise ValueError(code)
    return value


def _nonnegative(value, code):
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError(code)
    return value


def _interval_bp(row, code):
    if (not isinstance(row, list) or len(row) != 2
            or any(type(n) is not int for n in row)
            or not 0 <= row[0] <= row[1] <= 10_000):
        raise ValueError(code)
    return Fraction(row[0], 10_000), Fraction(row[1], 10_000)


def envelope(doc):
    """Parse an order envelope: strictly decreasing sieves with bp ranges."""
    grid = doc['sieves_um']
    rows = doc['limits_bp']
    if (not isinstance(grid, list) or not isinstance(rows, list)
            or not 1 <= len(grid) <= 30 or len(grid) != len(rows)
            or any(type(n) is not int or not 0 < n <= 1_000_000 for n in grid)
            or grid != sorted(set(grid), reverse=True)):
        raise ValueError('mix_envelope_shape')
    parsed = []
    for sieve, row in zip(grid, rows):
        low, high = _interval_bp(row, 'mix_envelope_range')
        parsed.append((sieve, (low, high), tuple(row)))
    return parsed


def _evidence(rows, code):
    """Passing-mass evidence is a closed interval at each documented sieve."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise ValueError(code)
    parsed = []
    for row in rows:
        if (not isinstance(row, dict) or type(row.get('sieve_um')) is not int
                or type(row.get('min_g')) is not int or type(row.get('max_g')) is not int):
            raise ValueError(code)
        low, high = _nonnegative(row['min_g'], code), _nonnegative(row['max_g'], code)
        if low > high:
            raise ValueError(code)
        parsed.append((row['sieve_um'], low, high))
    sieves = [row[0] for row in parsed]
    if sieves != sorted(set(sieves), reverse=True):
        raise ValueError(code)
    return parsed


def _code(bucket):
    if not isinstance(bucket, dict) or not isinstance(bucket.get('code'), str) or not bucket['code'].strip():
        raise ValueError('mix_bucket_code')
    return bucket['code']


def fresh_bucket(doc):
    """A fresh bucket is backed by direct measured sieve evidence."""
    code = _code(doc.get('bucket'))
    grams = _grams(doc['bucket'].get('net_g'), 'mix_bucket_mass')
    evidence = _evidence(doc.get('passing_evidence_g'), 'mix_evidence')
    capped = [(sieve, low, min(high, grams)) for sieve, low, high in evidence]
    if any(low > high for _, low, high in capped):
        raise ValueError('mix_evidence_inconsistent')
    return {'bucket': code, 'kind': 'fresh', 'net_g': grams,
            'mix_batch': doc['bucket'].get('mix_batch'), 'evidence': capped}


def rework_bucket(doc):
    """Book confirmed re-mixed leftovers as one new-lot physical bucket.

    The certified passing mass of the returned bucket is bounded at each
    sieve by the original passing mass minus what the consumed portion is
    certified to have carried through that sieve.  Nothing here treats the
    leftovers as fresh material on the original sieve shelf.
    """
    code = _code(doc.get('bucket'))
    batch = doc.get('bucket', {}).get('mix_batch')
    if not isinstance(batch, str) or not batch.strip():
        raise ValueError('mix_batch_required')
    original_g = _grams(doc.get('original_mix_g'), 'mix_original_mass')
    used_g = _grams(doc.get('used_g'), 'mix_used_mass')
    returned_g = _grams(doc.get('returned_g'), 'mix_returned_mass')
    if used_g + returned_g != original_g:
        raise ValueError('mix_mass_balance')
    rows = doc.get('origin_evidence')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise ValueError('mix_origin_evidence')
    evidence = []
    for row in rows:
        sieve = row.get('sieve_um')
        if type(sieve) is not int:
            raise ValueError('mix_origin_evidence')
        origin_passed = _nonnegative(row.get('original_passing_g'), 'mix_origin_evidence')
        used = row.get('used_passing_g')
        if (not isinstance(used, list) or len(used) != 2
                or any(type(n) is not int for n in used)):
            raise ValueError('mix_origin_evidence')
        used_low, used_high = _nonnegative(used[0], 'mix_origin_evidence'), _nonnegative(used[1], 'mix_origin_evidence')
        if origin_passed > original_g or used_high > used_g or used_low > used_high:
            raise ValueError('mix_evidence_inconsistent')
        low = max(0, origin_passed - used_high)
        high = min(returned_g, origin_passed - used_low)
        if low > high:
            raise ValueError('mix_evidence_inconsistent')
        evidence.append((sieve, low, high))
    sieves = [row[0] for row in evidence]
    if sieves != sorted(set(sieves), reverse=True):
        raise ValueError('mix_origin_evidence')
    return {'bucket': code, 'kind': 'rework', 'net_g': returned_g, 'mix_batch': batch,
            'origin_mix_g': original_g, 'used_g': used_g, 'evidence': evidence}


def assay_bucket(bucket, limits, limits_bp=None):
    """Classify a bucket against an envelope.

    ``certified`` every documented sieve is inside the envelope over its
    whole certified interval;
    ``certain_violation`` at least one sieve cannot stay inside even at the
    most favourable end of its interval;
    ``possible_violation`` every interval touches the envelope but at least
    one crosses a boundary;
    ``incomplete_evidence`` the envelope names a sieve without evidence.
    """
    grams = bucket['net_g']
    curve = {sieve: (Fraction(low, grams), Fraction(high, grams))
             for sieve, low, high in bucket['evidence']}
    rows, missing = [], False
    certain = possible = False
    for index, (sieve, (lower, upper), bp) in enumerate(limits):
        if limits_bp is not None:
            bp = limits_bp[index]
        if sieve not in curve:
            missing = True
            rows.append({'sieve_um': sieve, 'passing': None, 'limits_bp': list(bp)})
            continue
        low, high = curve[sieve]
        if high < lower or low > upper:
            certain = True
        elif low < lower or high > upper:
            possible = True
        rows.append({'sieve_um': sieve, 'passing': [str(low), str(high)], 'limits_bp': list(bp)})
    state = 'incomplete_evidence' if missing else (
        'certain_violation' if certain else 'possible_violation' if possible else 'certified')
    return {'bucket': bucket['bucket'], 'state': state, 'sieves': rows}


def certified(bucket, limits):
    """True only when every envelope sieve is fully inside the evidence."""
    grams = bucket['net_g']
    curve = {sieve: (Fraction(low, grams), Fraction(high, grams))
             for sieve, low, high in bucket['evidence']}
    for sieve, (lower, upper), _ in limits:
        if sieve not in curve:
            return False
        low, high = curve[sieve]
        if low < lower or high > upper:
            return False
    return True


def parse_order(doc):
    grams = _grams(doc.get('dry_g'), 'mix_order_mass')
    if not isinstance(doc.get('order'), str) or not doc['order'].strip():
        raise ValueError('mix_order_name')
    limits = envelope(doc)
    allowed = doc.get('allowed_buckets')
    if allowed is not None and (not isinstance(allowed, list) or not allowed
                                or any(not isinstance(code, str) or not code.strip() for code in allowed)
                                or len(set(allowed)) != len(allowed)):
        raise ValueError('mix_allowed_buckets')
    return {'order': doc['order'], 'dry_g': grams, 'envelope': limits,
            'allowed': allowed, 'rework_only': bool(doc.get('rework_only', False))}


def stored_envelope(limits):
    """JSON-safe [sieve, [low_bp, high_bp]] rows for persistence."""
    return [[sieve, list(bp)] for sieve, _, bp in limits]
