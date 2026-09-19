# PaperAgent AI 系统重设计计划

> 状态：提案（v1）｜范围：`backend/ai_system/` 及其与路由、前端的契约｜配套文档：`docs/plan-system-design.md`

## 0. 一页摘要

现在的 AI 系统是一个由长提示词驱动的 ReAct 编排器：模型决定流程、模型挑位置、模型判完成、模型写内容，代码只做子串匹配和事后 XML 比对。它能跑通 demo，但每一次失败都只能靠"再加一段 prompt"来修。

重设计的核心是四个反转：

| 现在 | 目标 |
|---|---|
| 模型掌控流程（ReAct 自由循环 + 3 轮整体重跑） | **代码掌控流程**：显式状态图，失败只重做失败的槽位 |
| 模板是一段塞进 prompt 的 Markdown 文本 | **模板是数据**：`TemplateSpec`（块 + 槽位 + 约束），上传时产出、确认一次、反复复用 |
| 内容是工具参数里的自由字符串，直接写进 docx | **内容是结构化 `blocks[]`**，写入唯一事实源 `PaperIR`，由渲染器落盘 |
| 判断 = 关键词/正则/LLM 自由文本 JSON | **判断层独立**：闭集问题 + 概率 + 置信度（Jev 优先，LLM 结构化输出回退），确定性校验兜底 |

目标不是"更聪明的 agent"，而是让弱一点的模型也写不坏文档、让每一步都可验证、让对话有记忆、让运行可恢复。

## 1. 现状诊断（只列决定设计的事实）

| 事实 | 证据 | 后果 |
|---|---|---|
| 每轮只把当前用户消息送进 agent，历史不进上下文 | `core_agents/main_agent.py` `run()`；`routers/chat_routes/chat.py` | 追问无记忆，"改一下第三章"靠猜 |
| 流程由 prompt 里的 Phase 1–4 约束，`recursion_limit=150` | `main_agent.py` `_create_system_prompt` | 行为不可预测，只能靠加提示修 |
| ReviewAgent 让 LLM 输出 `{complete}` JSON，失败整轮重跑，最多 3 次 | `core_agents/review_agent.py` | 成本高、不可量化、解析脆弱 |
| WriterAgent 一次调用同时决定位置与内容：`write_to_template(anchor_text, content)` | `core_tools/docx_tools.py` | 位置错、示例残留、样式漂移都在写坏之后才发现 |
| 三套 docx 写入范式并存（python-docx / OOXML unpack-pack / docx-js） | `docx_tools.py`、`docx_skill/` | 样式决策散落，行为不一致 |
| 标题识别只认 `heading*` 样式名，忽略 `outlineLvl`、字号、编号 | `docx_styles.py`、`review_agent.py`、`plan_reconciler.py`（四份 outline 解析） | 中文自定义标题样式被当正文，骨架比对误报 |
| 计划状态靠关键词表推断（含写死的 demo 词） | `services/file_services/plan_reconciler.py` `_infer_item_status` | 状态不可信 |
| `BaseAgent`/`ContextManager`/smolagents 为死代码；`os.environ["WORKSPACE_DIR"]` 全局写入 | `core_agents/agent_base.py`、`core_managers/context_manager.py` | 维护噪音；并发串工作区 |
| 任务状态在进程内存，重启即丢 | `services/chat_services/task_manager.py` | 不可恢复、不可扩容 |

## 2. 目标与非目标

**目标**

1. Word 模板模式的成品在结构、样式、占位清理上 100% 通过确定性校验，且失败可定位到槽位。
2. 对话有记忆、运行可恢复、可取消、可人工确认。
3. Markdown 与 Word 共用一条流程，仅渲染器不同。
4. 每个模型调用都有明确的输入 schema 和输出 schema；换模型不改流程。
5. 有可重复的评测集，改 prompt 或模型前后可比。

**非目标**

- 不重写前端框架、不换 FastAPI/Postgres。
- 不追求"全自动无人工"：模板槽位低置信度时允许上传者确认一次。
- 不在本计划内实现 LaTeX 渲染器（但 IR 为其预留）。

## 3. 借鉴的设计

