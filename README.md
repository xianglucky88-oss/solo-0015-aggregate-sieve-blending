# 粒料筛分工作台

原生 HTML、CSS 和 JavaScript 工作台，业务计算由 Python 完成，使用本机 SQLite 持久化。无需安装前端依赖。仅用于本地合成样本，不对公网开放。

```sh
python3 -B -m workbench
```

浏览器打开命令输出的地址（默认 `http://127.0.0.1:8815`）。工作台支持样例与文件导入、发布历史、指定版本加工、对比、复算和证据导出。可用 `--port`、`--db` 指定本机端口和数据库文件。

新增领域加工：复样合成、物料预算、复测级配。流程验收：`python3 -B examples/workbench_check.py`。

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
