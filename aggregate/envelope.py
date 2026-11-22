"""Read the target envelope and measure each blend against its tightest sieve."""
from fractions import Fraction


def target_ranges(doc, grid):
    bounds = doc['limits_bp']
    if not isinstance(bounds, list) or len(bounds) != len(grid):
        raise ValueError('envelope_shape')
    result = []
    for row in bounds:
        if not isinstance(row, list) or len(row) != 2 or any(type(x) is not int for x in row) or not 0 <= row[0] <= row[1] <= 10_000:
            raise ValueError('envelope_range')
        result.append(tuple(Fraction(x, 10_000) for x in row))
    return result


def shaped_curve(curves, components, count):
    total = sum(components.values())
    return [sum(curves[lot][i] * Fraction(mass, total) for lot, mass in components.items()) for i in range(count)]


def violations(grid, limits, curve):
    facts = []
    for sieve, (lower, upper), value in zip(grid, limits, curve):
        margin = min(value - lower, upper - value)
        state = 'low' if value < lower else ('high' if value > upper else 'inside')
        facts.append((sieve, value, margin, state))
    return facts


def serialize(facts):
    return [{'sieve_um': n, 'passing': str(v), 'margin': str(m), 'state': s} for n, v, m, s in facts]
