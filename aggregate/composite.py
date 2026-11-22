"""Combine independent replicate specimens by weighed dry mass."""
from .sieving import assays, sieve_cards, sieve_grid
from .dry_mass import closed_mass


def composite(doc):
    grid = sieve_grid(doc)
    original = doc['assays']
    assays(doc, grid)
    lots = doc['replicates']
    if not isinstance(lots, dict) or not 1 <= len(lots) <= 20 or lots.keys() - original.keys():
        raise ValueError('replicate_lots')
    merged = {}
    for lot, rows in lots.items():
        if not isinstance(rows, list) or not 2 <= len(rows) <= 12:
            raise ValueError('replicate_count')
        samples = {row['id']: row for row in rows}
        if len(samples) != len(rows) or any(not isinstance(key, str) or not key.strip() for key in samples):
            raise ValueError('replicate_identity')
        masses = [closed_mass(row, len(grid)) for row in samples.values()]
        merged[lot] = {'dry_g': sum(dry for _, _, dry in masses), 'pan_g': sum(pan for _, pan, _ in masses),
                       'retained_g': [sum(weights) for weights in zip(*(r for r, _, _ in masses))]}
    cards = sieve_cards({**doc, 'assays': merged})
    return {'rows': [{'lot': lot, 'specimens': [r['id'] for r in lots[lot]], 'mass': mass, 'sieves': cards[lot]}
                     for lot, mass in sorted(merged.items())]}
