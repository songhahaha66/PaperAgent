# 可读成果与论文工作台验证（2026-10-19）

基线：`origin/dev` 的 `72be9b1379ae2367cab7184cf2d9182c2e557257`。本批不部署、不启动或恢复已有用户研究、不调用付费模型。

## 实现范围

- 首页彻底移除执行设置；新工作仍按既有固定默认值初始化；研究目标、附件、开始研究、模板和交付格式保留。现有后端显式上限、旧项目预算、累计用量及连接超时兼容规则未放宽。
- `GET /api/chat/work/{work_id}/outcomes` 提供只读任务成果，同时在既有 research snapshot 附加 `outcomes`。沿用原结果、尝试、报告、结论与来源登记，不生成新的权威证据。按任务展示方案说明、数值、图表/序列、结论、来源和已记录的下一步理由；实验继续读取原冻结比较记录，增加按实际协议指标生成的比较表与观测/预测图。
- 计算、成果可用性与检查分别显示。检查服务故障不隐藏已计算成果；无审查记录不标通过；模型疑点显示待核实；确定性错误、版本变更与输入变化保留原依赖/发布限制。读取成果不会改验收状态或扩大写作登记表；新投影不额外进入只读追问的模型上下文。
- 论文无正文时隐藏章节选择、空历史与无效预览/导出操作；有正文继续沿用原阅读、手动/模型候选、差异、版本和冲突检测接口。反馈修订、局部重算、标签页/分屏、方法、经验、模板及多格式交付保留。

旧记录没有说明或单位时保留原名称/编号和值，不从变量名推断含义。序列没有结构化横轴说明时明确使用登记顺序；长序列图显示前 1000 个登记值，完整原始记录仍可展开。来源列表是本次尝试冻结的输入，不虚构单项成果与数据文件的精确对应。没有下一步决策/理由时明确显示缺失。历史成果保留可读，但不会因此重新进入活动登记表。

## 实际计算与浏览器

所有项目均为隔离 SQLite 和隔离工作目录中新建、标题含 `[TEST 无模型验证]` 的项目。输入为构造数据，候选代码回复受控，Python 子进程、sklearn 估计器、划分、指标、置换负对照及图表均实际执行。**外部模型调用为 0；不是完整真实模型端到端研究，也不据此声称科学结论已验收。**

- `34358d57`：120 个构造销量观测，时间留出，OLS 与标准化 Ridge(alpha=1) 两方案走现有实验执行器。实际 RMSE 分别为 `1.5515112512641693`、`1.5468040284225286`，共同基线为 `42.651749335128954`。既存选择理由为“按项目重算 RMSE 选择候选 2，未使用 critic 或 stdout 分数”；没有统计显著性推断。正式登记展示来自另一次真实内核输出核对，代码/输出/图表和冻结来源均保留。随后注入检查服务故障，成果仍可查看，状态为未完成检查，发布仍受原门槛约束。
- `b7ad2e8d`：另一领域的蒙特卡洛积分，固定 seed=42，实际运行一万个样本，圆周率估计为 `3.1388`，保存收敛序列及 PNG。未做模型审查，界面不标通过，也不编造下一步决策。正文为明确标记的手工测试正文；真实浏览器生成手动候选、查看差异并应用，旧版本完整保留。
- `877253ce`：旧登记数据缺少含义、单位、方案和下一步；界面保持保守回退。
- `7a3498de`：空论文；没有空章节、空历史或无效编辑/预览操作。

Chromium 使用真实 Vue 页面、TCP FastAPI、实际数据库和工作区文件，无 HTTP 或 WebSocket 响应替身；覆盖首页、成果、实验、来源链接、旧数据、空论文、有正文差异与应用、手机视口。浏览器无页面脚本错误、无外部请求。没有点击研究开始/恢复；手动文稿候选不调用模型。

## 检查结果

完整后端批次 **762 passed**；最终成果/快照/空论文/所有权等定向批次 **89 passed**，后续投影补充批次 **55 passed**（相互重叠，不合并为独立测试总数）。前端 `npm run test:workspace`、`npm run build`（含类型检查）通过，`git diff --check` 通过。浏览器截图及断言见下方。

## 验证与复现资料

本环境资料目录：`/workspace/scratch/paperagent-refactor-20261019/`。

- `seed.py`、`fixture-summary.json`、`sales-actual.json`、`montecarlo-actual.json`：构造输入、实际计算与保存的决策；`private-browser.json` 是仅本地测试凭据，不提交、不作为交付物。
- `browser-check.cjs`、`browser-check.log`、`browser-checks.json`：真实页面操作及断言。
- 生产构建还通过只读浏览器复核：`browser-production.cjs`、`production-browser-checks.json`，截图 `12-production-home.png`、`13-production-results.png`、`14-production-empty.png`。
- `01-home.png`、`02-progress-service-failure.png`、`03-readable-sales.png`、`04-sales-sources-reason.png`、`05-experiment-metrics-charts.png`、`06-generic-montecarlo.png`、`07-paper-edit-diff.png`、`08-paper-applied-history.png`、`09-legacy-results.png`、`10-empty-paper.png`、`11-mobile-paper.png`。
- `backend-tests-final.log`、`projection-final-tests.log`、`frontend-tests-final.log`、`frontend-build-final.log`：回归、类型检查及生产构建输出。

云环境原缺少 `ctexart.cls`。Debian 官方中文 TeX 包仅下载、解包到本验证目录，通过 `TEXMFHOME`/`TEXMFVAR` 供测试使用，没有修改系统或项目依赖。两个原生 LaTeX 导出测试在补齐后通过。旧恢复测试改为先验证同一耗尽操作仍被保护，再通过实际不同的受控连接标识验证恢复，不清零预算或删除失败证据。旧连接默认值断言更新为现有执行策略的 600 秒。

CI 保留 dev 推送触发，并增加前端工作台回归；部署 workflow 仍只对 master 自动触发。本批只 push dev。
