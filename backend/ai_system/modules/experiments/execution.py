"""
阶段 4：求解（每个子问题一个分支，按依赖关系分批并行）

每个分支是一个小的 CodeAct 循环：模型写代码 → 持久内核执行 → 看输出 → 继续，
关键数字和图通过 record_results / record_figure 登记，登记时校验数值确实来自某次执行的输出。
最后用结构化输出交一份求解报告，登记引用须有真实执行来源。

候选意图由研究控制器按任务版本冻结；普通实验直接执行，CSV比较是显式执行方式。
"""

from ai_system.research.domain import findings as research_findings

import ast
import json
import logging
import re
from dataclasses import asdict
from typing import Any, Callable, Dict, List, NamedTuple, Optional
from pydantic import create_model, model_validator

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, messages_to_dict
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.runtime import Runtime

from ai_system.graph.compaction import (
    CompactHistoryMiddleware,
    DropRefusedStepsMiddleware,
    HistoryRecorder,
    ReportOrderingMiddleware,
    StepBudgetMiddleware,
    TruncationGuardMiddleware,
    compact_messages,
    drop_invalid_calls,
)
from ai_system.graph.context import GraphContext, list_data_files
from ai_system.graph.llm_utils import call_structured, clip
from ai_system.modules.methods import describe, skill_policy_prompt, allowed_method_keys, SKILL_SOURCES, SKILL_VERSION
from ai_system.graph.registry import GroundingError
from ai_system.graph.state import BranchInput, Finding, ResultItem, ResultReview, SolveReport
from ai_system.graph.tracing import labelled, set_label
from ai_system.research.assumptions import unbound_assumptions
from ai_system.research.models import IssueReport, ReviewRecord
from ai_system.research.validation import report_fingerprint
from ai_system.modules.experiments.review import (
    feedback_message,
    needs_figure,
    report_issues,
    review_solution,
    report_submission_issues,
    submission_inventory_issues,
)

logger = logging.getLogger(__name__)

SYSTEM = """你是数学建模竞赛的编程手，负责把一个子问题的建模方案用 Python 实现并求出结果。

工作方式：
- 有冻结候选实验时，只执行所记录的方法和参数，SolveReport.approach说明实际执行的方法，候选身份由系统记录。无法实现时如实提交失败与原因，不自行改用其他算法或改变评价条件；交回observe，在原问题下记录新实验后再试。
- 用 run_python 执行代码。内核是持久的：前面定义的变量、读入的数据后面都能直接用，不要每次重新读数据。
- 每次写一段完整的代码（读数据、计算、打印关键结果可以放在一起），看到输出再决定下一步；出错就读回溯修正。
- run_python 只做读取、计算和绘图。record_results、record_figure、SolveReport 是外部工具，不能在 Python 代码里当函数调用；登记与报告分别通过工具提交，不在计算代码中编写整篇报告。
- 只 print 需要看的汇总结果，不要打印整张表或长数组（输出超过约 6000 字会被截断）。
- 数据文件路径相对于工作目录，例如 data/xxx.csv。
- 画图用 matplotlib，画完不需要 savefig，系统会自动保存本次执行打开的图并告诉你文件名。
- 需要传给后续子问题的中间数据，保存到 outputs/intermediate/ 下（csv），并在报告里说明文件名。
- 默认使用题目给出的全部相关数据（例如全部比赛、全部年份），只用子集时要在报告里说明理由。
- 建模要防止数据泄漏：特征只能用预测时刻之前的信息；报告验证集指标时同时给出基准率或对照基线。
- 按原题与冻结契约完成样本外评估和所需方法比较。普通预测不因报告MAE/RMSE就额外强制标签置换；时序数据不应硬套独立同分布的标签置换。
  只有确需检验额外预测增益或契约明确要求时才做适用的对照。主动提交controls时必须真实重算、登记数值与run_id；未做就留空，不能编造。
  是否优于基线、各误差指标排序是否一致，应如实报告；效果不足是研究结果，不得为通过验收伪造更好的值。
- 图中的标题、坐标轴、图例一律用中文，图例不要遮挡标题和数据。
- 内核已自动配置本机可用的中文字体，不要用未安装的SimHei等字体覆盖配置。

结果登记（非常重要）：
- 论文里会用到的每个关键数字（参数估计、预测值、指标、最优解等），先 print 出来，再用 record_results 登记，run_id 填打印它的那次执行。
- 把一批结果放进一次 record_results 调用里登记，不要一个结果调用一次。
- 执行、登记完成并拿到工具返回的真实编号之后，单独调用 SolveReport；不能与计算或登记工具同时调用报告。
- 登记的值要和输出一致（可以四舍五入到合理的位数，百分数写成 87.3%）。没出现在输出里的值会被拒绝。
- 同一变量的拟合值、预测值等完整数值序列可登记为JSON数组字符串（先print完整数组，推荐json.dumps）；顺序和长度必须与输出一致，不能省略或拼接不同指标。数值比较只接受标量，长度、最小值等单独计算登记。
- 论文要用的图用 record_figure 登记，并写清图题。
- 按本子问题要求完成验证并登记结果；数据整理只需核对数据完整性和计算。不要提前完成下游预测，或额外加入题目和本问未要求的假设检验、预测实验。

完成后提交求解报告：概要、实际方法、主要结论、灵敏度分析和局限。报告里的数字一律写成登记返回的引用（如 {{R:R3}}），不要直接写数字。
RQ0是完整原题，本次只执行subproblem指定的实验，不宣称全题已经完成。
SolveReport.assumptions不是论文假设汇总，没有新增时必须留空[]。只提交求解中新发现、实际改变方法或结论的关键前提。
原题给定的观测值等事实、已计算验证的数据长度/完整性、契约中已有的前提都直接使用，不再次声明。
不要通过换key、改写或合并已有前提制造“新假设”；已有A:任务:key的key就是该任务稳定键，通常无需再次填入。
确有新增时填入assumptions，使用稳定key、statement和可证伪checks；已有契约前提不能静默改写。
新前提会先登记版本再按冻结契约重算；它不是原题事实，也不因代码跑通而被验证。
每条主要结论都要写出依据的登记结果，并如实标注证据强度：检验不显著的就标「不显著」，不要把不显著的结果写成有效应。
这些结论会和其他子问题的结论放在一起做一致性审查。"""


