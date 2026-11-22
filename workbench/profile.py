from aggregate.blend import compare as recipe
from aggregate.sieving import sieve_cards as assays
from aggregate.blend import trial_grid as trial_grid
from aggregate.composite import composite as composite
from aggregate.batch_budget import account as budget
from aggregate.assay_recheck import verify as recheck

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
}
REQUIRED = ('recipe', 'assays', 'trial-grid')
