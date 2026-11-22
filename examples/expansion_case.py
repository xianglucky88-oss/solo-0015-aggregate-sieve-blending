OPERATION = 'shared-cuts'
OPTIONAL_FIELD = 'archived_assays'


def perturb(doc):
    doc['archived_assays']['b']['sieves_um'][1] = 1800


def verify(before, after):
    assert before['state'] == 'comparable' and before['shared_cuts_um'] == [4000, 2000]
    assert [row['pooled_g'] for row in before['bins']] == [50, 140, 110]
    assert before['bins'][1]['specimens']['a'] == {'mass_g': 60, 'source_bin_span': [1, 3]}
    assert before['bins'][2]['specimens']['b'] == {'mass_g': 100, 'source_bin_span': [2, 4]}
    assert after['state'] == 'partial' and after['shared_cuts_um'] == [4000]
    assert after['missing_required'] == {'2000': ['b']}
    assert [row['pooled_g'] for row in after['bins']] == [50, 250]
    for result in (before, after):
        for key, total in result['dry_mass_g'].items():
            assert sum(row['specimens'][key]['mass_g'] for row in result['bins']) == total


def edge_cases(doc):
    from aggregate.shared_cuts import reconcile

    archive = doc['archived_assays']
    shifted = {**archive, 'b': {**archive['b'], 'sieves_um': [3950, 1900, 1000]}}
    missing = reconcile({**doc, 'archived_assays': shifted})
    assert (missing['state'], missing['bins']) == ('no_shared_cut', [])
    invalid = {**archive, 'b': {**archive['b'], 'pan_g': 39}}
    try:
        reconcile({**doc, 'archived_assays': invalid})
    except ValueError as exc:
        assert str(exc) == 'assay_mass_not_closed'
    else:
        raise AssertionError('accepted an unclosed archived sample')