| 来源 | 借什么 | 落到哪 |
|---|---|---|
| 12-Factor Agents（HumanLayer） | Own your control flow / Tools are structured outputs / Small focused agents / Stateless reducer over an event log | 状态图取代 ReAct；`blocks[]` 结构化输出；每个节点 3–20 步内；事件日志 |
| Anthropic《Building effective agents》 | 优先用 workflow（routing、parallelization、orchestrator-workers、evaluator-optimizer），只在必要处用 agent | plan→fan-out→judge→repair 就是这四种模式的组合 |
| LangGraph | `StateGraph` + Postgres checkpointer、`Send` map-reduce、`interrupt` 人机协作、节点幂等 | 运行时骨架 |
| Docling `DoclingDocument` | Pydantic 定义的无损文档模型：内容项 + 阅读顺序树 + provenance；正文与 furniture 分离 | `PaperIR` / `TemplateSpec` 的形状 |
| Pandoc AST | 块/行内两级、`Header(level)`/`Para`/`Table`/`Image`，格式无关 | `blocks[]` 的类型集合；docx 读取回退 |
| OOXML（WordprocessingML） | `outlineLvl`、`numPr`、样式继承 `basedOn`、`w:sdt` 内容控件、表格/图/域 | 模板解析器的确定性信号 |
| TypeSafe Jev cookbooks（Structure recovery、Line-by-line search、Citation check、Classifying RAG passages） | 一次请求批量闭集问题、置信度门控、代码做算术模型做语义 | 判断层的问题模板 |
| AG-UI 事件协议 | `RUN_STARTED/STEP_*/TEXT_MESSAGE_*/TOOL_CALL_*/STATE_SNAPSHOT/STATE_DELTA(JSON Patch)` | 前后端事件契约，替代自由文本流 + 自定义 json_block |
| Spec-driven 工作流（Spec Kit / Kiro，已在 `plan-system-design.md`） | 稳定的任务契约：id、状态、依赖、当前焦点 | `Plan` 由 `TemplateSpec` 推导而非 LLM 写表 |

## 4. 目标架构

### 4.1 原则

1. 代码掌控流程；模型只做两件事：在闭集里做判断，或往有 schema 的槽位里生成内容。
2. 论文有唯一事实源（`PaperIR`），文件是渲染结果，随时可重生成。
3. 每一步可验证：写作前 schema 校验，写作后确定性校验，失败定位到槽位。
4. 状态在系统里（checkpoint + 事件），不在 prompt 里，也不只在文件里。
5. 判断带置信度；低置信度升级（更强模型或人），不猜。

### 4.2 分层

```
┌─────────────────────────────────────────────────────────────────┐
│  Interface   WebSocket/SSE 事件订阅（AG-UI 风格）· REST 查询       │
├─────────────────────────────────────────────────────────────────┤
│  Runtime     LangGraph StateGraph · Postgres checkpointer         │
│              事件日志 run_events · 取消/恢复/interrupt · 预算       │
├─────────────────────────────────────────────────────────────────┤
│  Nodes       intent · plan · gather · draft · judge · commit       │
│              render · validate · triage · repair · answer          │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  Judge       │  Generate    │  Sandbox     │  Render/Validate   │
│  Jev / LLM   │  writer LLM  │  独立进程     │  docx · markdown    │
│  结构化输出   │  blocks[]    │  代码执行     │  样式指纹 · 骨架     │
├──────────────┴──────────────┴──────────────┴────────────────────┤
│  Schemas     TemplateSpec · PaperIR · Plan · Draft · Events        │
└─────────────────────────────────────────────────────────────────┘
```

### 4.3 核心数据模型

所有模型用 Pydantic 定义，放在 `ai_system/schemas/`，是节点之间唯一的契约。

**TemplateSpec**（模板级，上传时产出，跨 work 复用）