OBSERVATION_CHARS = 6000
WRITING_APPROACH = "基于前序结论撰写"

FINAL_NUDGE = "步数已经用完，不能再执行代码。请根据上面已经得到的输出和登记的结果，现在提交报告；报告里的数字只能写登记返回的引用。"
REPORT_NUDGE = "求解已结束的话，请现在调用 SolveReport 提交求解报告（数字一律写成登记返回的引用）；还缺关键结果就先补齐再提交。"


# 网关的工具调用解析偶尔把模型的对话模板标记（DeepSeek 的 <｜DSML｜…>、</think>）连同后面的文字
# 一起塞进代码参数，整段执行时 SyntaxError；2024_C 一次运行 30 次执行里有 4 次这样浪费掉
TEMPLATE_TOKEN = re.compile(r"</?｜|｜DSML｜|</think>|<think>")

# v2h/v2k 的返工阶段各有 30~40 次 print('x') / print('ok') 这种空执行：每次占一步预算、一次模型往返
# 前面带几行 import 的也算（v3 的 2022_C Q3："import pandas as pd, numpy as np\nprint('q3')" 执行了十几次）
PLACEHOLDER_CODE = re.compile(
    r"""^\s*(?:(?:import [\w., ]+|from [\w.]+ import [\w., ]+)\s*\n\s*)*(?:print\(\s*(['"])[\w\s.!-]{0,12}\1\s*\)\s*;?\s*)+$"""
)
PLACEHOLDER_REPLY = (
    "这段代码只包含占位语句或固定字符串，没有执行。不需要用 run_python 占位或确认："
    "登记结果直接调用 record_results（run_id 填之前打印该值的那次执行），全部完成后直接提交 SolveReport。"
)


def is_placeholder(code: str) -> bool:
    if PLACEHOLDER_CODE.match(code):
        return True
    try:
        body = ast.parse(code).body
    except SyntaxError:
        return False  # Let execution return the real syntax error.
    empty = lambda node: isinstance(node, ast.Pass) or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    return not body or (any(empty(node) for node in body) and all(
        empty(node) or isinstance(node, (ast.Import, ast.ImportFrom)) for node in body))


