"""
阶段 4：求解（每个子问题一个分支，按依赖关系分批并行）

每个分支是一个小的 CodeAct 循环：模型写代码 → 持久内核执行 → 看输出 → 继续，
关键数字和图通过 record_result / record_figure 登记，登记时校验数值确实来自某次执行的输出。
最后用结构化输出交一份求解报告，报告里的数字也只能写 {{R:Rn}} 引用。

正式求解之前先做执行驱动的方案搜索（explore.py）：评分靠前的几个候选各自试跑，按统一协议比较，
胜出分支的内核和对话历史直接接着做正式求解。
"""

import logging
from typing import Dict, List, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.runtime import Runtime

from ..compaction import CompactHistoryMiddleware, StepBudgetMiddleware
from ..context import GraphContext, list_data_files
from ..llm_utils import clip
from ..methods import describe
from ..registry import GroundingError
from ..state import BranchInput, Finding, SolveReport
from .explore import explore, promotion_message
from .review import feedback_message, review_solution

logger = logging.getLogger(__name__)

SYSTEM = """你是数学建模竞赛的编程手，负责把一个子问题的建模方案用 Python 实现并求出结果。

工作方式：
- 用 run_python 执行代码。内核是持久的：前面定义的变量、读入的数据后面都能直接用，不要每次重新读数据。
- 每次只写一小段代码，看到输出再决定下一步；出错就读回溯修正。
- 数据文件路径相对于工作目录，例如 data/xxx.csv。
- 画图用 matplotlib，画完不需要 savefig，系统会自动保存本次执行打开的图并告诉你文件名。
- 需要传给后续子问题的中间数据，保存到 outputs/intermediate/ 下（csv），并在报告里说明文件名。
- 默认使用题目给出的全部相关数据（例如全部比赛、全部年份），只用子集时要在报告里说明理由。
- 建模要防止数据泄漏：特征只能用预测时刻之前的信息；报告验证集指标时同时给出基准率或对照基线。
- 图中的标题、坐标轴、图例一律用中文，图例不要遮挡标题和数据。

结果登记（非常重要）：
- 论文里会用到的每个关键数字（参数估计、预测值、指标、最优解等），先 print 出来，再用 record_result 登记，run_id 填打印它的那次执行。
- 登记的值要和输出一致（可以四舍五入到合理的位数，百分数写成 87.3%）。没出现在输出里的值会被拒绝。
- 论文要用的图用 record_figure 登记，并写清图题。
- 至少完成方案中的检验或灵敏度分析，并登记相应结果。

完成后提交求解报告：概要、实际方法、主要结论、灵敏度分析和局限。报告里的数字一律写成登记返回的引用（如 {{R:R3}}），不要直接写数字。
每条主要结论都要写出依据的登记结果，并如实标注证据强度：检验不显著的就标「不显著」，不要把不显著的结果写成有效应。
这些结论会和其他子问题的结论放在一起做一致性审查。"""


REPORT_NUDGE = "求解已结束的话，请现在调用 SolveReport 提交求解报告（数字一律写成登记返回的引用）；还缺关键结果就先补齐再提交。"


def _dependency_text(dependencies: Dict[str, Dict], ctx: GraphContext) -> str:
    if not dependencies:
        return ""
    parts = ["# 前序子问题的结论（可直接使用其登记结果和中间数据）"]
    for dep_id, report in dependencies.items():
        parts.append(f"## {dep_id}\n{report.get('summary', '')}")
        parts.append(ctx.registry.to_prompt_table(dep_id))
    return "\n\n".join(parts)


