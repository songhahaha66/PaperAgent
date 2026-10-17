import asyncio
import json
import re
import sys
from pathlib import Path
from typing import List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph import GraphContext, GroundingError, PaperGraphAgent, ResultRegistry, compile_graph
from ai_system.graph.nodes.confirm import is_approval
from ai_system.graph.nodes.review import ungrounded_findings
from ai_system.graph.nodes.validate import assemble, place_figures, untracked_numbers
from ai_system.graph.registry import value_in_output
from ai_system.graph.state import (
    CandidateSet,
    ClaimAudit,
    ClaimIssue,
    Conflict,
    ConsistencyReview,
    MethodPick,
    ModelCandidate,
    ProblemAnalysis,
    ResultReview,
    Subproblem,
    ValidationProtocol,
)
from ai_system.research import ENGINE_VERSION
from ai_system.research.models import Requirement, Assumption, IssueReport
from ai_system.research.planner import AmendmentSpec
from ai_system.research.store import ResearchStore
from ai_system.judge import Answers, ScoreAnswer
from services.file_services.workspace_fs import WorkspaceFS

TASK = "某城市给出 2015-2024 年用电量数据。问题一：建立模型预测 2025 年用电量。问题二：在问题一基础上评估节能政策效果。"


# ----------------------------------------------------------------------
# 假模型：按调用内容返回固定输出，覆盖结构化输出、工具调用循环和纯文本写作
# ----------------------------------------------------------------------


def _analysis() -> ProblemAnalysis:
    return ProblemAnalysis(
        title="基于时间序列的城市用电量预测与政策评估",
        restatement="重述",
        overall_analysis="先预测再评估",
        subproblems=[
            Subproblem(id="Q1", title="用电量预测", question="预测 2025 年用电量", deliverables=["预测值"]),
            Subproblem(id="Q2", title="政策评估", question="评估节能政策", depends_on=["Q1"]),
            Subproblem(id="Q3", title="致市长的建议信", question="给市长写一封建议信", kind="writing", depends_on=["Q1", "Q2"]),
        ],
        requirements=[Requirement(id="RQ1", statement="预测用电量", source_quote="预测 2025 年用电量", kind="goal", task_ids=["Q1"]),
                      Requirement(id="RQ2", statement="评估政策效果", source_quote="评估节能政策效果", kind="goal", task_ids=["Q2"]),
                      Requirement(id="RQ3", statement="形成政策建议", source_quote="评估节能政策效果", kind="deliverable", task_ids=["Q3"])],
        assumptions=[Assumption(id="A1", statement="数据真实可靠", task_ids=["Q1", "Q2"])],
    )


def _candidate(name: str) -> ModelCandidate:
    return ModelCandidate(
        name=name, method_keys=["arima", "regression"], idea="思路", formulation="$y_t = a y_{t-1}$",
        steps=["读数据", "拟合"], assumptions=[], validation="残差检验",
    )


class _Structured:
    reviewed_q1 = False
    audits = 0

    def __init__(self, schema):
        self.schema = schema

    async def ainvoke(self, messages):
        if self.schema is ProblemAnalysis:
            analysis = _analysis()
            source = messages[-1].content.split("# 题目\n", 1)[1].split("\n\n# 数据概览", 1)[0]
            for requirement in analysis.requirements:
                if requirement.source_quote not in source:
                    requirement.source_quote = source
            return analysis
        if self.schema is AmendmentSpec:
            data = json.loads(messages[-1].content)
            number = max(int(r[2:]) for r in data["used_requirement_ids"]) + 1
            return AmendmentSpec(requirements=[Requirement(id=f"RQ{number}", statement=data["change"], source_quote=data["change"],
                kind="goal", task_ids=data["scope"])])
        if self.schema is MethodPick:
            return MethodPick(method_keys=["arima", "not_a_method", "regression"], reason="r")
        if self.schema is CandidateSet:
            CANDIDATE_PROMPTS.append(messages[-1].content)
            if any(f"子问题\n{sid} " in messages[-1].content for sid in NO_CANDIDATES):
                # 真实解析器对不合 schema 的输出会抛校验错误
                return CandidateSet.model_validate({"candidates": []})
            return CandidateSet(candidates=[_candidate("方案甲"), _candidate("方案乙")])
        if self.schema is ValidationProtocol:
            return ValidationProtocol(applicable=True, metric="留出集 AUC", procedure="按年份留出", baseline="常数预测")
        if self.schema is ConsistencyReview:
            # 编号写错的冲突（C99）应被过滤
            return ConsistencyReview(conflicts=[
                Conflict(claims=["C1", "C99"], issue="Q1 结论强度与 Q2 不符", resolution="统一表述为倾向性结论"),
                Conflict(claims=["C99"], issue="不存在", resolution="x"),
            ])
        if self.schema is ClaimAudit:
            _Structured.audits += 1
            if _Structured.audits == 1:
                return ClaimAudit(issues=[
                    ClaimIssue(section="evaluation", problem="把 C1 写成了显著"),
                    ClaimIssue(section="not_a_section", problem="x"),
                    # 核对后一致的句子不是问题，不应触发修复
                    ClaimIssue(section="evaluation", problem="正文称 AUC 为 0.79，与 C1 一致。无矛盾。"),
                    ClaimIssue(section="evaluation", contradicts=False, problem="没有提到 C1"),
                ])
            return ClaimAudit()
        if self.schema is ResultReview:
            # Q1 第一次审查要求返工，随后重新审查通过；其余直接通过
            prompt = messages[-1].content
            if "子问题 Q1" in prompt and not _Structured.reviewed_q1:
                _Structured.reviewed_q1 = True
                return ResultReview(issues=[IssueReport(task_id="Q1", category="data", message="改用全部比赛重新估计")])
            return ResultReview()
        raise AssertionError(self.schema)


# 模拟网关故障：这些子问题在登记第一个结果后失败一次
FAIL_ONCE: set = set()
# 这些子问题的候选方案总是空的
NO_CANDIDATES: set = set()
# 生成候选方案时的请求，用来检查按需设计带上了前序结论
CANDIDATE_PROMPTS: list = []