```python
class Block(BaseModel):
    id: str                      # "P012" / "T000" / "G003"
    kind: Literal["paragraph", "table", "figure", "sdt"]
    style: str | None            # 解析后的样式名（含继承）
    outline_level: int | None    # 来自段落或样式的 w:outlineLvl
    numbered: bool
    font: FontHint | None        # bold / size_pt（含样式继承）
    text: str
    table: TablePreview | None

class Slot(BaseModel):
    id: str                      # "slot.2.1.code"
    role: Literal["heading", "fixed_text", "placeholder_fill", "example_delete",
                  "instruction_delete", "caption", "table", "figure_slot", "other"]
    anchor_block: str            # 相对定位的块 id
    section_path: list[str]      # ["2 实验内容", "2.1 创建数据表"]
    expects: list[BlockType]     # 允许写入的块类型
    constraints: SlotConstraints # style / min_chars / language / code_style / caption_style
    confidence: float
    source: Literal["ooxml", "judge", "human"]

class TemplateSpec(BaseModel):
    template_id: int
    version: int
    blocks: list[Block]
    slots: list[Slot]
    format_rules: list[FormatRule]      # 结构化：{"scope":"body","font":"宋体","size_pt":12,"line_spacing":1.5}
    style_fingerprint: StyleFingerprint # 现有 docx_styles 产出
    furniture: Furniture                # 页眉/页脚/封面图/校徽 等必须保留的部件
```

**PaperIR**（work 级唯一事实源）

```python
class Paragraph(BaseModel): type: Literal["paragraph"]; text: str
class Code(BaseModel):      type: Literal["code"]; language: str; text: str
class ListBlock(BaseModel): type: Literal["list"]; ordered: bool; items: list[str]
class FigureRef(BaseModel): type: Literal["figure"]; artifact_id: str; caption: str
class TableRows(BaseModel): type: Literal["table_rows"]; table_id: str; rows: list[list[str]]
class Equation(BaseModel):  type: Literal["equation"]; latex: str
class Citation(BaseModel):  type: Literal["citation"]; ref_id: str
BlockContent = Paragraph | Code | ListBlock | FigureRef | TableRows | Equation | Citation

class SectionContent(BaseModel):
    slot_id: str
    blocks: list[BlockContent]
    provenance: Provenance        # model, prompt_version, run_id, judged_by
    revision: int

class PaperIR(BaseModel):
    work_id: str
    revision: int
    sections: dict[str, SectionContent]   # slot_id → 内容
    artifacts: dict[str, Artifact]        # 图表/数据产物
    references: dict[str, Reference]
```

**Plan**（由 `TemplateSpec.slots` 推导；状态由 IR 计算，不由模型或关键词推断）

```python
class Task(BaseModel):
    slot_id: str
    title: str
    needs: TaskNeeds                # code / search / figure
    depends_on: list[str]
    status: Literal["pending", "in_progress", "drafted", "committed", "blocked"]

class Plan(BaseModel):
    tasks: list[Task]
    batches: list[list[str]]        # 按依赖分批，供并行 fan-out
```

前端仍消费 `plan.json`（保持 `plan-system-design.md` 契约），但它成为 `Plan` 的投影而非 LLM 产物。

### 4.4 状态图

```
START ─▶ load_context ─▶ classify_intent ─┬─ question/chat ─▶ answer ─▶ END
                                          └─ 写作/修改 ─▶ ensure_template_spec ─▶ plan
plan ─▶ ┬─ pending 为空 ─▶ render
        └─ 否 ─▶ fan_out(Send × 当前批次) ─▶ [task 子图] ─▶ join ─▶ commit ─▶ plan(下一批)
render ─▶ validate ─▶ ┬─ 无 issue ─▶ finalize ─▶ END
                      └─ 有 issue ─▶ triage ─▶ ┬─ 可修且轮数未超 ─▶ plan(仅受影响 slot)
                                                └─ 否 ─▶ human_confirm(interrupt) ─▶ finalize

task 子图:  gather? ─▶ draft ─▶ judge ─▶ ┬─ pass ─▶ 返回 Draft
                                          └─ revise(≤2) ─▶ draft(带 judge 反馈)
```

节点职责与模型使用：