def strip_template_tokens(code: str) -> str:
    """截掉代码里第一个对话模板标记及其之后的内容"""
    match = TEMPLATE_TOKEN.search(code)
    return code[: match.start()].rstrip() if match else code


def solution_code_path(subproblem_id: str) -> str:
    return f"code/{subproblem_id}_solution.py"


def export_solution_code(ctx: GraphContext, subproblem_id: str, run_log: List[Dict]) -> None:
    """把本问成功执行过的代码按顺序存成一个文件，供依赖它的子问题复用

    每个子问题有自己的内核，后一问看不到前一问的函数和变量。2022_C 的 Q3 要"运行 Q2 选定的策略"，
    只拿到 Q2 的结论和数值，只好从读数据开始重写回测，日期解析又踩了一遍坑，171 条对话后交了空报告。
    """
    runs = []
    for run in run_log:
        code = run.get("code") or ""
        if is_placeholder(code):
            continue
        if not run.get("ok"):
            # A failed cell can already have imported modules or defined values
            # subsequently used by successful cells. Retain only complete
            # top-level statements before the actual failing statement.
            info = ctx.fs.get_run_info(run["run_id"])
            if not info or not info.get("stdout"):
                continue
            trace = ctx.fs.read_text(info["stdout"]).rsplit("Traceback (most recent call last):\n", 1)[-1]
            frame = re.search(r'File "<cell>", line (\d+), in <module>', trace)
            if not frame:
                continue
            try:
                nodes = ast.parse(code).body
            except SyntaxError:
                continue
            prefix = [node for node in nodes if node.end_lineno < int(frame[1])]
            code = "\n".join(code.splitlines()[:prefix[-1].end_lineno]) if prefix else ""
        if code.strip():
            runs.append({**run, "code": code})
    if not runs:
        return
    body = "\n\n".join(f"# ---- {r['run_id']} ----\n{r['code'].rstrip()}" for r in runs)
    header = f"# {subproblem_id} 已执行代码（按顺序，含探索及出错语句之前的完整前缀）。复用时复制需要的函数和处理步骤。\n\n"
    try:
        ctx.fs.write_text(solution_code_path(subproblem_id), header + body)
    except Exception as exc:  # 只是给后续子问题的参考，失败不影响本问
        logger.warning("保存 %s 的代码失败: %s", subproblem_id, exc)


def _intermediate_files(ctx: GraphContext) -> List[str]:
    folder = ctx.workspace / "outputs" / "intermediate"
    if not folder.exists():
        return []
    return sorted(str(p.relative_to(ctx.workspace)) for p in folder.rglob("*") if p.is_file())


def _dependency_text(dependencies: Dict[str, Dict], ctx: GraphContext) -> str:
    if not dependencies:
        return ""
    parts = [
        "# 前序子问题的结论（可直接使用其登记结果、中间数据和代码）\n"
        "前序子问题在各自的内核里运行，它们的变量和函数在你的内核里不存在。需要沿用前序的数据处理或模型时，"
        "先读它的代码文件、复制需要的部分，或读取它保存的中间数据，不要从头重写。"
    ]
    for dep_id, report in dependencies.items():
        parts.append(f"## {dep_id}\n{report.get('summary', '')}")
        if ctx.fs.exists(solution_code_path(dep_id)):
            parts.append(f"代码：{solution_code_path(dep_id)}")
        parts.append(ctx.registry.to_prompt_table(dep_id))
    files = _intermediate_files(ctx)
    if files:
        parts.append("中间数据文件：\n" + "\n".join(f"- {f}" for f in files))
    return "\n\n".join(parts)


