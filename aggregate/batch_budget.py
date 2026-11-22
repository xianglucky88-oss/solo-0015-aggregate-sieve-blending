"""Check physical lot budgets separately from the fixed recipe envelope."""
from .blend import compare


def account(doc):
    grading = compare(doc)
    inventory = doc['inventory_g']
    lots = doc['assays']
    if not isinstance(inventory, dict) or inventory.keys() != lots.keys() or any(
            type(value) is not int or not 0 <= value <= 10_000_000 for value in inventory.values()):
        raise ValueError('inventory_mass')
    rows = []
    for lot, available in sorted(inventory.items()):
        used = doc['parts_g'].get(lot, 0)
        rows.append({'lot': lot, 'needed_g': used, 'available_g': available, 'balance_g': available-used})
    meets_budget = all(row['balance_g'] >= 0 for row in rows)
    return {'accepted': grading['accepted'] and meets_budget, 'recipe_in_spec': grading['accepted'],
            'inventory_sufficient': meets_budget, 'rows': rows, 'sieves': grading['sieves']}