| 节点 | 性质 | 模型 | 输入 → 输出 |
|---|---|---|---|
| `load_context` | 代码 | – | 读 TemplateSpec / IR 快照 |
| `classify_intent` | 判断 | judge | 用户消息 + plan 摘要 → `EditIntent{kind, target_slots[]}` |
| `ensure_template_spec` | 代码/生成 | brain（仅无模板） | 无模板时用 schema 输出章节结构 → TemplateSpec |
| `plan` | 代码 | – | TemplateSpec + IR + intent → Plan、当前批次 |
| `gather` | 代码 + coder | coder | 沙箱执行/检索 → artifacts |
| `draft` | 生成 | writer | 槽位约束 + 邻近上下文 + IR 摘要 → `Draft{slot_id, blocks[]}`（结构化输出，**无工具**） |
| `judge` | 判断 | judge | Draft → `Judgement{substantive, example_left, follows_rules, citation_support, confidence}` |
| `commit` | 代码 | – | 通过的 Draft 写入 IR，revision+1 |
| `render` | 代码 | – | IR + TemplateSpec → docx / md |
| `validate` | 代码（可选 vision 抽检） | – | 样式指纹、骨架按 slot、表格数、占位/示例清零、图片存在 → `issues[]` |
| `triage` | 代码 + 判断 | judge（模糊时） | issues → 受影响 slot 与修复动作 |
| `human_confirm` | interrupt | 人 | 待确认项 → 决策 |
| `answer` | 生成 | brain | 仅对提问/闲聊回复 |
| `finalize` | 代码 | – | 汇总事件、摘要消息 |

条件边全部是纯函数；硬停止条件来自代码：每槽位修复轮数 ≤ 2、总预算（token/时间）、批次数。

### 4.5 判断层（Judge）

- 接口：`judge.ask(state: dict, questions: dict[str, Question]) -> Answers`，`Question ∈ {Noul, Choice, Score}`，答案含概率与置信度。
- 实现：`JevJudge`（TypeSafe System One API，`TYPESAFE_API_KEY`，可经 OpenRouter/Vercel Gateway）→ 无 key 或超时时 `LLMJudge`（结构化输出 + enum 约束）→ 极端回退 `HeuristicJudge`（现有关键词逻辑，仅保证不阻塞）。
- 使用规则（来自 Jev 文档的 jaggedness 页）：state 只放问题需要的块；每个 Choice 带 `other`；不让模型做算术/比数字（交给 validate）；criteria 描述具体情形；置信度阈值按任务在评测集上标定并 pin 模型版本。
- 问题模板集中在 `ai_system/judge/questions/`，与 prompt 一样受版本管理。

### 4.6 渲染层（Render）

- 唯一的 docx 渲染引擎：模板副本 + IR → 文档。每种 block 对应确定的 OOXML 操作（段落继承 slot 样式、代码等宽、列表复用 `numbering.xml`、图居中 + 图题样式、表格克隆行保留 `rPr` 并先删示例行、公式 OMML 或退化为图、引用统一生成）。
- 现有 `write_to_template` / `insert_image_to_template` / `fill_template_table` 的实现细节被吸收为渲染器内部函数；`create_docx`（docx-js）仅保留给"无模板 + 需要复杂版式"的路径，且输入也是 IR。
- Markdown 渲染器消费同一份 IR。
- 渲染是幂等的：同一 IR + 同一模板 → 同一文件，便于重放与 diff。

### 4.7 校验层（Validate）

全部确定性，产出 `ValidationIssue{code, slot_id, severity, detail}`：

| 检查 | 来源 |
|---|---|
| 样式指纹（页面、边距、优先样式、页眉页脚） | 现有 `docx_styles.compare_style_fingerprints` |
| 骨架：每个 `heading`/`fixed_text` slot 原样存在且顺序一致（按 slot id 与 `outlineLvl`，不再按文本相等） | 新 |
| 内容：每个 `placeholder_fill` 之后有 ≥ `min_chars` 正文；每个 `example_delete` 已消失；`instruction_delete` 已删 | 新 |
| 表格：数量一致、示例行已替换、列数匹配 | 新 |
| 图片：每个 `FigureRef` 的 artifact 已嵌入；`furniture` 图（校徽/封面）仍在 | 现有 `docx_images` |
| OOXML 合法性 | 现有 `docx_skill/scripts/office/validate.py` |
| 视觉抽检（可选）：`soffice` → PDF → 首页/随机页交给 vision 模型答闭集问题 | 现有 `soffice.py` + vision |

