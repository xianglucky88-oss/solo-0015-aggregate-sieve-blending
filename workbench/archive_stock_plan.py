"""Resolve a historical handoff against its measured bins, never against the live head."""
from aggregate.shared_cuts import reconcile

from . import evidence
from .codec import encode
from .errors import Problem


def cell_key(specimen, bounds):
    return encode([specimen, bounds])


def handoff(db, run_id):
    bundle = evidence.export(db, run_id)
    run = bundle['result']
    if run['operation'] != 'archive-handoff':
        raise Problem('stock_requires_handoff', 409)
    measured = {
        cell_key(specimen, [bin_row['coarse_um'], bin_row['fine_um']]): row['mass_g']
        for bin_row in reconcile(bundle['document'])['bins']
        for specimen, row in bin_row['specimens'].items()
    }
    output = run['output']
    holds, orders = {}, {}
    for row in output['cells']:
        key = cell_key(row['specimen'], row['bounds_um'])
        holds[key] = row['held_g']
        for order, grams in row['orders_g'].items():
            orders[order] = (key, grams)
    return bundle['dataset'], run['revision'], output['state'], measured, holds, orders
