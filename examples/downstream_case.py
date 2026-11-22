OPERATION = 'archive-screen'
OPTIONAL_FIELD = 'archive_screen'


def perturb(doc):
    b = doc['archived_assays']['b']
    b['retained_g'][0] -= 12
    b['pan_g'] += 12


def verify(before, after):
    assert before['state'] == 'within' and before['passing_bp'] == ['5000', '23000/3']
    assert before['specimens']['a'] == {'fine_um': 3000, 'coarse_um': 3000, 'passing_g': [50, 50]}
    assert before['specimens']['b']['passing_g'] == [100, 180]
    assert after['state'] == 'needs_screening' and after['pooled_passing_g'] == [162, 242]
