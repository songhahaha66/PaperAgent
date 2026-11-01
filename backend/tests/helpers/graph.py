"""Offline model and workspace builders shared by graph tests."""

import json
import re
from pathlib import Path
from typing import List

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


from ai_system.graph import GraphContext
from ai_system.graph.state import (
    ClaimAudit,
    ClaimIssue,
    Conflict,
    ConsistencyReview,
    ResultReview,
)
from helpers.legacy_graph import CandidateSet, MethodPick, ModelCandidate, Subproblem
from ai_system.research.models import Requirement, Assumption, IssueReport
from ai_system.research.planner import AmendmentSpec

TASK = "某城市给出 2015-2024 年用电量数据。问题一：建立模型预测 2025 年用电量。问题二：在问题一基础上评估节能政策效果。"


# ----------------------------------------------------------------------
# 假模型：按调用内容返回固定输出，覆盖结构化输出、工具调用循环和纯文本写作
# ----------------------------------------------------------------------


from pydantic import BaseModel, Field
from research_tools_helpers import research_step


class ProblemAnalysis(BaseModel):
    """Saved full-analysis fixture; no production startup schema remains."""
    title: str
    restatement: str
    overall_analysis: str
    subproblems: list[Subproblem] = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    assumptions: list[Assumption]
    outline: list[dict] = Field(default_factory=list)

    def model_dump(self, **kwargs):
        value=super().model_dump(**kwargs)
        value['questions']=[{'id':'question:'+sp.id,'title':sp.title,'question':sp.question,
            'experiment_ids':[sp.id]} for sp in self.subproblems]
        return value


def _analysis() -> ProblemAnalysis:
    return ProblemAnalysis(
        title="基于时间序列的城市用电量预测与政策评估",
        restatement="重述",
        overall_analysis="先预测再评估",
        subproblems=[
            Subproblem(id="Q1", title="用电量预测", question="预测 2025 年用电量", deliverables=["预测值"]),
            Subproblem(id="Q2", title="政策评估", question="评估节能政策", depends_on=["Q1"]),
        ],
        requirements=[Requirement(id="RQ1", statement="预测用电量", source_quote="预测 2025 年用电量", kind="goal", task_ids=["Q1"]),
                      Requirement(id="RQ2", statement="评估政策效果", source_quote="评估节能政策效果", kind="goal", task_ids=["Q2"]),
                      Requirement(id="RQ3", statement="形成政策建议", source_quote="评估节能政策效果", kind="deliverable", task_ids=["Q1","Q2"])],
        assumptions=[Assumption(id="A1", statement="数据真实可靠", task_ids=["Q1", "Q2"])],
        outline=[{'key':'recommendations','title':'政策建议','purpose':'依据预测与政策评估结果表达建议和未知，不产生写作实验','task_ids':['Q1','Q2']}],
    )


def _candidate(name: str) -> ModelCandidate:
    return ModelCandidate(
        name=name, method_keys=["arima", "regression"], idea="思路", formulation="$y_t = a y_{t-1}$",
        steps=["读数据", "拟合"], assumptions=[], validation="残差检验",
    )


def decision_outline(context,coverage):
    basis=[b for c in coverage for b in c['basis']]
    return [{'key':key,'title':title,'purpose':'按当前实际证据表达研究结果、来源与局限。',
             'task_ids':[],'basis':basis[:12]} for key,title in [
        ('abstract','摘要'),('restatement','问题重述'),('analysis','问题分析'),('assumptions','模型假设'),
        ('symbols','符号说明'),('solve_heading','模型建立与求解'),('sensitivity','灵敏度分析'),('evaluation','模型评价')]]


class _Structured:
    reviewed_q1 = False
    audits = 0

    def __init__(self, schema):
        self.schema = schema

    async def ainvoke(self, messages):
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

        if self.schema is ConsistencyReview:
            # A valid completed review; malformed IDs are fail-closed separately.
            return ConsistencyReview(conflicts=[
                Conflict(claims=["C1"], issue="Q1 结论强度与 Q2 不符", resolution="统一表述为倾向性结论"),
            ])
        if self.schema is ClaimAudit:
            allowed=re.search(r'section 只能填这些 key：(\[[^\n]*\])',messages[-1].content)
            if allowed and 'evaluation' not in json.loads(allowed[1]):
                return ClaimAudit(issues=[])  # Custom templates have different section keys.
            _Structured.audits += 1
            if _Structured.audits == 1:
                return ClaimAudit(issues=[
                    ClaimIssue(section="evaluation", problem="把 C1 写成了显著"),
                    # 核对后一致的句子不是问题，不应触发修复
                    ClaimIssue(section="evaluation", problem="正文称 AUC 为 0.79，与 C1 一致。无矛盾。"),
                    ClaimIssue(section="evaluation", contradicts=False, problem="没有提到 C1"),
                ])
            return ClaimAudit(issues=[])
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
        return self.bind(tools=tools,**kwargs)

    def with_structured_output(self, schema, **kwargs):
        return _Structured(schema)

    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        tools=kwargs.get('tools') or []
        names={t.get('function',t).get('name') if isinstance(t,dict) else t.name for t in tools}
        reply=self._research_step(messages,_analysis()) if 'research_create_question' in names else self._respond(messages)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _research_step(self,messages,plan):
        return research_step(messages,plan,transform=self._draft)

    def _draft(self,text):return text

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
        sid = re.search(r"# 子问题 ([QE]\d+)", task).group(1)
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        last = tool_results[-1].content if tool_results else ""
        found = re.search(r"\[run_id: (\S+)\]", last)
        run_id = found.group(1) if found else ""
        step = len(tool_results)
        if step == 2 and sid in FAIL_ONCE:
            FAIL_ONCE.discard(sid)
            raise RuntimeError("403 real_name_required")
        value = "1234.57" if sid in {"Q1","E1"} else "8.5%"
        script = [
            ("run_python", {"code": "forecast = 1234.5678\nprint('forecast', forecast)\nprint('saving', 0.085)"}),
            ("run_python", {"code": "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])"}),
            ("SolveReport", {"summary": "完成，预测为 " + value, "approach": "ARIMA",
                "key_findings": [{"statement": "当前条件下的结果为 " + value, "strength": "倾向"}],
                "sensitivity": "稳定", "limitations": ["样本少"], "validation":"已检查输入与输出范围"}),
        ]
        name, args = script[min(step, len(script) - 1)]
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{sid}_{step}"}])

    def _probe_step(self, task: str, messages: List[BaseMessage]) -> AIMessage:
        # 方案甲真实 AUC 0.71；方案乙打印 0.65 却报告 0.99（编造），核对后不参与比较
        sid = re.search(r"# 子问题 ([QE]\d+)", task).group(1)
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
        if "撰写「模型的评价与推广」" in request and "# 当前稿" in request and "把 C1 写成了显著" in request:
            body = ("本节依据登记结果展开论述，" + "、".join(numbers)
                    + "。当前证据只能统一表述为倾向性结论，尚无统计显著性检验证据。"
                      "结论限于当前数据和已检验条件，推广到其他条件仍需独立验证。")
        return body


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    (ws / "data" / "power.csv").write_text("year,power\n2023,1100.5\n2024,1180.2\n", encoding="utf-8")
    return ws


def _context(ws: Path, model: FakeModel, **options) -> GraphContext:
    # These fixtures exercise multi-route comparison explicitly.
    return GraphContext.create(str(ws), planner_llm=model,
                               **options)
