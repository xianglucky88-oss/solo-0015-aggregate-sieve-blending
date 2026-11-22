"""Reserve archive material against the exact common coarsening of measured bins."""
from .shared_cuts import reconcile


def prepare(doc):
    plan = doc['archive_handoff']
    orders, holds = plan['orders'], plan['holds']
    if not isinstance(orders, dict) or not 1 <= len(orders) <= 20 or any(not k.strip() for k in orders):
        raise ValueError('handoff_orders')
    if not isinstance(holds, list) or len(holds) > 120:
        raise ValueError('handoff_holds')
    archive = reconcile(doc)
    weights = {(specimen, row['coarse_um'], row['fine_um']): data
               for row in archive['bins'] for specimen, data in row['specimens'].items()}

    def parse(item):
        specimen = item['specimen']
        coarse, fine = item['bounds_um']
        grams = item['mass_g']
        if (not isinstance(specimen, str) or type(grams) is not int or not 1 <= grams <= 1_000_000_000
                or any(n is not None and (type(n) is not int or not 1 <= n <= 1_000_000)
                       for n in (coarse, fine))):
            raise ValueError('handoff_cell')
        key = (specimen, coarse, fine)
        if specimen not in doc['archived_assays']:
            raise ValueError('handoff_specimen')
        return key, grams

    cells = {}
    for item, name in [(item, None) for item in holds] + [(orders[name], name) for name in sorted(orders)]:
        key, grams = parse(item)
        row = cells.setdefault(key, {'held_g': 0, 'orders_g': {}})
        if name is None:
            row['held_g'] += grams
        else:
            row['orders_g'][name] = grams

    rows = []
    for key, row in cells.items():
        specimen, coarse, fine = key
        need = row['held_g'] + sum(row['orders_g'].values())
        evidence = weights.get(key)
        if evidence is None:
            row.update(state='unmeasured_bin')
        else:
            remaining = evidence['mass_g'] - need
            row.update(state='available' if remaining >= 0 else 'short', remaining_g=remaining,
                       measured_g=evidence['mass_g'], source_bin_span=evidence['source_bin_span'])
        rows.append({'specimen': specimen, 'bounds_um': [coarse, fine], **row})
    ready = all(row['state'] == 'available' for row in rows)
    return {'state': 'ready' if ready else 'withheld', 'cells': rows,
            'released_orders': sorted(orders) if ready else []}