class FakeModel(BaseChatModel):
    loose_number_once: bool = True
    text_calls: List[str] = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return _Structured(schema)

    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._respond(messages))])

    def _respond(self, messages: List[BaseMessage]) -> AIMessage:
        first = next(m for m in messages if isinstance(m, HumanMessage)).content
        if "JSON Schema" in messages[-1].content:
            # 结构化调用失败后的纯文本 JSON 重试：假模型同样给不出合格结果
            return AIMessage(content="无法给出")
        if "开始求解" in first:
            return self._solver_step(first, messages)
        if "开始探索" in first:
            promoted = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and "正式求解" in m.content]
            if promoted:
                return self._solver_step(first, messages[promoted[-1]:])
            return self._probe_step(first, messages)
        return AIMessage(content=self._write(first))

    def _solver_step(self, task: str, messages: List[BaseMessage]) -> AIMessage:
        sid = re.search(r"# 子问题 (Q\d)", task).group(1)
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        last = tool_results[-1].content if tool_results else ""
        found = re.search(r"\[run_id: (\S+)\]", last)
        run_id = found.group(1) if found else ""
        step = len(tool_results)
        if step == 2 and sid in FAIL_ONCE:
            FAIL_ONCE.discard(sid)
            raise RuntimeError("403 real_name_required")
        value = "1234.57" if sid == "Q1" else "8.5%"
        script = [
            ("run_python", {"code": "forecast = 1234.5678\nprint('forecast', forecast)\nprint('saving', 0.085)"}),
            ("record_results", {"items": [
                {"name": "预测值" if sid == "Q1" else "节电比例", "value": value, "run_id": run_id, "unit": "万千瓦时" if sid == "Q1" else ""},
                {"name": "编造值", "value": "99.99", "run_id": run_id},
            ]}),
            ("run_python", {"code": "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])"}),
            ("record_figure", {"run_id": run_id, "figure": "figure_1.png", "caption": f"{sid} 结果图"}),
            ("SolveReport", {
                "summary": "完成，预测为 {{R:R1}}", "approach": "ARIMA", "key_findings": [{"statement": "结论 {{R:R1}}", "strength": "显著"}],
                "sensitivity": "稳定", "limitations": ["样本少"],
            }),
        ]
        name, args = script[min(step, len(script) - 1)]
        if name == "SolveReport":
            source = task.split("# 研究契约", 1)[-1]
            if "# 研究契约" in task:
                contract, _ = json.JSONDecoder().raw_decode(source[source.index("{"):])
                refs = [ref for message in tool_results for ref in re.findall(r"\{\{R:(R[0-9]+)\}\}", str(message.content))]
                args["requirement_evidence"] = [{"requirement_id": r["id"], "evidence_refs": [refs[-1]]}
                                                for r in contract["requirements"]] if refs else []
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{sid}_{step}"}])

    def _probe_step(self, task: str, messages: List[BaseMessage]) -> AIMessage:
        # 方案甲真实 AUC 0.71；方案乙打印 0.65 却报告 0.99（编造），核对后不参与比较
        sid = re.search(r"# 子问题 (Q\d)", task).group(1)
        name = re.search(r"# 选定的建模方案：(\S+)", task).group(1)
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        if not tool_results:
            auc = "0.71" if name == "方案甲" else "0.65"
            code = f"model = '{name}'\nprint('AUC', {auc})\nprint('baseline', 0.5)\nprint('shuffled', 0.51)"
            return AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": code}, "id": f"probe_{sid}_{name}_0"}])
        run_id = re.search(r"\[run_id: (\S+)\]", tool_results[-1].content).group(1)
        report = {
            "works": True, "metric_value": "0.71" if name == "方案甲" else "0.99", "metric_run_id": run_id,
            "baseline_value": "0.5", "shuffled_value": "0.51", "summary": f"{name} 跑通", "problems": [],
        }
        return AIMessage(content="", tool_calls=[{"name": "ProbeReport", "args": report, "id": f"probe_{sid}_{name}_1"}])

    def _write(self, request: str) -> str:
        self.text_calls.append(request)
        refs = sorted(set(re.findall(r"\{\{[RF]:[RF]\d+\}\}", request.split("# 当前稿")[0])))
        numbers = [r for r in refs if r.startswith("{{R")]
        figures = [r for r in refs if r.startswith("{{F")]
        body = "本节依据登记结果展开论述，" + "、".join(numbers) + "。" + "模型推导过程详见正文，结论稳健可靠。" * 4
        if figures and "求解小节" in request:
            body += "\n\n" + "\n\n".join(figures)
        if "撰写「摘要」" in request and "# 当前稿" not in request and self.loose_number_once:
            body += " 另外误差约为 12.345。"
        return body


class FixedJudge:
    name = "fixed"

    async def ask(self, state, questions):
        # 第二个候选（方案2）分数更高；每次只评一个候选
        return Answers(
            scores={k: ScoreAnswer(score=3.0 if "「方案2」" in q.instructions else 1.0, probabilities={}, confidence=None)
                    for k, q in questions.items()},
            judge="fixed",
        )


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    (ws / "data" / "power.csv").write_text("year,power\n2023,1100.5\n2024,1180.2\n", encoding="utf-8")
    return ws


def _context(ws: Path, model: FakeModel, **options) -> GraphContext:
    return GraphContext.create(str(ws), planner_llm=model, judge=FixedJudge(), **options)


# ----------------------------------------------------------------------
# 端到端（全部离线）
# ----------------------------------------------------------------------


def test_graph_end_to_end_offline(tmp_path):
    _Structured.reviewed_q1 = False
    _Structured.audits = 0
    FakeModel.text_calls = []
    ws = _make_workspace(tmp_path)
    model = FakeModel()
    # 评审分差 8 分，默认会跳过试跑；放宽阈值以覆盖探索流程
    ctx = _context(ws, model, explore_margin=100)
    ctx.research = ResearchStore(ws, "t")

    async def go():
        graph = compile_graph(InMemorySaver())
        try:
            return await graph.ainvoke(
                {"task": TASK}, {"configurable": {"thread_id": "t"}, "recursion_limit": 100}, context=ctx
            )
        finally:
            await ctx.kernels.close()

    result = asyncio.run(go())

    # 数据概览由固定代码生成
    assert "data/power.csv" in result["data_profile"] and "2 行 x 2 列" in result["data_profile"]
    # 方法检索过滤掉不存在的 key；评审选了分数更高的第二个候选
    design = result["designs"]["Q1"]
    assert design["methods"] == ["arima", "regression"]
    assert design["scores"][1]["total"] == 12.0
    # 评审选了分数更高的方案乙，但探索时按统一协议实测，方案甲胜出；方案乙编造的指标没通过核对
    exploration = design["exploration"]
    assert [p["name"] for p in exploration["probes"]] == ["方案乙", "方案甲"]
    assert [p["metric_grounded"] for p in exploration["probes"]] == [False, True]
    assert exploration["chosen_index"] == 0 and design["chosen"]["name"] == "方案甲"
    assert "留出集 AUC" in exploration["reason"]

    # 审查要求 Q1 返工一轮后通过；Q2 一次通过
    assert [bool(r["issues"]) for r in result["solutions"]["Q1"]["reviews"]] == [True, False]
    assert not result["solutions"]["Q2"]["reviews"][-1]["issues"] and result["solutions"]["Q2"]["summary"] == "完成，预测为 {{R:R1}}"

    # 两个子问题都求解完成；编造值登记被拒绝，只登记了真实结果和图
    assert {k: v["status"] for k, v in result["solutions"].items()} == {"Q1": "completed", "Q2": "completed", "Q3": "completed"}
    # 写作类子问题不设计方案、不跑编码循环，写作时依据前序结论和登记结果
    assert result["designs"]["Q3"]["chosen"]["name"] == "基于前序结论撰写"
    assert result["solutions"]["Q3"]["approach"] == "基于前序结论撰写"
    q3_request = next(r for r in model.text_calls if "Q3 致市长的建议信（文书类交付物）" in r and "撰写「Q3" in r)
    assert "{{R:R1}}" in q3_request
    registry = ResultRegistry.load(WorkspaceFS(str(ws)))
    kinds = sorted((r.subproblem, r.kind) for r in registry.results.values())
    assert kinds == [("Q1", "figure"), ("Q1", "number"), ("Q2", "figure"), ("Q2", "number")]
    assert all(r.value != "99.99" for r in registry.results.values())
    assert (ws / registry.get("F1").path).exists()

    # 结论图：每个子问题的结论登记为节点，证据指向登记结果
    claims = sorted((c.subproblem, c.strength, tuple(c.evidence)) for c in registry.claims.values())
    assert claims == [("Q1", "显著", ("R1",)), ("Q2", "显著", ("R1",))]
    # 写作前的一致性审查：指向不存在结论的冲突被过滤，统一口径带进每一节的写作请求
    assert [c["claims"] for c in result["consistency"]] == [["C1"]]
    assert all("全文结论表" in req and "统一表述为倾向性结论" in req for req in model.text_calls)

    # 摘要第一次带了编造数字、模型评价与结论表不一致，校验后同一轮修复
    assert any("与结论表不一致：把 C1 写成了显著" in req for req in model.text_calls)
    assert not any("无矛盾" in req or "没有提到 C1" in req for req in model.text_calls)
    assert result["repair_round"] == 1
    assert result["issues"] == []

    paper = (ws / "paper.md").read_text(encoding="utf-8")
    assert paper.startswith("# 基于时间序列的城市用电量预测与政策评估")
    assert "1234.57 万千瓦时" in paper and "8.5%" in paper
    assert f"![图1 Q1 结果图]({registry.get('F1').path})" in paper
    assert "{{" not in paper and "12.345" not in paper
    for heading in ("## 摘要", "## 三、模型假设", "### Q2 政策评估", "## 七、模型的评价与推广"):
        assert heading in paper
    assert "{{R:R1}}" in (ws / ".system" / "paper_raw.md").read_text(encoding="utf-8")

    # 按需设计（默认不提前设计）：Q2 设计时 Q1 已经求解完，候选方案的请求里带着 Q1 的结论
    q2_design = next(p for p in CANDIDATE_PROMPTS if "# 子问题\nQ2 " in p)
    assert "前序子问题的结论" in q2_design and "## Q1" in q2_design
    # 依赖：Q2 的求解任务里带上了 Q1 的结论
    plan = json.loads((ws / "plan.json").read_text(encoding="utf-8"))
    assert plan["stats"]["completed"] == plan["stats"]["total"]
    manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert sum(r["status"] == "success" for r in manifest["runs"].values()) >= 5


