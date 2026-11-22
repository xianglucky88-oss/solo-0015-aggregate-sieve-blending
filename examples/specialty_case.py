from copy import deepcopy

from aggregate.archive_handoff import prepare


OPERATION = 'archive-handoff'
OPTIONAL_FIELD = 'archive_handoff'


def perturb(doc):
    assay = doc['archived_assays']['a']
    assay['retained_g'][1] -= 2
    assay['pan_g'] += 2


def verify(before, after):
    assert before['state'] == 'ready' and before['released_orders'] == ['bench-east', 'bench-west']
    row, = before['cells']
    assert (row['measured_g'], row['remaining_g'], row['source_bin_span']) == (60, 1, [1, 3])
    assert after['state'] == 'withheld' and not after['released_orders']
    assert after['cells'][0]['measured_g'] == 58 and after['cells'][0]['remaining_g'] == -1


def edge_cases(doc):
    a = deepcopy(doc)
    a['archive_handoff']['orders']['bench-east']['bounds_um'] = [4000, 3000]
    assert not prepare(a)['released_orders']
    assert any(row['state'] == 'unmeasured_bin' for row in prepare(a)['cells'])