def build_tools(
    ctx: GraphContext,
    subproblem_id: str,
    run_log: Optional[List[Dict]] = None,
    kernel_key: Optional[str] = None,
    record: bool = True,
    submission_gaps: Optional[Callable[[], List[str]]] = None,
) -> List[StructuredTool]:
    """record=False 时只给 run_python（探索阶段不登记结果）"""
    kernel = ctx.kernels.get(kernel_key or subproblem_id)
    run_log = run_log if run_log is not None else []

    def submission_feedback(text):
        if submission_gaps is None:
            return text
        gaps = submission_gaps()
        return text + ("\n[提交检查] 尚缺：\n" + "\n".join(f"- {gap}" for gap in gaps)
                       if gaps else "\n[提交检查] 必需登记指标已齐。请单独提交 SolveReport，requirement_evidence 使用已返回的原始编号。")

    async def run_python(code: str) -> str:
        """在持久 Python 内核中执行代码，返回输出、错误回溯和自动保存的图片文件名"""
        code = strip_template_tokens(code)
        if ctx.comparison_proof:
            try:
                if any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'fit','fit_transform','partial_fit','set_params'} for node in ast.walk(ast.parse(code))):
                    return '本任务方案已冻结；禁止重新 fit/调参。请复用 comparison_estimator、_formal_truth、_formal_pred 和已登记的 comparison 指标。'
            except SyntaxError:pass
        if is_placeholder(code):
            return PLACEHOLDER_REPLY
        from ai_system.research.views import activity
        with activity(ctx,'run_python',subproblem_id) as operation:
            await ctx.events.emit('research_activity',{'task_id':subproblem_id})
            result = await kernel.execute(code)
            operation.update(run_id=result.run_id,status='completed' if result.ok else 'failed')
        run_log.append({"run_id": result.run_id, "code": code, "ok": result.ok})
        await ctx.events.emit(
            "paper_code",
            f"{subproblem_id} · {result.run_id} · {'成功' if result.ok else '失败'}",
        )
        observation = clip(result.to_observation(), OBSERVATION_CHARS)
        if not result.ok:
            return observation + "\n[执行失败] 本次run不能作为登记来源。请先修复错误并成功执行，再使用实际返回的run_id登记。"
        if submission_gaps is not None:
            observation += "\n已有执行输出请直接按契约原名登记；不要为登记重复执行相同计算。"
        return submission_feedback(observation)

    async def record_results(items: List[ResultItem]) -> str:
        """一次登记多个论文会用到的结果（同一次或多次执行的结果放在一起登记，减少往返）。
        每个 value 必须出现在对应 run_id 那次执行的输出里，未通过核对的逐条说明原因，其余照常登记。
        返回可在报告和论文中使用的引用"""
        lines = []
        for item in items:
            item = item if isinstance(item, ResultItem) else ResultItem.model_validate(item)
            if ctx.comparison_proof and any(r['name']==item.name for r in ctx.comparison_proof['protected'].values()):
                lines.append('- '+item.name+'：这是项目保护的冻结指标，请直接使用已有引用，不重新登记替换。');continue
            try:
                result = ctx.registry.record_value(
                    subproblem_id, item.name, item.value, item.run_id, item.unit, item.description
                )
            except GroundingError as exc:
                lines.append(f"- {item.name}：登记失败，{exc} 请核对原始执行输出并使用其实际 run_id；不会按相同数值自动改写来源。")
                logger.info("%s 登记「%s」被拒：%s", subproblem_id, item.name, exc)
                continue
            lines.append(f"- {result.name} = {result.display()}，引用写作 {{{{R:{result.id}}}}}")
        return submission_feedback("\n".join(lines) or "没有要登记的结果")

    async def record_figure(run_id: str, figure: str, caption: str) -> str:
        """登记一张论文要用的图。figure 是执行结果里给出的图片文件名，caption 是图题"""
        try:
            result = ctx.registry.record_figure(subproblem_id, run_id, figure, caption)
        except (GroundingError, FileNotFoundError, KeyError) as exc:
            return f"登记失败：{exc}"
        await ctx.events.file_changed(result.path)
        return submission_feedback(f"已登记，引用写作 {{{{F:{result.id}}}}}（{result.name}）")

    tools = [StructuredTool.from_function(coroutine=run_python, name="run_python")]
    if record:
        tools += [
            StructuredTool.from_function(coroutine=record_results, name="record_results"),
            StructuredTool.from_function(coroutine=record_figure, name="record_figure"),
        ]
    return tools