def test_confirm_interrupt_and_feedback(tmp_path):
    ws = _make_workspace(tmp_path)
    model = FakeModel()
    ctx = _context(ws, model, confirm_models=True)
    ctx.research = ResearchStore(ws, "t")
    config = {"configurable": {"thread_id": "t"}, "recursion_limit": 100}

    async def go():
        graph = compile_graph(InMemorySaver())
        try:
            first = await graph.ainvoke({"task": TASK}, config, context=ctx)
            second = await graph.ainvoke(Command(resume="问题二请用双重差分"), config, context=ctx)
            third = await graph.ainvoke(Command(resume="确认"), config, context=ctx)
            return first, second, third
        finally:
            await ctx.kernels.close()

    first, second, third = asyncio.run(go())
    assert first["__interrupt__"][0].value["type"] == "confirm_models"
    assert "方案乙" in first["__interrupt__"][0].value["message"]
    # 修改意见触发重新设计，再次等待确认
    assert second["feedback"] == "问题二请用双重差分" and second["design_round"] == 1
    assert "__interrupt__" in second
    assert third["approved"] is True
    assert (ws / "paper.md").exists()


def test_paper_graph_agent_runs_and_persists_checkpoint(tmp_path):
    ws = _make_workspace(tmp_path)
    events = []
    phases = []

    class Recorder:
        async def send_json_block(self, block_type, content):
            events.append(block_type)
            if block_type == "paper_phase":
                phases.append((content["stage"], content["status"]))

    agent = PaperGraphAgent(FakeModel(), stream_manager=Recorder(), workspace_dir=str(ws), judge=FixedJudge())
    output = asyncio.run(agent.run(TASK))

    assert "paper.md" in output and "Q1 用电量预测：完成" in output
    assert (ws / ".system" / "graph.sqlite").exists()
    assert json.loads((ws / ".system" / "graph_thread.json").read_text())["status"] == "done"
    assert events[0] == "main_agent_start" and events[-1] == "main_agent_complete"
    assert "plan_updated" in events and "file_changed" in events
    # 前端阶段卡片依赖的结构化阶段事件
    assert phases[0] == ("analyze", "in_progress") and ("render", "completed") in phases
    # markdown 输出不排版
    assert not any(stage == "export" for stage, _ in phases) and not (ws / "paper.tex").exists()
    # 逐次模型调用的耗时记录：外层节点名和子问题/章节标签
    calls = json.loads((ws / ".system" / "llm_calls.json").read_text(encoding="utf-8"))["calls"]
    tagged = {(c["node"], c["label"]) for c in calls}
    assert {("solve", "Q1"), ("solve", "Q2"), ("write", "abstract"), ("repair", "repair:abstract")} <= tagged

    # 同一工作空间开始新任务时，登记表从头编号，不混入上一篇的结果
    first_thread = json.loads((ws / ".system" / "graph_thread.json").read_text())["thread_id"]
    asyncio.run(agent.run(TASK + "（第二篇）"))
    assert json.loads((ws / ".system" / "graph_thread.json").read_text())["thread_id"] != first_thread
    registry = ResultRegistry.load(WorkspaceFS(str(ws)))
    assert sorted(registry.results) == ["F1", "F2", "R1", "R2"]


# ----------------------------------------------------------------------
# 单元
# ----------------------------------------------------------------------


@pytest.mark.parametrize("value,output,expected", [
    ("0.873", "acc=0.87312", True),
    ("87.3%", "acc=0.87312", True),
    ("87.31%", "acc 87.312", True),
    ("1,234.6", "total 1234.5678", True),
    ("3", "value 2.6", False),
    ("3", "count 3", True),
    ("0.9", "acc=0.87312", False),
    ("1.2e-05", "p=0.000012", True),
    ("A→B→C", "best path A→B→C", True),
])
def test_value_in_output(value, output, expected):
    assert value_in_output(value, output) is expected


def test_registry_rejects_failed_or_unknown_runs(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "x 1.5")
    fs.finish_run(run_id, "error")
    registry = ResultRegistry.load(fs)
    with pytest.raises(GroundingError):
        registry.record_value("Q1", "x", "1.5", run_id)
    with pytest.raises(GroundingError):
        registry.record_value("Q1", "x", "1.5", "run_missing")


def test_registry_rejects_composite_output_lines(tmp_path):
    """整行输出登记成一条结果，正文引用时会把原始输出贴进论文"""
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "ATTR word_length coef -0.0377 p 0 valAUC 0.500\nlag7 p=0.3261 lag14 p=0.8776\n"
                               "order (0,1,1)(1,0,1)_7 range 2022-01-07..2022-10-20")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    for value in ("ATTR word_length coef -0.0377 p 0 valAUC 0.500", "lag7 p=0.3261 lag14 p=0.8776"):
        with pytest.raises(GroundingError, match="单独登记"):
            registry.record_value("Q1", "x", value, run_id)
    assert registry.record_value("Q1", "p", "0.3261", run_id).kind == "number"
    assert registry.record_value("Q1", "order", "(0,1,1)(1,0,1)_7", run_id).kind == "text"
    assert registry.record_value("Q1", "range", "2022-01-07..2022-10-20", run_id).kind == "text"


