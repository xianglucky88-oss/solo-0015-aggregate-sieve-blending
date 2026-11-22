"""Keep individual replicate results alongside the exact mass-weighted blend."""
from fractions import Fraction

from .composite import composite
from .envelope import serialize, target_ranges, violations
from .sieving import assays, sieve_grid


def _verdict(grid, limits, curve):
    facts = violations(grid, limits, curve)
    return {'accepted': all(row[3] == 'inside' for row in facts), 'sieves': serialize(facts)}


def review(doc):
    combined = composite(doc)
    grid = sieve_grid(doc)
    limits = target_ranges({**doc, 'limits_bp': doc['replicate_limits_bp']}, grid)
    reports = []
    for row in combined['rows']:
        lot = row['lot']
        samples = assays({'assays': {sample['id']: sample for sample in doc['replicates'][lot]}}, grid)
        individual = [{'id': key, **_verdict(grid, limits, curve)} for key, curve in sorted(samples.items())]
        bulk = _verdict(grid, limits, [Fraction(sieve['passing']) for sieve in row['sieves']])
        total_ok = all(sample['accepted'] for sample in individual)
        state = 'consistent_pass' if bulk['accepted'] and total_ok else (
            'masked_failure' if bulk['accepted'] else 'composite_failure')
        reports.append({'lot': lot, 'state': state, 'composite': bulk, 'specimens': individual})
    return {'accepted': all(row['state'] == 'consistent_pass' for row in reports), 'lots': reports}
