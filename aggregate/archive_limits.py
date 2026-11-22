"""Screen the pooled archive at an unmeasured cut using monotone mass bounds."""
from fractions import Fraction

from .shared_cuts import archived_curves


def screen(doc):
    cut, lower, upper = doc['archive_screen']
    if (any(type(n) is not int for n in (cut, lower, upper))
            or not 1 <= cut <= 1_000_000 or not 0 <= lower <= upper <= 10000):
        raise ValueError('archive_screen_limits')
    curves, dry = archived_curves(doc['archived_assays'])
    rows = {}
    for key, curve in curves.items():
        coarser = min((n for n in curve if n >= cut), default=None)
        finer = max((n for n in curve if n <= cut), default=None)
        rows[key] = {'fine_um': finer, 'coarse_um': coarser,
                     'passing_g': [curve.get(finer, 0), curve.get(coarser, dry[key])]}
    pooled = [sum(row['passing_g'][i] for row in rows.values()) for i in range(2)]
    minimum, maximum = [Fraction(n*10000, sum(dry.values())) for n in pooled]
    state = ('within' if lower <= minimum <= maximum <= upper else
             'outside' if maximum < lower or minimum > upper else 'needs_screening')
    return {'state': state, 'cut_um': cut, 'limits_bp': [lower, upper], 'pooled_passing_g': pooled,
            'passing_bp': [str(minimum), str(maximum)], 'specimens': rows}