def test_registry_reuses_only_identical_records(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    first, second = fs.create_run(), fs.create_run()
    fs.save_run_stdout(first, "auc 0.71")
    fs.finish_run(first, "success")
    fs.save_run_stdout(second, "auc 0.66")
    fs.finish_run(second, "success")
    registry = ResultRegistry.load(fs)
    a = registry.record_value("Q1", "AUC", "0.71", first)
    b = registry.record_value("Q1", "AUC", "0.71", first)
    c = registry.record_value("Q1", "AUC", "0.66", second)
    other = registry.record_value("Q2", "AUC", "0.66", second)
    assert a.id == b.id == "R1" and c.id == "R2" and other.id == "R3"
    assert registry.get("R1") is None and registry.get("R2").value == "0.66"
    assert registry.staged_history[0].value == "0.71" and len(registry.results) == 2
    assert ResultRegistry.load(fs).staged_history[0] == a


def test_registry_render_numbers_figures_and_unknown_refs(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "rate 0.5")
    (fs.run_dir(run_id) / "artifacts" / "figure_1.png").write_bytes(b"png")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    rate = registry.record_value("Q1", "比例", "50%", run_id)
    fig = registry.record_figure("Q1", run_id, "runs/x/artifacts/figure_1.png", "趋势")

    text = f"比例为 {{{{R:{rate.id}}}}}，见 {{{{F:{fig.id}}}}}。\n\n{{{{F:{fig.id}}}}}\n\n未知 {{{{R:R99}}}}"
    rendered = registry.render(text)
    assert "比例为 50%，见 图1。" in rendered
    assert "![图1 趋势](outputs/figures/F1.png)\n\n*图1 趋势*" in rendered
    assert "{{R:R99}}" in rendered
    # 图名自带的编号去掉；同一张图第二次独占一行只写"见图"；编号按图片出现顺序，不按摘要里的行内提及
    second = registry.record_figure("Q1", run_id, "runs/x/artifacts/figure_1.png", "图3 敏感性")
    ref = f"{{{{F:{second.id}}}}}"
    rendered = registry.render(f"摘要见 {ref}。\n\n{{{{F:{fig.id}}}}}\n\n{ref}\n\n{ref}")
    assert "摘要见 图2。" in rendered and "*图2 敏感性*" in rendered and "图2 图3" not in rendered
    assert rendered.count("![图2") == 1 and rendered.rstrip().endswith("*图2 敏感性*")
    # 重新加载后编号连续
    assert ResultRegistry.load(fs).get("R1").value == "50%"


def test_untracked_numbers_ignores_refs_formulas_and_allowed():
    text = "预测值 {{R:R1}}，公式 $y = 0.5x$，题目给定 0.65，另有 3.14 与 20%。整数 2025 不检查。"
    assert untracked_numbers(text, allowed_text="给定 0.65") == ["3.14", "20%"]
    assert untracked_numbers("### 4.1 整体思路\n#### Q1.2 求解\n正文 4.5", "") == ["4.5"]
    # 公式里单独等于一个数的是结果（Q2 把没登记的检验统计量写成 $z = 4.05$）
    assert untracked_numbers("真实序列 $z = 4.05$，$\\hat p \\approx 0.673$，$x = {{R:R1}}$", "") == ["4.05", "0.673"]
    assert untracked_numbers(r"降幅 $23.1\%$，近似 $r \approx 24.2\%$，比例 $0.231$，整数比例 $23\%$", "") == ["23.1%", "24.2%", "0.231", "23%"]
    assert untracked_numbers(r"已知 $23.1\%$，真实值 ${{R:R1}}$，系数 $y=0.5x$", "23.1%") == []
    assert untracked_numbers("```python\n# $3.14$\n``` 与 `$2.34$`", "") == []


def test_ungrounded_findings_flags_numbers_outside_refs():
    report = {
        "summary": "Ljung-Box 检验 {{R:R2}}，验证集 6 场",
        "key_findings": [{"statement": "真实序列 z=4.05，置换后 9.18，换挡率约 5%", "strength": "不显著"}],
    }
    issues = ungrounded_findings(report)
    assert len(issues) == 1 and "4.05, 9.18" in issues[0] and "5%" not in issues[0]
    report["key_findings"][0]["statement"] = "真实序列 {{R:R3}}，置换后 {{R:R4}}"
    assert ungrounded_findings(report) == []


def test_place_figures_inserts_inline_only_figures_after_paragraph():
    text = "见 {{F:F1}} 与 {{F:F2}}。\n续行\n\n{{F:F2}}\n\n末段见{{F:F3}}"
    assert place_figures(text) == "见 {{F:F1}} 与 {{F:F2}}。\n续行\n\n{{F:F1}}\n\n{{F:F2}}\n\n末段见{{F:F3}}\n\n{{F:F3}}"
    # 已经单独成行的不重复插入
    assert place_figures("{{F:F1}}\n\n见 {{F:F1}}") == "{{F:F1}}\n\n见 {{F:F1}}"


def test_assemble_keeps_figures_out_of_summary_sections():
    from ai_system.graph.nodes.write import solve_key

    state = {
        "analysis": {"title": "T", "subproblems": [{"id": "Q1", "title": "建模"}]},
        "sections": {"abstract": "结论见下图\n\n{{F:F1}}", solve_key("Q1"): "曲线见 {{F:F1}}。"},
    }
    raw = assemble(state)
    abstract = raw.split("## 摘要", 1)[1].split("##", 1)[0]
    assert "（见{{F:F1}}）" in abstract and "\n{{F:F1}}\n" not in abstract
    assert "曲线见 {{F:F1}}。\n\n{{F:F1}}" in raw


def test_hypothesis_test_subproblems_skip_exploration():
    from ai_system.graph.nodes.explore import is_hypothesis_test

    assert is_hypothesis_test({"title": "检验势头是否随机(教练假说检验)", "question": "检验比赛中的波动是否随机"})
    assert not is_hypothesis_test({"title": "换挡点预测", "question": "预测转折并检验泛化性"})
    assert not is_hypothesis_test({"title": "政策评估", "question": "评估节能政策"})


def test_is_approval():
    assert is_approval("确认") and is_approval("OK!") and is_approval("")
    assert not is_approval("问题二换成双重差分")


def test_render_units_math_and_duplicates(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "p 0.687 excess -6.26 z 1.2908")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    rate = registry.record_value("Q1", "胜率", "68.7%", run_id, unit="%")
    excess = registry.record_value("Q1", "超额", "-6.26", run_id, unit="点")
    z = registry.record_value("Q1", "z", "1.2908", run_id, unit="z分数")

    text = f"胜率 {{{{R:{rate.id}}}}}，超额 {{{{R:{excess.id}}}}} 点，统计量 $z={{{{R:{z.id}}}}}$。"
    assert registry.render(text) == "胜率 68.7%，超额 -6.26 点，统计量 $z=1.2908$。"


def test_demote_headings_nests_section_body():
    from ai_system.graph.nodes.validate import demote_headings

    assert demote_headings("### 模型的建立\n正文\n#### 细节", 3) == "#### 模型的建立\n正文\n##### 细节"
    assert demote_headings("### 背景\n正文", 2) == "### 背景\n正文"


def test_compact_messages_keeps_recent_steps_and_stable_prefix():
    from ai_system.graph.compaction import compact_messages, compaction_boundary

    def history(steps):
        messages = [HumanMessage(content="task")]
        for i in range(steps):
            messages.append(AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": f"# {i}\n" + "x" * 2000}, "id": f"c{i}"}]))
            messages.append(ToolMessage(content=f"out{i} " + "y" * 2000, tool_call_id=f"c{i}"))
        return messages

    # 边界按整块推进：块内不变，前缀稳定才能命中缓存
    assert [compaction_boundary(n, 6, 10) for n in (10, 16, 17, 25, 26, 36)] == [0, 0, 10, 10, 20, 30]

    assert compact_messages(history(16), keep_recent=6, block=10) == history(16)
    messages = history(20)
    compacted = compact_messages(messages, keep_recent=6, block=10)
    assert len(compacted) == len(messages) and compacted[0].content == "task"
    # 前 10 步被截短，之后原样保留
    assert len(compacted[1].tool_calls[0]["args"]["code"]) < 1000 and "已压缩" in compacted[20].content
    assert compacted[21] is messages[21] and compacted[-1] is messages[-1]
    assert compacted[1].tool_calls[0]["id"] == "c0"
    assert len(messages[1].tool_calls[0]["args"]["code"]) > 2000
    # 再走几步（同一块内），被压缩部分完全一致
    later = compact_messages(history(24), keep_recent=6, block=10)
    assert [m.content for m in later[:21]] == [m.content for m in compacted[:21]]


def test_select_probe_metric_direction_ties_and_fallbacks():
    from ai_system.graph.nodes.explore import Probe, select_probe, shortlist

    def probe(i, prior, works=True, metric=None):
        p = Probe(candidate_index=i, candidate={"name": f"方案{i}"}, kernel_key=f"Q1~c{i}", prior=prior)
        p.report = {"works": works, "metric_value": str(metric) if metric is not None else ""}
        p.run_log = [{"ok": works}]
        p.metric = metric
        return p

    rmse = {"applicable": True, "metric": "RMSE", "higher_is_better": False}
    # 越小越好
    assert select_probe([probe(0, 10, metric=3.2), probe(1, 8, metric=2.1)], rmse)[0].candidate_index == 1
    # 相差 1% 以内视为打平，用评审分打破平局
    assert select_probe([probe(0, 8, metric=2.000), probe(1, 10, metric=2.010)], rmse)[0].candidate_index == 1
    # 没跑通或指标没核对通过的均不能晋升到正式求解
    assert select_probe([probe(0, 12, works=False, metric=1.0), probe(1, 5, metric=9.0)], rmse)[0].candidate_index == 1
    assert select_probe([probe(0, 12), probe(1, 5)], rmse)[0] is None
    # 没有统一指标时直接正式求解，不用文字评分替代执行验收
    none = {"applicable": False}
    winner, reason = select_probe([probe(0, 12), probe(1, 5)], none)
    assert winner is None and "新内核" in reason
    # 都没跑通就沿用评审选择
    winner, reason = select_probe([probe(0, 5, works=False), probe(1, 9, works=False)], none)
    assert winner is None and "沿用" in reason

    design = {"candidates": [{}, {}, {}], "scores": [{"total": 5}, {"total": 9}, {"total": None}], "chosen_index": 1}
    assert shortlist(design, 2) == [1, 0]
    assert shortlist({"candidates": [{}], "chosen_index": 0}, 2) == [0]


def test_claims_replace_per_subproblem_and_render(tmp_path):
    from ai_system.graph.nodes.consistency import valid_conflicts

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "p 0.2320")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    p = registry.record_value("Q2", "游程检验p值", "0.2320", run_id)

    registry.set_claims("Q2", [{"statement": f"不能拒绝随机 {{{{R:{p.id}}}}}，见 {{{{R:R99}}}}", "strength": "不显著"}])
    registry.set_claims("Q3", [{"statement": "动量可预测", "strength": "显著"}, {"statement": " ", "strength": "显著"}])
    # 返工后重新提交，Q2 的旧结论被替换，编号继续递增
    registry.set_claims("Q2", [{"statement": f"逐分层面不能拒绝随机 {{{{R:{p.id}}}}}", "strength": "不显著"}])

    reloaded = ResultRegistry.load(fs)
    assert sorted(reloaded.claims) == ["C2", "C3"]
    assert reloaded.claims["C3"].evidence == [p.id] and reloaded.claims["C2"].evidence == []
    table = reloaded.claims_table()
    assert "| C3 | Q2 | 不显著 | 逐分层面不能拒绝随机 0.2320⟨R1⟩ | R1 |" in table
    assert valid_conflicts([{"claims": ["C2", "C9"], "resolution": "r"}, {"claims": ["C9"], "resolution": "r"}], set(reloaded.claims)) == [
        {"claims": ["C2"], "resolution": "r"}
    ]


def test_claims_ground_literal_numbers(tmp_path):
    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "r2 0.883 coef -0.0079 p 1.04e-164 cost 3.50 dup 0.5 0.5 n 15082")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    r2 = registry.record_value("Q1", "R2", "0.883", run_id)
    coef = registry.record_value("Q1", "系数", "-0.0079", run_id)
    p = registry.record_value("Q1", "p值", "1.04e-164", run_id)
    cost = registry.record_value("Q1", "成本", "3.50", run_id, unit="元")
    registry.record_value("Q1", "甲", "0.5", run_id)
    registry.record_value("Q1", "乙", "0.5", run_id)
    registry.record_value("Q1", "样本量", "15082", run_id)
    other = registry.record_value("Q2", "别的", "0.883", run_id)

    statement = registry.ground_statement(
        "Q1", "R²=0.883，系数 -0.0079（p=1.04e-164），成本 3.50 元，两组都是 0.5，样本 15082，$x=0.883$，0.8830，已有 {{R:%s}}" % other.id
    )
    assert statement == (
        f"R²={{{{R:{r2.id}}}}}，系数 {{{{R:{coef.id}}}}}（p={{{{R:{p.id}}}}}），成本 {{{{R:{cost.id}}}}}，"
        f"两组都是 0.5，样本 15082，$x=0.883$，0.8830，已有 {{{{R:{other.id}}}}}"
    )
    # "数（引用）"合并成一个引用；已引用过的结果不再重复替换
    assert registry.ground_statement("Q1", f"r=0.883（{{{{R:{r2.id}}}}}），再写 -0.0079 ({{{{R:{coef.id}}}}})，-0.0079") == (
        f"r={{{{R:{r2.id}}}}}，再写 {{{{R:{coef.id}}}}}，-0.0079"
    )
    claims = registry.set_claims("Q1", [{"statement": "R²=0.883", "strength": "显著"}])
    assert claims[0].evidence == [r2.id]