def build_task(
    branch: BranchInput,
    ctx: GraphContext,
    candidate: Optional[Dict] = None,
    extra: str = "",
    ending: str = "开始求解。",
) -> str:
    subproblem = branch["subproblem"]
    chosen = candidate or branch["design"]["chosen"]
    if not set(chosen.get('method_keys') or []) <= allowed_method_keys(ctx.skill_policy):
        raise ValueError('已有方案使用了当前禁用的技能，请重新设计')
    files = "\n".join(f"- {f}" for f in list_data_files(ctx.workspace)) or "（无）"
    from ai_system.research.experience import prompt_context
    from ai_system.research.citations import literature_root
    from ai_system.research.literature import literature_context
    return "\n\n".join(filter(None, [
        f"# 原题（含题内数据；不得用模拟数据替代）\n{branch['source']}",
        f"# 子问题 {subproblem['id']}：{subproblem['title']}\n{subproblem['question']}\n交付物：{'；'.join(subproblem.get('deliverables') or [])}",
        f"# 数据文件\n{files}\n\n# 数据概览\n{branch['data_profile']}",
        '# 用户对必要信息的回复（保留来源；不是计算成果，不得借此放宽冻结契约）\n'+json.dumps(branch['clarifications'],ensure_ascii=False) if branch.get('clarifications') else '',
        f"# 选定的建模方案：{chosen['name']}\n思路：{chosen['idea']}\n数学表述：{chosen['formulation']}\n"
        f"步骤：\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(chosen["steps"]))
        + f"\n检验：{chosen['validation']}",
        literature_context(literature_root(ctx), json.dumps(subproblem, ensure_ascii=False)+'\n'+json.dumps(chosen, ensure_ascii=False)),
        '# 本次冻结实验（方法、参数、数据范围与评价条件不得静默更换）\n'+json.dumps(branch['experiment'],ensure_ascii=False) if branch.get('experiment') else '',
        f"# 用户技能配置\n{skill_policy_prompt(ctx.skill_policy)}",
        f"# 方法参考\n{describe(chosen.get('method_keys') or [], ctx.skill_policy)}",
        prompt_context(ctx,branch,chosen.get('method_keys') or [],'solve') if chosen.get('method_keys') else '',
        _dependency_text(branch.get("dependencies") or {}, ctx),
        "# 研究契约（不得放宽验收；按指定名称登记检查指标，并填写 requirement_evidence）\n" + json.dumps(branch["research_contract"], ensure_ascii=False) if branch.get("research_contract") else "",
        "# 必须逐项复核的先前审查意见\n" + json.dumps(branch["review_findings"], ensure_ascii=False) if branch.get("review_findings") else "",
        extra,
        ending,
    ]))


class ReportFallback(NamedTuple):
    """步数用完时直接要报告：用哪个模型、什么报告格式、什么系统提示，对话从哪份记录取"""

    llm: Any
    schema: Any
    system: str
    recorder: HistoryRecorder
    preflight: Optional[Callable[[], List[str]]] = None


class IncompleteSubmission(ValueError):
    """No tool budget remains to fill the report's missing evidence."""


def submission_schema(ctx: GraphContext, branch: BranchInput, runs: List[Dict]):
    """Let ToolStrategy return concrete gaps to the same live tool loop."""
    @model_validator(mode="after")
    def ready(report):
        sid = branch["subproblem"]["id"]
        # A new premise is staged centrally, never accepted as a solved task.
        if unbound_assumptions(sid, report.assumptions, branch.get("research_contract", {})):
            return report
        report.summary = ctx.registry.ground_statement(sid, report.summary)
        for finding in report.key_findings:
            finding.statement = ctx.registry.ground_statement(sid, finding.statement)
        issues = report_submission_issues(ctx, branch, report.model_dump(), runs)
        from ai_system.modules.experiments.handoff import proof_issues
        issues.extend(proof_issues(ctx,report.model_dump()))
        if issues:
            available = ctx.registry.to_prompt_table(sid)
            successful = [r["run_id"] for r in runs if r.get("ok")][-8:]
            raise ValueError("报告未提交，先补齐以下缺项：\n" + "\n".join(f"- {issue}" for issue in issues)
                + f"\n本问实际登记结果：\n{available}\n本次成功执行：{successful}"
                + "\n内核与变量仍在。已有输出就调用 record_results / record_figure；缺指标再调用 run_python。"
                  "使用工具返回的真实编号补齐 requirement_evidence 和报告，再调用 SolveReport，禁止自造引用。")
        return report

    return create_model("SolveReport", __base__=SolveReport, __validators__={"submission_ready": ready})


