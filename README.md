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

### 留样整包托管（交接柜离线发样）

交接柜断网时仍要按单发样：总库以唯一封签整包交柜，出柜前按实际包组合核对保底单与可接候补单；托管量从总库可领量扣开，但没有实发票就不计作已发。实发与交回凭据可乱序到达，凭票号幂等；只扫到实物或只收到凭据的包记为待对账，不默认为可领。交回包恢复可领的时机可选包级（归属独立确认即恢复）或轮级（本轮全部对平后恢复），两种策略最终账一致。口头退柜、同重量别柜包、重复交回凭据都不能供现场领用；不交柜的领样流程照常使用。规则与接口见 [领域规则与接口](PROJECT_SPEC.md#留样整包托管)。

端到端演练：`python3 -B examples/archive_custody_check.py`。脚本用同档 40、30、30 克三包分交两柜，演练乱序凭据、待对账、两种恢复策略及各项禁止口径。

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