def test_call_structured_falls_back_to_plain_json():
    from types import SimpleNamespace

    from ai_system.graph.llm_utils import call_structured

    class Ignoring:
        async def ainvoke(self, messages):
            return None

    class LLM:
        def with_structured_output(self, schema, **kwargs):
            return Ignoring()

        async def ainvoke(self, messages):
            assert "JSON Schema" in messages[-1].content
            return SimpleNamespace(content='好的：\n```json\n{"method_keys": ["arima"], "reason": "时间序列"}\n```')

    pick = asyncio.run(call_structured(LLM(), MethodPick, [HumanMessage(content="挑方法")]))
    assert pick.method_keys == ["arima"]


def test_step_budget_nudges_near_limit_counting_since_last_human():
    from langchain.agents.middleware import ModelRequest

    from ai_system.graph.compaction import StepBudgetMiddleware

    def history(old_steps, new_steps):
        msgs = [HumanMessage(content="任务")]
        for i in range(old_steps):
            msgs += [AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "1"}, "id": f"a{i}"}]),
                     ToolMessage(content="ok", tool_call_id=f"a{i}")]
        msgs.append(HumanMessage(content="返工"))
        for i in range(new_steps):
            msgs += [AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "1"}, "id": f"b{i}"}]),
                     ToolMessage(content="ok", tool_call_id=f"b{i}")]
        return msgs

    seen = []

    async def handler(request):
        seen.append((request.messages, request.tools))
        return None

    middleware = StepBudgetMiddleware(budget=12, reserve=3, message="请交报告")
    for msgs in (history(20, 8), history(0, 9), history(0, 11)):
        asyncio.run(middleware.awrap_model_call(ModelRequest(model=None, messages=msgs, tools=["run_python"]), handler))
    # 返工前的 20 步不计入；第二次已到 9 步，追加提醒；最后一步只留报告工具
    assert seen[0][0][-1].content == "ok" and seen[0][1] == ["run_python"]
    assert "还剩约 3 步。请交报告" in seen[1][0][-1].content and seen[1][1] == ["run_python"]
    assert "步数已用完" in seen[2][0][-1].content and seen[2][1] == []
    from langgraph.errors import GraphRecursionError
    with pytest.raises(GraphRecursionError):
        asyncio.run(middleware.awrap_model_call(ModelRequest(model=None, messages=history(0, 12), tools=[]), handler))
    assert len(seen) == 3, "invalid final reports cannot keep calling the model past the step budget"


