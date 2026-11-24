from aggregate.blend import compare as recipe
from aggregate.sieving import sieve_cards as assays
from aggregate.blend import trial_grid as trial_grid
from aggregate.composite import composite as composite
from aggregate.batch_budget import account as budget
from aggregate.assay_recheck import verify as recheck
from aggregate.replicate_envelope import review as replicate_envelope
from aggregate.shared_cuts import reconcile as shared_cuts
from aggregate.archive_limits import screen as archive_screen
from aggregate.archive_handoff import prepare as archive_handoff

PRODUCT = '0015-aggregate-sieve-blending'
TITLE = '粒料筛分工作台'
WORK_LABEL = '级配实验 / 干质量'
THEME = 'sieve'
DEFAULT_DATASET = 'grading-lot'
PORT = 8815

OPERATIONS = {
    'recipe': ('配方对照', recipe),
    'assays': ('筛分实测', assays),
    'trial-grid': ('配比试配', trial_grid),
    'composite': ('复样合成', composite),
    'budget': ('物料预算', budget),
    'recheck': ('复测级配', recheck),
    'replicate-envelope': ('复样包络核验', replicate_envelope),
    'shared-cuts': ('共用筛档归档', shared_cuts),
    'archive-screen': ('异筛归档限值核验', archive_screen),
    'archive-handoff': ('筛样留样交接', archive_handoff),
}
def _review_eligible(report):
    return report['accepted']


REVIEW = ('replicate-envelope', _review_eligible)
REQUIRED = ('recipe', 'assays', 'trial-grid')

# 整包托管交回包恢复可领的时机：'package' 归属独立确认即恢复，'round' 等本轮全部对平后恢复。
CUSTODY_RESTORE = 'package'