def build_tools(
    ctx: GraphContext,
    subproblem_id: str,
    run_log: Optional[List[Dict]] = None,
    kernel_key: Optional[str] = None,
    record: bool = True,
) -> List[StructuredTool]:
    """record=False 时只给 run_python（探索阶段不登记结果）"""
    kernel = ctx.kernels.get(kernel_key or subproblem_id)
    run_log = run_log if run_log is not None else []

    async def run_python(code: str) -> str:
        """在持久 Python 内核中执行代码，返回输出、错误回溯和自动保存的图片文件名"""
        result = await kernel.execute(code)
        run_log.append({"run_id": result.run_id, "code": code, "ok": result.ok})
        await ctx.events.emit(
            "paper_code",
            f"{subproblem_id} · {result.run_id} · {'成功' if result.ok else '失败'}",
        )
        return clip(result.to_observation(), 12000)

    async def record_result(name: str, value: str, run_id: str, unit: str = "", description: str = "") -> str:
        """登记一个论文会用到的结果。value 必须出现在 run_id 那次执行的输出里。
        unit 只填物理或计数单位（如 点、秒、米、万元、%），p 值、AUC、系数、比例这类无量纲量留空。
        返回可在报告和论文中使用的引用"""
        try:
            result = ctx.registry.record_value(subproblem_id, name, value, run_id, unit, description)
        except GroundingError as exc:
            return f"登记失败：{exc}"
        return f"已登记，引用写作 {{{{R:{result.id}}}}}（{result.name} = {result.display()}）"

    async def record_figure(run_id: str, figure: str, caption: str) -> str:
        """登记一张论文要用的图。figure 是执行结果里给出的图片文件名，caption 是图题"""
        try:
            result = ctx.registry.record_figure(subproblem_id, run_id, figure, caption)
        except (GroundingError, FileNotFoundError, KeyError) as exc:
            return f"登记失败：{exc}"
        await ctx.events.file_changed(result.path)
        return f"已登记，引用写作 {{{{F:{result.id}}}}}（{result.name}）"

    tools = [StructuredTool.from_function(coroutine=run_python, name="run_python")]
    if record:
        tools += [
            StructuredTool.from_function(coroutine=record_result, name="record_result"),
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
    files = "\n".join(f"- {f}" for f in list_data_files(ctx.workspace)) or "（无）"
    return "\n\n".join(filter(None, [
        f"# 子问题 {subproblem['id']}：{subproblem['title']}\n{subproblem['question']}\n交付物：{'；'.join(subproblem.get('deliverables') or [])}",
        f"# 数据文件\n{files}\n\n# 数据概览\n{branch['data_profile']}",
        f"# 选定的建模方案：{chosen['name']}\n思路：{chosen['idea']}\n数学表述：{chosen['formulation']}\n"
        f"步骤：\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(chosen["steps"]))
        + f"\n检验：{chosen['validation']}",
        f"# 方法参考\n{describe(chosen.get('method_keys') or [])}",
        _dependency_text(branch.get("dependencies") or {}, ctx),
        extra,
        ending,
    ]))


async def run_agent(agent, messages: List, recursion_limit: int, nudge: str = REPORT_NUDGE):
    """跑一轮循环；模型用文字收尾、没交报告时提醒一次"""
    result = await agent.ainvoke({"messages": messages}, config={"recursion_limit": recursion_limit})
    if result.get("structured_response") is None:
        result = await agent.ainvoke(
            {"messages": result["messages"] + [HumanMessage(content=nudge)]},
            config={"recursion_limit": 11},
        )
    return result


async def solve(branch: BranchInput, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    subproblem_id = branch["subproblem"]["id"]
    await ctx.set_stage("solve", "in_progress")

    run_log: List[Dict] = []
    exploration = None
    design = branch["design"]
    messages = None
    try:
        if ctx.explore_top_k > 1:
            try:
                exploration = await explore(ctx, branch)
            except Exception as exc:  # 探索失败不阻塞，按 design 选中的方案直接求解
                logger.warning("探索 %s 失败，直接求解: %s", subproblem_id, exc)
        if exploration is not None:
            winner = exploration.winner
            design = {**design, "chosen": winner.candidate, "chosen_index": winner.candidate_index,
                      "exploration": exploration.to_record()}
            branch = {**branch, "design": design}
            run_log.extend(winner.run_log)
            if winner.report is not None:
                # 胜出分支连同对话历史继续，内核里的变量已经晋升到子问题名下
                messages = winner.messages + [HumanMessage(content=promotion_message(exploration))]
        await ctx.events.stage(f"{subproblem_id}：开始编码求解「{design['chosen']['name']}」")
        status, error, report, reviews = await _solve_loop(
            ctx, branch, run_log, messages or [HumanMessage(content=build_task(branch, ctx))]
        )
    finally:
        await ctx.kernels.release(subproblem_id)

    if not report:
        # 没交报告也保留已登记的结果，写作阶段仍可使用
        recorded = ctx.registry.for_subproblem(subproblem_id)
        report = SolveReport(
            summary="求解未正常结束，以下为已登记的结果。",
            approach=design["chosen"]["name"],
            key_findings=[Finding(statement=f"{r.name}：{{{{{r.id[0]}:{r.id}}}}}", strength="倾向") for r in recorded],
        ).model_dump()
    else:
        ctx.registry.set_claims(subproblem_id, report.get("key_findings") or [])

    count = len(ctx.registry.for_subproblem(subproblem_id))
    await ctx.events.emit("paper_solved", f"{subproblem_id} 求解{'完成' if status == 'completed' else '未完成'}，登记 {count} 项结果")
    update = {
        "solutions": {subproblem_id: {**report, "status": status, "error": error, "reviews": reviews}},
        "log": [f"solve {subproblem_id}: {status}, {count} results, {len(reviews)} reviews"],
    }
    if exploration is not None:
        # 写作阶段按实际求解的方案描述模型
        update["designs"] = {subproblem_id: design}
        update["log"].append(f"explore {subproblem_id}: -> {exploration.chosen_index} ({exploration.reason})")
    return update


async def _solve_loop(ctx: GraphContext, branch: BranchInput, run_log: List[Dict], messages: List):
    """正式求解：CodeAct 循环 + 结果审查返工，返回 (status, error, report, reviews)"""
    subproblem_id = branch["subproblem"]["id"]
    agent = create_agent(
        model=ctx.coder_llm,
        tools=build_tools(ctx, subproblem_id, run_log),
        system_prompt=SYSTEM,
        response_format=ToolStrategy(SolveReport),
        middleware=[CompactHistoryMiddleware(), StepBudgetMiddleware(ctx.max_solver_steps, 4, REPORT_NUDGE)],
    )
    limit = ctx.max_solver_steps * 2 + 1
    status, error = "completed", None
    report: Dict = {}
    reviews: List[Dict] = []
    try:
        result = await run_agent(agent, messages, limit)
        structured = result.get("structured_response")
        # 审查结果，必须修正的问题交回同一个循环（内核变量还在）
        for _ in range(ctx.max_review_rounds):
            if structured is None:
                break
            try:
                review = await review_solution(ctx, branch, structured.model_dump(), run_log)
            except Exception as exc:  # 审查失败不能丢掉已经交上来的报告
                logger.warning("审查 %s 失败，跳过审查: %s", subproblem_id, exc)
                break
            reviews.append(review.model_dump())
            if not review.must_fix:
                break
            await ctx.events.emit("paper_review", f"{subproblem_id} 审查发现 {len(review.must_fix)} 个必须修正的问题，返工中")
            result = await run_agent(agent, result["messages"] + [HumanMessage(content=feedback_message(review))], limit)
            structured = result.get("structured_response") or structured
        if structured is None:
            status = "incomplete"
        else:
            report = structured.model_dump()
    except GraphRecursionError:
        status, error = "incomplete", f"超过 {ctx.max_solver_steps} 步仍未完成"
    except Exception as exc:
        logger.exception("求解 %s 失败", subproblem_id)
        status, error = "failed", str(exc)
    return status, error, report, reviews


def ready_subproblems(state) -> List[Dict]:
    """依赖都已求解的子问题；有环或依赖不存在时把剩下的一起放出，避免卡死"""
    subproblems = state["analysis"]["subproblems"]
    solved = set(state.get("solutions") or {})
    known = {sp["id"] for sp in subproblems}
    pending = [sp for sp in subproblems if sp["id"] not in solved]
    ready = [
        sp for sp in pending
        if all(dep in solved or dep not in known or dep == sp["id"] for dep in sp.get("depends_on") or [])
    ]
    return ready or pending