def test_design_falls_back_when_candidates_keep_failing(tmp_path, monkeypatch):
    from ai_system.graph import llm_utils

    monkeypatch.setattr(llm_utils, "RETRY_DELAY", 0)
    _Structured.reviewed_q1 = True
    ws = _make_workspace(tmp_path)
    NO_CANDIDATES.add("Q2")
    try:
        agent = PaperGraphAgent(FakeModel(), workspace_dir=str(ws), judge=FixedJudge())
        message = asyncio.run(agent.run(TASK))
    finally:
        NO_CANDIDATES.clear()
    # 一个子问题设计失败不应中止整篇，改由求解者自行设计
    assert "Q2 政策评估：完成" in message
    assert (ws / "paper.md").exists()


def test_normalize_plan_fills_writing_kind_and_dependencies():
    from ai_system.graph.nodes.analyze import normalize_plan

    plan = normalize_plan([
        {"id": "Q1", "kind": "compute", "title": "动量模型", "question": "建立模型", "depends_on": []},
        {"id": "Q2", "kind": "compute", "title": "随机性检验", "question": "用Q1建立的指标检验，不涉及 Q12", "depends_on": ["Q9"]},
        {"id": "Q3", "kind": "compute", "title": "给教练的备忘录", "question": "总结建议", "depends_on": []},
    ])
    assert [(sp["kind"], sp["depends_on"]) for sp in plan] == [
        ("compute", []), ("compute", ["Q1"]), ("writing", ["Q1", "Q2"]),
    ]


def test_model_candidate_rejects_empty_placeholder():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ModelCandidate(name="", method_keys=[], idea="", formulation="", steps=[], validation="")
    # 把 schema 当模板原样填回的占位方案
    with pytest.raises(pydantic.ValidationError):
        ModelCandidate(name="placeholder", method_keys=[], idea="placeholder", formulation="", steps=["x"], validation="")
    ModelCandidate(name="逻辑回归", method_keys=[], idea="用前序得分预测下一分", formulation="", steps=["拟合"], validation="")


def test_normalize_plan_drops_paper_writing_subproblem():
    from ai_system.graph.nodes.analyze import normalize_plan

    plan = normalize_plan([
        {"id": "Q1", "kind": "compute", "title": "动量模型", "question": "建立模型", "depends_on": []},
        {"id": "Q2", "kind": "writing", "title": "给教练的备忘录与报告整理", "question": "总结建议", "depends_on": []},
        {"id": "Q3", "kind": "writing", "title": "完整建模论文撰写", "question": "整合 Q1、Q2", "depends_on": ["Q1", "Q2"]},
    ])
    assert [sp["id"] for sp in plan] == ["Q1", "Q2"]


def test_normalize_plan_drops_duplicate_ids():
    from ai_system.graph.nodes.analyze import normalize_plan

    first = {"id": "Q1", "kind": "compute", "title": "动量模型", "question": "建立模型", "depends_on": []}
    second = {"id": "Q2", "kind": "compute", "title": "检验", "question": "用 Q1 的模型检验", "depends_on": []}
    plan = normalize_plan([first, second, {**first, "title": "重复的 Q1"}, second])
    assert [sp["id"] for sp in plan] == ["Q1", "Q2"]
    assert plan[0]["title"] == "动量模型" and plan[1]["depends_on"] == ["Q1"]


def test_report_issues_requires_registered_results(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.review import report_issues

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    ctx = SimpleNamespace(registry=ResultRegistry.load(fs))
    stub = {"summary": "读入了全部比赛数据并完成了特征构造", "key_findings": [{"statement": "数据读入完成", "strength": "倾向"}]}
    issues = report_issues(ctx, stub, "Q1")
    assert len(issues) == 1 and "还没有登记任何结果" in issues[0]
    # 删掉结论来回避检查
    assert any("概要或主要结论为空" in i for i in report_issues(ctx, {"summary": "test", "key_findings": []}, "Q1"))
    # 要求可视化却没登记图
    issues = report_issues(ctx, stub, "Q1", figure_required=True)
    assert any("还没有登记任何图" in i for i in issues)

    # Q3 没登记结果，却引用了 Q1 登记的 R1 冒充自己的指标
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "test matches 6")
    fs.finish_run(run_id, "success")
    ctx.registry.record_value("Q1", "验证集场数", "6", run_id)
    stolen = {"summary": "AUC 为 {{R:R1}}", "key_findings": []}
    assert any("R1 不是本子问题登记的结果" in i for i in report_issues(ctx, stolen, "Q3"))
    assert not any("不是本子问题" in i for i in report_issues(ctx, stolen, "Q3", ["Q1"]))


def test_record_results_recovers_mistyped_run_id(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.solve import build_tools

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "permutation p = 0.0123")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    ctx = SimpleNamespace(registry=registry, kernels=SimpleNamespace(get=lambda key: None))
    run_log = [{"run_id": run_id, "code": "", "ok": True}]
    record = {t.name: t for t in build_tools(ctx, "Q2", run_log)}["record_results"]
    reply = asyncio.run(record.coroutine([{"name": "p值", "value": "0.0123", "run_id": run_id[:-3] + "zzz"}]))
    assert "{{R:R1}}" in reply and registry.get("R1").run_id == run_id
    # 最近的执行里也没有这个值：照样拒绝
    reply = asyncio.run(record.coroutine([{"name": "编造", "value": "0.9876", "run_id": "run_x"}]))
    assert "登记失败" in reply and len(registry.results) == 1


def test_label_shuffle_control_flags_leakage(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.review import baseline_gaps, control_issues

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "LogLoss 0.4934 shuffled 0.5010 baseline 0.6778\nAUC 0.71 shuffled_auc 0.502 serve_auc 0.6")
    fs.finish_run(run_id, "success")
    ctx = SimpleNamespace(registry=ResultRegistry.load(fs))

    def report(**control):
        base = {"metric": "LogLoss", "run_id": run_id, "shuffled_run_id": run_id, "higher_is_better": False}
        return {"summary": "LogLoss 优于基线", "key_findings": [], "controls": [{**base, **control}]}

    # 卡尔曼滤波吸收了当期结果：打乱标签后 LogLoss 仍远低于基线，判为泄漏
    leaky = control_issues(ctx, report(value="0.4934", shuffled_value="0.5010", chance="0.6778"))
    assert len(leaky) == 1 and "泄漏" in leaky[0]
    # 打乱后回到无信息水平、且优于简单基线：通过
    auc = dict(metric="AUC", value="0.71", shuffled_value="0.502", chance="0.5", higher_is_better=True, baseline_run_id=run_id)
    assert control_issues(ctx, report(**auc, baseline_value="0.6")) == []
    # 没给简单基线：要补
    assert "最简单的有信息基线" in control_issues(ctx, report(**{**auc, "baseline_run_id": ""}))[0]
    # 没超过简单基线（Q1 的滤波势头只是复现了发球方先验）：不是返工能消掉的问题，记为基线差距
    flat = report(**auc, baseline_value="0.71")
    assert control_issues(ctx, flat) == [] and "没有优于基线" in baseline_gaps(ctx, flat)[0]
    # 拿无信息水平充当基线：要换
    assert "就是无信息水平" in control_issues(ctx, report(**auc, baseline_value="0.502"))[0]
    # 打乱后的值不在输出里：不能通过
    assert "无法核对" in control_issues(ctx, report(value="0.4934", shuffled_value="0.6801", chance="0.6778"))[0]
    # 报告了 AUC 却没做对照
    missing = control_issues(ctx, {"summary": "测试集 AUC 0.71", "key_findings": [], "controls": []})
    assert "没有做标签置换对照" in missing[0]
    assert control_issues(ctx, {"summary": "检验 p 值 0.03", "key_findings": [], "controls": []}) == []
    # 假设检验类子问题不要求置换对照
    assert control_issues(ctx, {"summary": "检验功效 AUC 0.71", "key_findings": [], "controls": []}, hypothesis=True) == []
    # 打乱后的值恰好等于无信息水平：像是填的理论值
    assert "恰好等于无信息水平" in control_issues(ctx, report(**{**auc, "shuffled_value": "0.6", "chance": "0.6"}, baseline_value="0.71"))[0]