async def run_agent(agent, messages: List, recursion_limit: int, nudge: str = REPORT_NUDGE,
                    fallback: Optional[ReportFallback] = None):
    """跑一轮循环；模型用文字收尾、没交报告时提醒一次

    步数用完（GraphRecursionError）时，有 fallback 就从记录里取回对话，不给任何执行工具、直接用结构化输出要报告：
    官方 API 的 deepseek-flash 步子很碎，最后一步只给报告工具它也照样调 run_python，v2l 的 Q3 跑满 40 步后整段作废。
    """
    try:
        result = await agent.ainvoke({"messages": messages}, config={"recursion_limit": recursion_limit})
        if result.get("structured_response") is None:
            history = [drop_invalid_calls(m) if isinstance(m, AIMessage) else m for m in result["messages"]]
            result = await agent.ainvoke(
                {"messages": history + [HumanMessage(content=nudge)]},
                config={"recursion_limit": 11},
            )
        return result
    except GraphRecursionError:
        if fallback is None or not fallback.recorder.messages:
            raise
        if fallback.preflight:
            gaps = fallback.preflight()
            if gaps:
                raise IncompleteSubmission("步数已用尽，报告仍缺少证据：" + "；".join(gaps))
        history = list(fallback.recorder.messages)
        logger.warning("步数用完，用 %d 条对话直接要报告", len(history))
        prompt = [SystemMessage(content=fallback.system), *compact_messages(history), HumanMessage(content=FINAL_NUDGE)]
        report = await call_structured(fallback.llm, fallback.schema, prompt)
        return {"messages": history + [HumanMessage(content=FINAL_NUDGE)], "structured_response": report}


