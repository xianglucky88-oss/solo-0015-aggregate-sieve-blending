"""Assess fixed dry-mass recipes and search the two-source batching grid."""
from .envelope import serialize, shaped_curve, target_ranges, violations
from .sieving import normalized


def compare(doc):
    grid, curves = normalized(doc)
    limits = target_ranges(doc, grid)
    parts = doc['parts_g']
    if not isinstance(parts, dict) or not parts or parts.keys() - curves.keys() or any(type(n) is not int or not 0 <= n <= 10_000_000 for n in parts.values()) or not sum(parts.values()):
        raise ValueError('recipe_parts')
    facts = violations(grid, limits, shaped_curve(curves, parts, len(grid)))
    return {'accepted': all(row[3] == 'inside' for row in facts), 'sieves': serialize(facts)}


def trial_grid(doc):
    grid, curves = normalized(doc)
    limits = target_ranges(doc, grid)
    first, second = doc['pair']
    steps = doc['divisions']
    if first == second or first not in curves or second not in curves:
        raise ValueError('trial_sources')
    if type(steps) is not int or not 2 <= steps <= 1_000:
        raise ValueError('trial_divisions')
    accepted = []
    best = None
    for share in range(1, steps):
        facts = violations(grid, limits, shaped_curve(curves, {first: share, second: steps-share}, len(grid)))
        margin = min(row[2] for row in facts)
        if margin >= 0:
            accepted.append(share)
            if best is None or margin > best[0]:
                best = (margin, share, facts)
    if best is None:
        return {'state': 'no_grid_match', 'feasible_first_units': [], 'divisions': steps}
    margin, share, facts = best
    return {'state': 'matched', 'feasible_first_units': accepted, 'divisions': steps, 'parts': {first: share, second: steps-share}, 'worst_margin': str(margin), 'sieves': serialize(facts)}