def test_leaky_metric_downgrades_findings(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.review import downgrade_findings

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "auc 0.7768 shuffled 0.6440 weight 0.3416")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    registry.record_value("Q1", "AUC", "0.7768", run_id)
    registry.record_value("Q1", "权重", "0.3416", run_id)
    report = {
        "controls": [
            {"metric": "AUC", "value": "0.7768", "run_id": run_id, "shuffled_value": "0.6440", "chance": "0.5", "higher_is_better": True},
            # 检验的 p 值不是预测指标，置换后仍偏小也不算泄漏
            {"metric": "单侧p值", "value": "0.0428", "run_id": run_id, "shuffled_value": "0.1442", "chance": "0.5", "higher_is_better": False},
        ],
        "key_findings": [
            {"statement": "综合动量 AUC 为 {{R:R1}}，提升来自有效信号", "strength": "显著"},
            {"statement": "得分率权重 {{R:R2}} 最高", "strength": "显著"},
            {"statement": "波动幅度检验 p={{R:R3}}", "strength": "显著"},
            {"statement": "另一次运行碰巧同值 {{R:R4}}", "strength": "显著"},
        ],
    }
    other = fs.create_run()
    fs.save_run_stdout(run_id, "auc 0.7768 shuffled 0.6440 weight 0.3416 p 0.0428")
    fs.save_run_stdout(other, "0.7768")
    fs.finish_run(other, "success")
    registry.record_value("Q2", "p", "0.0428", run_id)
    registry.record_value("Q1", "另一个", "0.7768", other)
    downgrade_findings(SimpleNamespace(registry=registry), report)
    assert [f["strength"] for f in report["key_findings"]] == ["不显著", "显著", "显著", "显著"]
    assert "未通过标签置换对照" in report["key_findings"][0]["statement"]


def test_implausible_refs_catch_wrong_reference_numbers(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.review import implausible_refs

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "chi2 9.6546 p 0.0412 events 426 auc 0.5657")
    fs.finish_run(run_id, "success")
    registry = ResultRegistry.load(fs)
    for name, value in [("卡方统计量", "9.6546"), ("p", "0.0412"), ("事件数", "426"), ("AUC", "0.5657")]:
        registry.record_value("Q2", name, value, run_id)
    ctx = SimpleNamespace(registry=registry)
    bad = {"summary": "检验 p={{R:R1}}，AUC 为 {{R:R3}}", "key_findings": [{"statement": "q=1.5 时事件 {{R:R3}} 个"}]}
    issues = implausible_refs(ctx, bad)
    assert len(issues) == 1 and "{{R:R1}}" in issues[0] and "AUC 为 {{R:R3}}" in issues[0] and "卡方统计量" in issues[0]
    good = {"summary": "卡方 {{R:R1}}，p 值为 {{R:R2}}，AUC={{R:R4}}，共 {{R:R3}} 个事件", "key_findings": []}
    assert implausible_refs(ctx, good) == []


def test_probe_metric_rejected_when_shuffled_labels_keep_the_gain(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.explore import shuffle_check

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    run_id = fs.create_run()
    fs.save_run_stdout(run_id, "logloss 0.4674 baseline 0.6778 shuffled 0.48 clean 0.675")
    fs.finish_run(run_id, "success")
    ctx = SimpleNamespace(registry=ResultRegistry.load(fs))
    report = {"metric_value": "0.4674", "metric_run_id": run_id, "baseline_value": "0.6778"}
    protocol = {"higher_is_better": False}
    assert "泄漏" in shuffle_check(ctx, {**report, "shuffled_value": "0.48"}, protocol)
    assert shuffle_check(ctx, {**report, "shuffled_value": "0.675"}, protocol) == ""
    assert shuffle_check(ctx, report, protocol) == "没有做标签置换对照"


def test_metric_direction_overrides_model_for_known_metrics():
    from ai_system.graph.nodes.review import metric_direction

    assert metric_direction("验证集上的对数损失（LogLoss）") is False
    assert metric_direction("测试集 RMSE") is False
    assert metric_direction("留出集 AUC") is True
    assert metric_direction("宏平均 F1") is True
    assert metric_direction("综合得分") is None
    assert metric_direction("测试集负对数似然") is None  # 两边都命中时不覆盖
    # 取了负号的损失越大越好
    assert metric_direction("验证集 -LogLoss（球员1赢分）") is True
    assert metric_direction("负对数损失") is True


def test_strip_template_tokens_from_code():
    from ai_system.graph.nodes.solve import strip_template_tokens

    leaked = "print(df['swing'].mean())</｜DSML｜parameter>    }  ]</｜DSML｜invoke></think>Let me define the target"
    assert strip_template_tokens(leaked) == "print(df['swing'].mean())"
    assert strip_template_tokens("x = 1\nprint(x)") == "x = 1\nprint(x)"


def test_llm_call_trace_records_label_node_and_slow_calls():
    from uuid import uuid4

    from langchain_core.outputs import LLMResult

    from ai_system.graph.tracing import LLMCallTrace, labelled

    trace = LLMCallTrace()
    ok, failed = uuid4(), uuid4()
    with labelled("Q2:design"):
        trace.on_chat_model_start({}, [[]], run_id=ok, metadata={"langgraph_checkpoint_ns": "solve:abc|model:def"})
    trace.on_chat_model_start({}, [[]], run_id=failed, metadata={"langgraph_node": "write"})
    message = AIMessage(content="x", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                        response_metadata={"finish_reason": "length"})
    trace.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=ok)
    trace.on_llm_error(TimeoutError("timed out"), run_id=failed)

    first, second = trace.calls
    assert (first["node"], first["label"], first["input"], first["output"], first["finish"]) == ("solve", "Q2:design", 100, 20, "length")
    assert second["node"] == "write" and second["label"] == "" and "TimeoutError" in second["error"]
    summary = trace.summary()
    assert (summary["calls"], summary["truncated"], summary["errors"]) == (2, 1, 1)
    assert summary["by_node"]["solve"]["output"] == 20


def test_cap_output_only_lowers_max_tokens():
    from langchain_openai import ChatOpenAI

    from ai_system.graph.llm_utils import cap_output

    llm = ChatOpenAI(model="m", api_key="k", max_tokens=16000)
    assert cap_output(llm, 4096).max_tokens == 4096 and llm.max_tokens == 16000
    small = ChatOpenAI(model="m", api_key="k", max_tokens=1000)
    assert cap_output(small, 4096) is small
    assert cap_output(FakeModel(), 4096).__class__ is FakeModel


