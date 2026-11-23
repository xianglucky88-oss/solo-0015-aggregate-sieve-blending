# 粒料筛分工作台

原生 HTML、CSS 和 JavaScript 工作台，业务计算由 Python 完成，使用本机 SQLite 持久化。无需安装前端依赖。仅用于本地合成样本，不对公网开放。

```sh
python3 -B -m workbench
```

浏览器打开命令输出的地址（默认 `http://127.0.0.1:8815`）。工作台支持样例与文件导入、发布历史、指定版本加工、对比、复算和证据导出。可用 `--port`、`--db` 指定本机端口和数据库文件。

新增领域加工：复样合成、物料预算、复测级配。流程验收：`python3 -B examples/workbench_check.py`。

### 共用筛档归档

在工作台运行 `shared-cuts`。按各样实测筛孔交集归档各档干质量和原始档位，可逐样对回总干重。演练：`python3 -B examples/workbench_check.py shared-cuts`。

### 异筛归档限值核验

在工作台运行 `archive-screen`。用样品各自夹住目标孔径的实测值和实际干重推上下界，区分全可行、全越限和待补测。演练：`python3 -B examples/workbench_check.py archive-screen`。

### 筛样留样交接

在工作台运行 `archive-handoff`。将保留量与同批领用量落到实测共用筛档，按样品逐档出守恒账和批次核准。演练：`python3 -B examples/workbench_check.py archive-handoff`。

### 筛样实物台账与对账

留样交接仍只核准计划。实物台账通过本机 HTTP API 单独登记期初量，以交接单记录实际领用；实物退样和数量错票各自追加凭据。新版计划不会重置库存，可用任意一个历史计划对照当前台账。接口和对账口径见 [领域规则与接口](PROJECT_SPEC.md#筛样实物台账)。

端到端演练：`python3 -B examples/archive_stock_check.py`。脚本用临时数据库和合成称量数据，演练部分领用、退样、更正、重复及并发交单、计划变更与对账。

### 混合料桶供料承诺

剩料经称重、确认混匀后以新批号实物桶入账，不退回原筛档当鲜料；桶的有据组成由原混料、已用部分过筛凭据与退仓净重推定，凭据不自洽拒收。验方结论区分 `certified`、`possible_violation`、`certain_violation` 与 `incomplete_evidence`，只有全有据组成满足筛限的桶能承诺。开票可锁定具体桶（`commit-locked`）或只保证从允许桶供齐重量（`commit-pool`，不记候选桶号为锁定）；任一新承诺都须让全部未结承诺保有互不抢料的兑现办法，称量时再确认桶、重量与验方，且不得选一个满足本单却饿死其他承诺的桶。拒单区分验方不成立与合格料已被占用，同票重发不重复扣料。接口与口径见 [领域规则与接口](PROJECT_SPEC.md#混合料桶供料承诺)。

端到端演练：`python3 -B examples/mix_ledger_check.py`。脚本用同一份期初数据（80 g 回料桶过筛率 40%–60%、60 g 全过筛鲜料桶）核对两种承诺方式、两种第二单（35%–75% 与至少 90%）、只准回料且至少 55% 的拒单、逐单兑付余料、重复请求与并发双占。

### 成品复核与交接

运行 `replicate-envelope` 后在结果中打开「查看复核交接」。合格成品可通过复核后签发；不合格成品可退回且保留原判定。发布新修订或恢复旧修订都会换代，已经通过但未签发的旧复核不能补签。已签凭据可从同一运行的历史复核中下载。

旧版工作台数据库打开时会在事务中自动升级到 SQLite 结构 v4，不改写原始录入、历史运行或证据，也不会把旧计划当成实物流转。升级后的数据库不再由旧版工作台打开；保留旧版时请使用数据库的独立副本。

兼容保留原 HTTP 服务和数据格式，以下是原服务说明。

---

# 粒料筛分配比工作台

材料试配工程师录入料源的干质量、逐筛留量和筛底。服务核对质量闭合，计算干质量加权曲线，或从两料离散配方中选出最差筛孔裕量最大的方案。仅用合成数据，不用于工程验收。

## 运行

Python 3.11 或以上，仅标准库。切换到此仓库目录。

```sh
python3 -B -m aggregate.http_api --port 8742
curl --json @examples/demo.json http://127.0.0.1:8742/grading/trial-grid
```

不常驻启动也可运行以下演示；每次使用临时本机端口，结束后释放。

```sh
python3 -B -m aggregate.example /grading/assays
python3 -B -m aggregate.example /grading/recipe
python3 -B -m aggregate.example /grading/trial-grid
```

参见 [领域规则与接口](PROJECT_SPEC.md)。`examples/demo.json` 为完整合成输入；其他示例用于观察拒收或待核。业务单元测试留待后续，当前不假设联网、数据库或已有用户数据。

### 复样包络核验

发布更新后的 `examples/workbench.json`，在结果区选择「复样包络核验」。验收命令：`python3 -B examples/workbench_check.py replicate-envelope`。这项加工可选；老原料不含 `replicate_limits_bp` 时仍可使用原操作。