### 4.8 沙箱

`gather` 节点通过 `sandbox.run(code, inputs) -> RunResult{stdout, artifacts[]}` 调用独立进程（首期 subprocess + 资源限制，后期容器），产物登记为 `Artifact{id, path, mime, produced_by}`。API 进程不再 import 科学计算栈执行用户代码。

### 4.9 运行时

- `thread_id = work_id`，`AsyncPostgresSaver`（Psycopg 3）；节点设计为幂等（重放安全）。
- 事件：节点入口/出口发 AG-UI 风格事件（`RUN_STARTED`、`STEP_STARTED{node, slot_id}`、`TEXT_MESSAGE_*`（仅 draft/answer 流式）、`STATE_DELTA`（IR/plan 的 JSON Patch）、`CUSTOM{validation_issue}`、`RUN_FINISHED/ERROR`），写入 `run_events` 表并推送给订阅者；重连按 offset 回放。
- 取消：终止当前 invoke，checkpoint 保留到最后完成的 superstep；再次发消息从 `classify_intent` 继续。
- 预算：`Budget{tokens, cost, seconds}` 在每个模型节点后累加，超限走 `finalize` 并标注未完成槽位。
- Prompt：`ai_system/llm/prompts/*.md`，按节点拆分、参数化（语言、学科、格式规则），带版本号写入 provenance；不含任何示例论文内容。

### 4.10 目录形态

```
ai_system/
  schemas/      template_spec.py  paper_ir.py  plan.py  draft.py  events.py
  template/     ooxml_parser.py   role_labeler.py  spec_builder.py   # docx → TemplateSpec
  graph/        state.py  build.py  nodes/{intent,plan,gather,draft,judge,commit,render,validate,triage,answer}.py
  judge/        base.py  jev.py  llm.py  heuristic.py  questions/*.py
  render/       docx_renderer.py  markdown_renderer.py  ooxml/ (吸收 docx_skill 的 unpack/pack/validate)
  validate/     fingerprint.py  skeleton.py  content.py  visual.py
  sandbox/      runner.py
  llm/          providers.py  roles.py  prompts/*.md
```

## 5. 迁移路径

每个阶段独立可合并、可回退（feature flag `AI_PIPELINE=legacy|v2`），不阻塞现网。

### Phase 0 — 清理与地基（无行为变化）

- 删除 `agent_base.py`、`context_manager.py`、smolagents 与 `create_smolagents_model_from_config`、`PyPDF2`。
- 去掉 `os.environ["WORKSPACE_DIR"]` 全局写入与 `debug=True`。
- 合并四份 `_docx_outline` 为 `template/ooxml_parser.py` 的一个函数（含 `outlineLvl`、`numPr`、样式继承）。
- 把 MainAgent / WriterAgent / CodeAgent 的 prompt 抽到 `llm/prompts/`，删除 demo 示例与 `plan_reconciler` 中的 demo 关键词。
- 建 `run_events` 表与事件发射器（先与现有 `json_block` 并行双写）。
- **验收**：现有 52 个测试通过；线上行为不变；`ooxml_parser` 对 3 份真实中文模板的标题识别覆盖自定义样式。

### Phase 1 — 数据模型、模板解析、渲染器（离线可测）

- 落地 `schemas/`；`template/spec_builder.py`：解析 → 判断层打标 → `TemplateSpec`，上传阶段持久化到 `templates/.analysis/<id>/spec.json`，低置信度槽位在模板页面供上传者确认。
- `render/docx_renderer.py`：吸收现有写入工具；`render/markdown_renderer.py`。
- `validate/`：骨架/内容/表格/图片检查 + 现有指纹比对。
- **验收**：golden set（≥5 份真实模板 × 手工标注 slots）上，slot 角色准确率 ≥ 90%（judge）/≥ 98%（人工确认后）；用手写的 IR 渲染 → validate 零 issue；渲染幂等（两次输出字节级一致，时间戳除外）。