async def solve(branch: BranchInput, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    subproblem_id = branch["subproblem"]["id"]
    set_label(subproblem_id)
    await ctx.set_stage("solve", "in_progress")
    if branch["subproblem"].get("kind") == "writing":
        # 写作类子问题不开内核、不跑编码循环，写作阶段直接依据前序结论撰写
        await ctx.events.stage(f"{subproblem_id} 为写作类交付物，写作阶段依据前序结论撰写")
        report = SolveReport(
            summary="本问为文书类交付物，依据前序各问的结论撰写。",
            approach=WRITING_APPROACH,
            key_findings=[],
        ).model_dump()
        update = {
            "solutions": {subproblem_id: {**report, "status": "completed", "error": None, "reviews": []}},
            "log": [f"solve {subproblem_id}: writing"],
        }
        if branch.get("design") is None:
            from ai_system.modules.experiments.planning import WRITING_DESIGN

            update["designs"] = {subproblem_id: {
                "methods": [], "candidates": [WRITING_DESIGN], "scores": [{"total": None}],
                "chosen": WRITING_DESIGN, "chosen_index": 0, "critic": None, "critic_error": None,
            }}
        return update

    run_log: List[Dict] = []
    if branch.get('experiment'):
        from .planning import experiment_design
        branch={**branch,'design':experiment_design(branch['experiment'])}
        branch['design']['clarification_refs']=[r['ref'] for r in branch.get('clarifications',[])]
    designed_here = branch.get("design") is None
    if designed_here:
        # 按需设计：轮到这个子问题时才设计，候选方案能参考已经求解完的前序结论

        if branch["subproblem"].get("kind") == "verify":
            chosen = {"name": "独立复算固定验收", "method_keys": [], "idea": "从冻结输入独立复算争议检查，不引入新的建模前提。",
                "formulation": "", "steps": ["读取冻结输入", "独立复算并登记指定指标"], "assumptions": [], "validation": "提交固定检查所需证据"}
            record = {"methods": [], "candidates": [chosen], "scores": [{"total": None}], "chosen": chosen,
                "chosen_index": 0, "critic": None, "critic_error": None}
        else:
            chosen={'name':'执行当前研究问题','method_keys':sorted(allowed_method_keys(ctx.skill_policy)) if ctx.skill_policy and ctx.skill_policy['mode']=='selected' else [],
                    'idea':branch.get('feedback') or '根据已有材料和当前问题落实方法，如实说明选择、证据和局限。',
                    'formulation':'','steps':['读取相关材料并选择适用方法','完成本问所需计算与检验，登记实际结果'],
                    'assumptions':[],'validation':'保留原问题及冻结验收契约，不虚构数据或阈值'}
            record={'methods':chosen['method_keys'],'candidates':[chosen],'chosen':chosen,'chosen_index':0,'scores':[],
                    'critic':None,'critic_error':None,'direct_execution':True,
                    'clarification_refs':[r['ref'] for r in branch.get('clarifications',[])]}
        branch = {**branch, "design": record}
    design = branch["design"]
    try:
        if (branch.get('experiment') or {}).get('csv'):
            from ai_system.modules.registry import invoke
            from .handoff import publish_comparison
            record=await invoke(ctx,'experiments.compare',branch=branch)
            report=await publish_comparison(ctx,branch,record,run_log)
            if report['status']=='completed' and ctx.max_review_rounds>0:
                try:
                    review=await review_solution(ctx,branch,report,run_log)
                except Exception as exc:
                    review=ResultReview(issues=[IssueReport(task_id=subproblem_id,category='service',message=f'局部审查未完成：{exc}')])
                cited={ref for issue in review.issues for ref in issue.evidence_refs}
                report['reviews']=[ReviewRecord(report_hash=report_fingerprint(report),issues=review.issues,
                    evidence=[asdict(r) for r in ctx.registry.for_subproblem(subproblem_id) if r.id in cited]).model_dump()]
            return {'solutions':{subproblem_id:report},'designs':{subproblem_id:design},
                    'log':['comparison measurements returned to observe']}
        if unbound_assumptions(subproblem_id, design["chosen"].get("assumptions", []), branch.get("research_contract", {})):
            return {"solutions": {subproblem_id: {"status": "prepared", "reviews": []}},
                    "designs": {subproblem_id: design}, "log": [f"prepare {subproblem_id}: register selected premises"]}
        await ctx.events.stage(f"{subproblem_id}：开始编码求解「{design['chosen']['name']}」")
        status, error, report, reviews = await _solve_loop(
            ctx, branch, run_log, [HumanMessage(content=build_task(branch, ctx))]
        )
    finally:
        await ctx.kernels.release(subproblem_id)
        export_solution_code(ctx, subproblem_id, run_log)

    if not report:
        # 保留已登记结果供恢复和回查；未完成状态仍由提交器阻止验收。
        recorded = ctx.registry.for_subproblem(subproblem_id)
        report = SolveReport(
            summary="求解未正常结束，以下为已登记的结果。",
            approach=design["chosen"]["name"],
            key_findings=[Finding(statement=f"{r.name}：{{{{{r.id[0]}:{r.id}}}}}", strength="倾向") for r in recorded],
        ).model_dump()
    else:
        # 写作看的报告和结论表用同一份陈述：手写的数换成引用
        for finding in report.get("key_findings") or []:
            finding["statement"] = ctx.registry.ground_statement(subproblem_id, str(finding.get("statement", "")))
        ctx.registry.set_claims(subproblem_id, report.get("key_findings") or [])

    count = len(ctx.registry.for_subproblem(subproblem_id))
    update = {
        "solutions": {subproblem_id: {**report, "status": status, "error": error, "reviews": reviews}},
        "log": [f"solve {subproblem_id}: {status}, {count} results, {len(reviews)} reviews"],
    }
    if branch.get('experiment') or designed_here:
        # 写作阶段按实际求解的方案描述模型
        update["designs"] = {subproblem_id: design}
    return update


async def _solve_loop(ctx: GraphContext, branch: BranchInput, run_log: List[Dict], messages: List):
    """正式求解：CodeAct 循环 + 结果审查返工，返回 (status, error, report, reviews)"""
    subproblem_id = branch["subproblem"]["id"]
    recorder = HistoryRecorder()
    schema = submission_schema(ctx, branch, run_log)
    def inventory():
        return submission_inventory_issues(ctx, branch, run_log)
    agent = create_agent(
        model=ctx.coder_llm,
        tools=build_tools(ctx, subproblem_id, run_log, submission_gaps=inventory),
        system_prompt=SYSTEM,
        response_format=ToolStrategy(schema),
        middleware=[recorder, CompactHistoryMiddleware(), StepBudgetMiddleware(ctx.max_solver_steps, 4, REPORT_NUDGE,
                        report_ready=lambda: not inventory()),
                    DropRefusedStepsMiddleware(PLACEHOLDER_REPLY), TruncationGuardMiddleware(),
                    ReportOrderingMiddleware(schema.__name__, report_ready=lambda: not inventory())],
    )
    limit = ctx.max_solver_steps * 2 + 1
    fallback = ReportFallback(ctx.coder_llm, schema, SYSTEM, recorder, inventory)
    status, error = "completed", None
    report: Dict = {}
    reviews: List[Dict] = []
    structured = None
    result = None
    try:
        result = await run_agent(agent, messages, limit, fallback=fallback)
        structured = result.get("structured_response")
        for round_index in range(ctx.max_review_rounds + 1):
            if structured is None:
                break
            report = structured.model_dump()
            for finding in report.get("key_findings", []):
                finding["statement"] = ctx.registry.ground_statement(subproblem_id, str(finding["statement"]))
            if unbound_assumptions(subproblem_id, report["assumptions"], branch.get("research_contract", {})):
                break  # Register new premises before accepting/reviewing their conclusions.
            if not ctx.max_review_rounds:
                break
            subproblem = branch["subproblem"]
            fixes = [] if subproblem.get("kind") == "verify" else report_issues(
                ctx, report, subproblem_id, subproblem.get("depends_on") or [],
                figure_required=needs_figure(subproblem))
            prior = branch.get("review_findings", []) + [issue for r in reviews for issue in r["issues"]]
            try:
                review = await review_solution(ctx, {**branch, "review_findings": prior}, report, run_log)
            except Exception as exc:
                review = ResultReview(issues=[IssueReport(task_id=subproblem_id, category="service", message=f"局部审查未完成：{exc}")])
            review.issues = [IssueReport(task_id=subproblem_id, category="evidence", message=message) for message in fixes] + review.issues
            cited = {ref for i in review.issues for ref in i.evidence_refs}
            reviews.append(ReviewRecord(report_hash=report_fingerprint(report), issues=review.issues,
                evidence=[asdict(r) for r in ctx.registry.for_subproblem(subproblem_id) if r.id in cited]).model_dump())
            if not review.issues or round_index == ctx.max_review_rounds or any(i.category == "service" for i in review.issues):
                break
            await ctx.events.emit("paper_review", f"{subproblem_id} 审查发现 {len(review.issues)} 个问题，返工后重新核验")
            result = await run_agent(agent, result["messages"] + [HumanMessage(content=feedback_message(review))], limit, fallback=fallback)
            structured = result.get("structured_response")
            if structured is None:
                status, error = "incomplete", "审查返工未提交完整报告"
                break
        if not report or not ctx.registry.for_subproblem(subproblem_id):
            status, error = "incomplete", "没有提交完整报告或登记结果"
    except GraphRecursionError:
        status, error = "incomplete", f"超过 {ctx.max_solver_steps} 步仍未完成"
    except IncompleteSubmission as exc:
        status, error = "incomplete", str(exc)
    except Exception as exc:
        logger.exception("求解 %s 失败", subproblem_id)
        status, error = "failed", str(exc)
    if result is not None:
        save_transcript(ctx, subproblem_id, result["messages"])
    elif recorder.messages:
        save_transcript(ctx, subproblem_id, recorder.messages)
    return status, error, report, reviews


def save_transcript(ctx: GraphContext, name: str, messages: List) -> None:
    """求解对话落盘（.system/transcripts/），读 trace 时能看到每一步模型写了什么、调用了什么工具"""
    try:
        ctx.write_json(f".system/transcripts/{name}.json", messages_to_dict(messages))
    except Exception as exc:  # 只是排查用的记录，失败不影响求解
        logger.warning("保存 %s 的求解对话失败: %s", name, exc)
