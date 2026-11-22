"""Coarsen archived assays at physically measured cuts, never at inferred cuts."""
from .dry_mass import closed_mass
from .sieving import sieve_grid


def archived_curves(archive):
    if not isinstance(archive, dict) or not 2 <= len(archive) <= 20 or any(not name.strip() for name in archive):
        raise ValueError('archived_assays')
    cumulative, masses = {}, {}
    for key, assay in sorted(archive.items()):
        grid = sieve_grid(assay)
        retained, _, mass = closed_mass(assay, len(grid))
        passed = mass
        cumulative[key] = {}
        for cut, grams in zip(grid, retained):
            passed -= grams
            cumulative[key][cut] = passed
        masses[key] = mass
    return cumulative, masses


def reconcile(doc):
    required = doc['archive_required_cuts_um']
    if (not isinstance(required, list) or not 1 <= len(required) <= 30
            or any(type(n) is not int or not 1 <= n <= 1_000_000 for n in required)
            or required != sorted(set(required), reverse=True)):
        raise ValueError('archive_required_cuts')
    cumulative, masses = archived_curves(doc['archived_assays'])
    indices = {key: {cut: i for i, cut in enumerate(curve)} for key, curve in cumulative.items()}
    shared = set.intersection(*(set(curve) for curve in cumulative.values()))

    missing = {str(cut): [key for key, curve in cumulative.items() if cut not in curve]
               for cut in required if cut not in shared}
    cuts = sorted(shared, reverse=True)
    if not cuts:
        return {'state': 'no_shared_cut', 'shared_cuts_um': [], 'missing_required': missing, 'bins': []}
    edges = [None, *cuts, None]
    bins = []
    for index, (coarse, fine) in enumerate(zip(edges, edges[1:])):
        specimens = {}
        for key, curve in cumulative.items():
            begin = 0 if coarse is None else indices[key][coarse]+1
            end = len(curve)+1 if fine is None else indices[key][fine]+1
            grams = (masses[key] if coarse is None else curve[coarse]) - (0 if fine is None else curve[fine])
            specimens[key] = {'mass_g': grams, 'source_bin_span': [begin, end]}
        bins.append({'index': index, 'coarse_um': coarse, 'fine_um': fine, 'specimens': specimens,
                     'pooled_g': sum(row['mass_g'] for row in specimens.values())})
    return {'state': 'partial' if missing else 'comparable', 'shared_cuts_um': cuts,
            'missing_required': missing, 'dry_mass_g': masses, 'bins': bins}