### Phase 2 — 状态图替换主循环

- `graph/`：`load_context → classify_intent → plan → draft(串行) → commit → render → validate → finalize`；`AsyncPostgresSaver`。
- `draft` 用结构化输出产出 `Draft`；`plan` 从 `TemplateSpec` 推导；前端 `plan.json` 由 `Plan` 投影。
- WebSocket handler 瘦身为鉴权 + 提交 run + 订阅事件；`task_manager` 由 checkpoint + `run_events` 取代。
- **验收**：Word 与 Markdown 两种模式在 golden set 上端到端产出通过 validate；断开重连 / 进程重启后 run 可继续；追问"修改第 N 节"只重写对应槽位。

### Phase 3 — 判断、并行、修复

- 接入 `judge`（Jev + 回退）：intent 分类、draft 评审、triage；`Send` 按批次并行 draft；`triage → plan(局部)` 修复循环；`human_confirm` interrupt。
- ReviewAgent 下线。
- **验收**：judge 在标注集上与人工一致率与置信度校准曲线达标（阈值写入配置并 pin 模型版本）；平均每篇 LLM 调用次数与 token 相比 legacy 下降（目标 ≥ 40%）；修复只触及失败槽位。

### Phase 4 — 沙箱、评测、前端事件化

- `sandbox/` 独立进程；`gather` 节点接入。
- 评测 harness：golden set + 指标（结构一致率、样式漂移、占位残留、引用核验、成本、时长）进 CI。
- 前端改为消费事件流（`STATE_DELTA` 驱动 plan/预览刷新），`Work.vue` 拆分；`legacy` 管线移除。
- **验收**：CI 每次 PR 跑评测并对比基线；前端预览随 `render_done` 事件精确刷新。

依赖关系：`P0 → P1 → P2 → P3 → P4`，其中 P1 的渲染器/校验器与 P0 可并行开发；P4 的评测 harness 应在 P1 末期就开始积累样本。

## 6. 评测与验收

- **Golden set**：真实模板（课程报告、毕业论文、期刊投稿各 ≥ 2）+ 需求描述 + 人工标注的 `TemplateSpec.slots` + 参考成品。
- **指标**：
  - 结构：骨架一致率、占位/示例残留数、表格/图片就位率
  - 样式：指纹 diff 条数
  - 内容：judge 通过率、引用支持率、人工抽检分
  - 工程：每篇 LLM 调用次数、token、成本、时长、修复轮数
- **门槛**：任何改动 prompt / 模型 / 判断阈值的 PR 必须附评测对比。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| Jev 对中文精度不足 / 服务可用性（早期访问、国内连通） | 判断层三级回退；阈值按语言分别标定；可走 OpenRouter |
| 模板五花八门，`TemplateSpec` 覆盖不全 | `other` 角色 + 人工确认；解析器只承诺"不丢块"，语义靠 judge + 人 |
| 结构化输出在弱模型上不稳定 | schema 校验失败重试并附错误；draft 粒度限制到单槽位 |
| LangGraph 版本演进 | 只依赖 StateGraph / checkpointer / Send / interrupt 四个稳定接口，节点逻辑与框架解耦 |
| 迁移期间双管线维护成本 | feature flag + 按 work 灰度；Phase 2 完成后设定 legacy 下线日期 |

## 8. 决策记录（ADR 摘要）

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| 1 | 用显式状态图而非 ReAct agent 作为主循环 | 继续 `create_agent` + 更强 prompt | 可验证、可恢复、失败局部化；符合 12-Factor #8 |
| 2 | 引入 `PaperIR` 作为唯一事实源 | 继续直接操作 docx/md | diff/版本/局部重写/多格式的共同前提 |
| 3 | `TemplateSpec` 在上传阶段产出并允许人工确认 | 每次写作实时解析 | 模板复用率高，一次确认摊薄成本 |
| 4 | 判断层独立且带置信度，Jev 优先 | 全部由 LLM 判 | 毫秒级、便宜、可校准；不足处有回退 |
| 5 | 单一 docx 渲染引擎（python-docx 在模板副本上） | 保留 docx-js / OOXML 三条路径 | 样式决策集中；OOXML 工具降为校验与底层 |
| 6 | 事件协议采用 AG-UI 风格 | 继续自定义 `json_block` | 有现成前端消费模式，类型清晰 |
| 7 | Markdown 与 Word 共用流程 | 保持两条路径 | 减少一半的 prompt 与工具面 |

