"""Evaluate one planned recipe against independently sampled lot assays."""
from .blend import compare


def verify(doc):
    replacements = doc['recheck_assays']
    if not isinstance(replacements, dict) or not replacements or replacements.keys() - doc['assays'].keys():
        raise ValueError('recheck_lots')
    original = compare(doc)
    repeat = compare({**doc, 'assays': {**doc['assays'], **replacements}})
    rows = [{'prior': left, 'repeat': right} for left, right in zip(original['sieves'], repeat['sieves'])]
    return {'accepted': original['accepted'] and repeat['accepted'], 'prior_accepted': original['accepted'],
            'repeat_accepted': repeat['accepted'], 'changed_count': sum(r['prior'] != r['repeat'] for r in rows),
            'assay_replacements': sorted(replacements), 'rows': rows}
