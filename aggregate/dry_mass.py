"""Validate the weighed constituents of a closed dry specimen."""


def closed_mass(row, sieve_count):
    retained = row['retained_g']
    pan = row['pan_g']
    dry = row['dry_g']
    if not isinstance(retained, list) or len(retained) != sieve_count:
        raise ValueError('retained_shape')
    if any(type(n) is not int or not 0 <= n <= 10_000_000 for n in (*retained, pan, dry)):
        raise ValueError('mass_range')
    if not dry or sum(retained) + pan != dry:
        raise ValueError('assay_mass_not_closed')
    return retained, pan, dry