## 附录 A：现有模块 → 目标模块

| 现有 | 目标 |
|---|---|
| `core_agents/main_agent.py` | `graph/build.py` + `nodes/plan.py` |
| `core_agents/writer_agent.py` | `nodes/draft.py`（无工具） |
| `core_agents/code_agent.py` + `core_tools/code_executor.py` | `nodes/gather.py` + `sandbox/` |
| `core_agents/review_agent.py` | `nodes/judge.py` + `validate/` |
| `services/file_services/template_contract.py` | `template/spec_builder.py` |
| `services/file_services/plan_reconciler.py` | `schemas/plan.py` 的推导函数（删除启发式） |
| `core_tools/docx_tools.py`（写入部分） | `render/docx_renderer.py` |
| `core_tools/docx_styles.py`、`docx_images.py` | `validate/fingerprint.py`、`template/ooxml_parser.py`（保留） |
| `docx_skill/scripts/office/*` | `render/ooxml/`（校验、soffice） |
| `core_managers/stream_manager.py` | `schemas/events.py` + 事件发射器 |
| `core_managers/langchain_tools.py` | 删除（节点直接调用函数） |
| `core_agents/agent_base.py`、`core_managers/context_manager.py` | 删除 |

## 附录 B：判断层问题模板示例

```python
# template/role_labeler.py —— 上传阶段，一次请求覆盖全部块
questions = {}
for b in blocks:
    questions[f"{b.id}.role"] = Choice(
        instructions=f"`blocks` 中 id 为 {b.id} 的段落在这份模板里扮演什么角色？",
        criteria={
            "heading": "章节标题：短句，通常带编号或大字号/加粗，成稿后必须保留",
            "fixed_text": "模板固定文字（封面、学号姓名栏、签字栏）：原样保留",
            "placeholder_fill": "要求作者在此处填写内容的提示语，成稿时其后要有正文",
            "example_delete": "示例内容（示例代码、示例表格行、示例文字），成稿时必须替换或删除",
            "instruction_delete": "写给作者的格式/写作说明，成稿后应整段删除",
            "caption": "图题或表题",
            "other": "以上都不是",
        },
    )
    questions[f"{b.id}.format_rule"] = Noul(
        instructions=f"id 为 {b.id} 的段落是否陈述了字体、字号、行距、缩进、对齐或页边距要求？")

# nodes/judge.py —— 单个槽位的 Draft 评审
questions = {
    "substantive": Noul(instructions="`draft.blocks` 是否包含针对 `slot.title` 的实质性内容，而非空话或复述标题？"),
    "example_left": Noul(instructions="`draft.blocks` 中是否残留了 `slot.examples` 里的示例文字？"),
    "follows_rules": Score(instructions="`draft.blocks` 对 `slot.constraints` 的遵守程度",
                           criteria=["明显违反", "部分遵守", "完全遵守"]),
}
```

## 附录 C：事件类型（首期子集）

| 事件 | 载荷 | 前端用途 |
|---|---|---|
| `RUN_STARTED` / `RUN_FINISHED` / `RUN_ERROR` | `run_id, thread_id` | 会话状态 |
| `STEP_STARTED` / `STEP_FINISHED` | `node, slot_id?` | 进度条、计划面板高亮 |
| `TEXT_MESSAGE_START/CONTENT/END` | `message_id, delta` | 聊天流式文本（draft/answer） |
| `STATE_DELTA` | JSON Patch（`/plan`、`/paper/sections/<slot>`） | 计划与预览增量刷新 |
| `CUSTOM:validation_issue` | `ValidationIssue` | 问题列表 |
| `CUSTOM:render_done` | `{format, path, revision}` | 精确重载预览 |
| `CUSTOM:awaiting_confirmation` | 待确认项 | 人工确认 UI |