def test_placeholder_runs_are_not_executed(tmp_path):
    from ai_system.graph.nodes.solve import PLACEHOLDER_REPLY, build_tools

    ws = _make_workspace(tmp_path)
    ctx = _context(ws, FakeModel())
    run_log: List = []
    run_python = build_tools(ctx, "Q1", run_log)[0]

    async def go():
        try:
            return [await run_python.ainvoke({"code": code}) for code in ("print('x')", "print('ok')\nprint('done')", "import pandas as pd, numpy as np\nprint('q3')", "print(1 + 1)")]
        finally:
            await ctx.kernels.close()

    outputs = asyncio.run(go())
    assert outputs[:3] == [PLACEHOLDER_REPLY] * 3
    assert "2" in outputs[3] and len(run_log) == 1


def test_refused_steps_dropped_from_request():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from ai_system.graph.compaction import drop_refused_steps

    def call(i):
        return {"name": "run_python", "args": {"code": "x"}, "id": f"c{i}"}

    messages = [
        HumanMessage(content="task"),
        AIMessage(content="", tool_calls=[call(1)]),
        ToolMessage(content="NO", tool_call_id="c1"),
        AIMessage(content="", tool_calls=[call(2), call(3)]),
        ToolMessage(content="NO", tool_call_id="c2"),
        ToolMessage(content="out", tool_call_id="c3"),
        AIMessage(content="", tool_calls=[call(4)]),
        ToolMessage(content="out", tool_call_id="c4"),
    ]
    kept = drop_refused_steps(messages, "NO")
    # 全部被拒的一步整步去掉；部分被拒的一步保留，否则剩下的调用没有对应结果
    assert [getattr(m, "tool_call_id", None) for m in kept] == [None, None, "c2", "c3", None, "c4"]
    assert drop_refused_steps(messages[:1], "NO") == messages[:1]


def test_truncation_guard_retries_with_note():
    from ai_system.graph.compaction import TRUNCATED_NOTE, TruncationGuardMiddleware

    class Request:
        def __init__(self, messages):
            self.messages = messages

        def override(self, messages):
            return Request(messages)

    seen = []

    async def handler(request):
        seen.append(request.messages)
        reason = "length" if len(seen) == 1 else "stop"
        return AIMessage(content="", response_metadata={"finish_reason": reason})

    result = asyncio.run(TruncationGuardMiddleware().awrap_model_call(Request([HumanMessage(content="go")]), handler))
    assert result.response_metadata["finish_reason"] == "stop"
    assert len(seen) == 2 and seen[1][-1].content == TRUNCATED_NOTE and seen[1][0].content == "go"


def test_malformed_tool_call_is_retried_and_never_left_in_history():
    from ai_system.graph.compaction import MALFORMED_NOTE, TruncationGuardMiddleware, drop_invalid_calls

    class Request:
        def __init__(self, messages):
            self.messages = messages

        def override(self, messages):
            return Request(messages)

    bad = {"name": "run_python", "args": '{"code": "print(1', "id": "c1", "error": "bad json", "type": "invalid_tool_call"}
    seen = []

    async def handler(request):
        seen.append(request.messages)
        return AIMessage(content="", invalid_tool_calls=[bad], response_metadata={"finish_reason": "tool_calls"})

    result = asyncio.run(TruncationGuardMiddleware().awrap_model_call(Request([HumanMessage(content="go")]), handler))
    assert len(seen) == 2 and seen[1][-1].content == MALFORMED_NOTE
    # 重试仍然坏掉时，坏调用不留在历史里（否则下一次请求被接口以"工具调用没有结果"拒绝）
    assert result.invalid_tool_calls == []
    assert drop_invalid_calls(AIMessage(content="x")).content == "x"


def test_run_agent_asks_for_report_when_steps_run_out():
    from langgraph.errors import GraphRecursionError

    from ai_system.graph.compaction import HistoryRecorder
    from ai_system.graph.nodes.solve import FINAL_NUDGE, ReportFallback, run_agent

    class Agent:
        async def ainvoke(self, payload, config=None):
            raise GraphRecursionError("limit")

    seen = []

    class Reporter:
        def with_structured_output(self, schema, **kwargs):
            outer = self

            class Bound:
                async def ainvoke(self, messages):
                    seen.append(messages)
                    return schema(summary="已完成", approach="a", key_findings=[])

            return Bound()

    recorder = HistoryRecorder()
    recorder.messages = [HumanMessage(content="任务"), AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "1"}, "id": "a"}]),
                         ToolMessage(content="1", tool_call_id="a")]
    from ai_system.graph.state import SolveReport

    result = asyncio.run(run_agent(Agent(), [], 5, fallback=ReportFallback(Reporter(), SolveReport, "系统", recorder)))
    assert result["structured_response"].summary == "已完成"
    assert seen[0][0].content == "系统" and seen[0][-1].content == FINAL_NUDGE and len(seen[0]) == 5
    # 没有对话记录时照常抛出
    with pytest.raises(GraphRecursionError):
        asyncio.run(run_agent(Agent(), [], 5, fallback=ReportFallback(Reporter(), SolveReport, "系统", HistoryRecorder())))


def test_stream_manager_accepts_structured_block():
    """plan_updated / paper_phase 的 content 是 dict，send_json_block 不能按字符串切片"""
    from ai_system.core_managers.stream_manager import StreamOutputManager

    received = []

    class Callback:
        async def on_json_block(self, block):
            received.append(block)

    asyncio.run(StreamOutputManager(Callback()).send_json_block("paper_phase", {"stage": "solve", "status": "in_progress"}))
    assert received == [{"type": "paper_phase", "content": {"stage": "solve", "status": "in_progress"}}]


def test_writing_brief_asks_for_actionable_advice():
    from ai_system.graph.nodes.write import ACTIONABLE_ADVICE, subproblem_brief

    memo = {"id": "Q4", "kind": "writing", "title": "给教练的备忘录", "question": "给出建议", "deliverables": ["一页备忘录"]}
    brief = subproblem_brief({"analysis": {"subproblems": [memo]}}, None, memo)
    assert ACTIONABLE_ADVICE in brief


def test_dependency_handoff_includes_solution_code(tmp_path):
    from types import SimpleNamespace

    from ai_system.graph.nodes.solve import _dependency_text, export_solution_code, solution_code_path

    fs = WorkspaceFS(str(tmp_path))
    fs.init_structure()
    ctx = SimpleNamespace(fs=fs, workspace=tmp_path, registry=ResultRegistry.load(fs))
    export_solution_code(ctx, "Q2", [
        {"run_id": "r1", "code": "def backtest(p):\n    return p", "ok": True},
        {"run_id": "r2", "code": "raise ValueError", "ok": False},
        {"run_id": "r3", "code": "print('ok')", "ok": True},
    ])
    code = (tmp_path / solution_code_path("Q2")).read_text(encoding="utf-8")
    assert "def backtest" in code and "ValueError" not in code and "print('ok')" not in code
    (tmp_path / "outputs" / "intermediate").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outputs" / "intermediate" / "aligned.csv").write_text("a\n1\n")
    text = _dependency_text({"Q2": {"summary": "策略回测"}}, ctx)
    assert solution_code_path("Q2") in text and "outputs/intermediate/aligned.csv" in text
    # 没有成功执行过的代码时不写文件
    export_solution_code(ctx, "Q3", [{"run_id": "r4", "code": "x", "ok": False}])
    assert not (tmp_path / solution_code_path("Q3")).exists()


def test_explicit_new_run_does_not_resume_an_interrupted_plan(tmp_path):
    ws = _make_workspace(tmp_path)
    agent = PaperGraphAgent(FakeModel(), workspace_dir=str(ws), judge=FixedJudge(), confirm_models=True)
    asyncio.run(agent.run(TASK))
    first_thread = agent._load_thread()
    assert agent.waiting_confirm
    asyncio.run(agent.run("另一项研究", new_run=True))
    assert agent.waiting_confirm
    assert agent._load_thread() != first_thread
