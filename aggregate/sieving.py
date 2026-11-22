"""Normalize dry sieve assays to exact cumulative passing fractions."""
from fractions import Fraction

from .dry_mass import closed_mass


def sieve_grid(doc):
    grid = doc['sieves_um']
    if not isinstance(grid, list) or not 2 <= len(grid) <= 30 or any(type(n) is not int or not 0 < n <= 1_000_000 for n in grid):
        raise ValueError('sieve_grid')
    if grid != sorted(set(grid), reverse=True):
        raise ValueError('sieve_order')
    return grid


def assays(doc, grid):
    lots = doc['assays']
    if not isinstance(lots, dict) or not 1 <= len(lots) <= 20 or any(not k.strip() for k in lots):
        raise ValueError('assay_lots')
    results = {}
    for lot, row in lots.items():
        retained, _, mass = closed_mass(row, len(grid))
        remaining, curve = mass, []
        for shelf in retained:
            remaining -= shelf
            curve.append(Fraction(remaining, mass))
        results[lot] = curve
    return results


def normalized(doc):
    grid = sieve_grid(doc)
    return grid, assays(doc, grid)


def sieve_cards(doc):
    grid, data = normalized(doc)
    return {lot: [{'sieve_um': sieve, 'passing': str(v)} for sieve, v in zip(grid, curve)] for lot, curve in data.items()}
