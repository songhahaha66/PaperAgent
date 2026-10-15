"""
阶段 4：求解（每个子问题一个分支，按依赖关系分批并行）

每个分支是一个小的 CodeAct 循环：模型写代码 → 持久内核执行 → 看输出 → 继续，
关键数字和图通过 record_results / record_figure 登记，登记时校验数值确实来自某次执行的输出。
最后用结构化输出交一份求解报告，报告里的数字也只能写 {{R:Rn}} 引用。

正式求解之前先做执行驱动的方案搜索（explore.py）：评分靠前的几个候选各自试跑，按统一协议比较，
胜出分支的内核和对话历史直接接着做正式求解。
"""

import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, messages_to_dict
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.runtime import Runtime

from ..compaction import (
    CompactHistoryMiddleware,
    HistoryRecorder,
    StepBudgetMiddleware,
    TruncationGuardMiddleware,
    compact_messages,
    drop_invalid_calls,
)
from ..context import GraphContext, list_data_files
from ..llm_utils import call_structured, clip
from ..methods import describe
from ..registry import GroundingError
from ..state import BranchInput, Finding, ResultItem, ResultReview, SolveReport
from ..tracing import labelled, set_label
from .explore import explore, is_hypothesis_test, promotion_message
from .review import (
    baseline_gaps,
    control_issues,
    degenerate,
    downgrade_findings,
    feedback_message,
    needs_figure,
    report_issues,
    review_solution,
)

logger = logging.getLogger(__name__)

SYSTEM = """你是数学建模竞赛的编程手，负责把一个子问题的建模方案用 Python 实现并求出结果。

工作方式：
- 用 run_python 执行代码。内核是持久的：前面定义的变量、读入的数据后面都能直接用，不要每次重新读数据。
- 每次写一段完整的代码（读数据、计算、打印关键结果可以放在一起），看到输出再决定下一步；出错就读回溯修正。
- 只 print 需要看的汇总结果，不要打印整张表或长数组（输出超过约 6000 字会被截断）。
- 数据文件路径相对于工作目录，例如 data/xxx.csv。
- 画图用 matplotlib，画完不需要 savefig，系统会自动保存本次执行打开的图并告诉你文件名。
- 需要传给后续子问题的中间数据，保存到 outputs/intermediate/ 下（csv），并在报告里说明文件名。
- 默认使用题目给出的全部相关数据（例如全部比赛、全部年份），只用子集时要在报告里说明理由。
- 建模要防止数据泄漏：特征只能用预测时刻之前的信息；报告验证集指标时同时给出基准率或对照基线。
- 报告预测效果（AUC、准确率、LogLoss、RMSE 等）时必须做标签置换对照：把原始结果列（如每一分的胜者）在构造任何特征和标签之前随机打乱，
  其余流程完全不变，重新算一遍同一指标；只打乱训练集标签不算，print 并登记真实值和打乱后的值，填进报告的 controls。打乱后指标仍明显好于无信息水平，说明存在泄漏。
  同一划分上还要算一个最简单的有信息基线（如按发球方区分的先验胜率、只用上一期值；常数预测是无信息水平，不算），一并登记填进 controls；
  模型没有超过这个基线时，结论只能说预测信息来自基线已有的结构。
  假设检验（自相关、游程、卡方等）不需要这种对照：检验的零分布本身就是对照，controls 留空。
- 图中的标题、坐标轴、图例一律用中文，图例不要遮挡标题和数据。

结果登记（非常重要）：
- 论文里会用到的每个关键数字（参数估计、预测值、指标、最优解等），先 print 出来，再用 record_results 登记，run_id 填打印它的那次执行。
- 把一批结果放进一次 record_results 调用里登记，不要一个结果调用一次。
- 登记的值要和输出一致（可以四舍五入到合理的位数，百分数写成 87.3%）。没出现在输出里的值会被拒绝。
- 论文要用的图用 record_figure 登记，并写清图题。
- 至少完成方案中的检验或灵敏度分析，并登记相应结果。

完成后提交求解报告：概要、实际方法、主要结论、灵敏度分析和局限。报告里的数字一律写成登记返回的引用（如 {{R:R3}}），不要直接写数字。
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
PLACEHOLDER_CODE = re.compile(r"""^\s*(?:print\(\s*(['"])[\w\s.!-]{0,12}\1\s*\)\s*;?\s*)+$""")
PLACEHOLDER_REPLY = (
    "这段代码只打印了一个固定字符串，没有执行。不需要用 run_python 占位或确认："
    "登记结果直接调用 record_results（run_id 填之前打印该值的那次执行），全部完成后直接提交 SolveReport。"
)


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
    runs = [r for r in run_log if r.get("ok") and not PLACEHOLDER_CODE.match(r.get("code") or "")]
    if not runs:
        return
    body = "\n\n".join(f"# ---- {r['run_id']} ----\n{r['code'].rstrip()}" for r in runs)
    header = f"# {subproblem_id} 成功执行过的代码（按执行顺序，含探索过程）。复用时复制需要的函数和处理步骤，不必整体运行。\n\n"
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


def _record_from_recent(ctx: GraphContext, subproblem_id: str, item: ResultItem, run_log: List[Dict], recent: int = 8):
    """run_id 核对不上时，到本问最近几次成功执行里找输出含这个值的那次"""
    for run in reversed([r for r in run_log if r.get("ok")][-recent:]):
        if run["run_id"] == item.run_id:
            continue
        try:
            return ctx.registry.record_value(subproblem_id, item.name, item.value, run["run_id"], item.unit, item.description)
        except GroundingError:
            continue
    return None


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
        code = strip_template_tokens(code)
        if PLACEHOLDER_CODE.match(code):
            return PLACEHOLDER_REPLY
        result = await kernel.execute(code)
        run_log.append({"run_id": result.run_id, "code": code, "ok": result.ok})
        await ctx.events.emit(
            "paper_code",
            f"{subproblem_id} · {result.run_id} · {'成功' if result.ok else '失败'}",
        )
        return clip(result.to_observation(), OBSERVATION_CHARS)

    async def record_results(items: List[ResultItem]) -> str:
        """一次登记多个论文会用到的结果（同一次或多次执行的结果放在一起登记，减少往返）。
        每个 value 必须出现在对应 run_id 那次执行的输出里，未通过核对的逐条说明原因，其余照常登记。
        返回可在报告和论文中使用的引用"""
        lines = []
        for item in items:
            item = item if isinstance(item, ResultItem) else ResultItem.model_validate(item)
            try:
                result = ctx.registry.record_value(
                    subproblem_id, item.name, item.value, item.run_id, item.unit, item.description
                )
                note = ""
            except GroundingError as exc:
                # 模型常把 run_id 的随机后缀记错（115040_8c4fb0 写成了 115040_4c80fb 这种）；
                # 值必须出现在某次真实执行输出里这一条不变，只是到本问最近的几次执行里找
                result = _record_from_recent(ctx, subproblem_id, item, run_log)
                if result is None:
                    lines.append(f"- {item.name}：登记失败，{exc}")
                    logger.info("%s 登记「%s」被拒：%s", subproblem_id, item.name, exc)
                    continue
                note = f"（{item.run_id} 核对不上，已按输出里含这个值的 {result.run_id} 登记）"
            lines.append(f"- {result.name} = {result.display()}，引用写作 {{{{R:{result.id}}}}}{note}")
        return "\n".join(lines) or "没有要登记的结果"

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


class ReportFallback(NamedTuple):
    """步数用完时直接要报告：用哪个模型、什么报告格式、什么系统提示，对话从哪份记录取"""

    llm: Any
    schema: Any
    system: str
    recorder: HistoryRecorder


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
        await ctx.events.emit("paper_solved", f"{subproblem_id} 为写作类交付物，写作阶段依据前序结论撰写")
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
            from .design import WRITING_DESIGN

            update["designs"] = {subproblem_id: {
                "methods": [], "candidates": [WRITING_DESIGN], "scores": [{"total": None}],
                "chosen": WRITING_DESIGN, "chosen_index": 0, "critic": None, "critic_error": None,
            }}
        return update

    run_log: List[Dict] = []
    exploration = None
    designed_here = branch.get("design") is None
    if designed_here:
        # 按需设计：轮到这个子问题时才设计，候选方案能参考已经求解完的前序结论
        from .design import make_design  # 避免循环导入

        record, _ = await make_design(ctx, branch)
        branch = {**branch, "design": record}
    design = branch["design"]
    messages = None
    try:
        if ctx.explore_top_k > 1:
            try:
                with labelled(f"{subproblem_id}:explore"):
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
        export_solution_code(ctx, subproblem_id, run_log)

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
    if exploration is not None or designed_here:
        # 写作阶段按实际求解的方案描述模型
        update["designs"] = {subproblem_id: design}
    if exploration is not None:
        update["log"].append(f"explore {subproblem_id}: -> {exploration.chosen_index} ({exploration.reason})")
    return update


async def _solve_loop(ctx: GraphContext, branch: BranchInput, run_log: List[Dict], messages: List):
    """正式求解：CodeAct 循环 + 结果审查返工，返回 (status, error, report, reviews)"""
    subproblem_id = branch["subproblem"]["id"]
    recorder = HistoryRecorder()
    agent = create_agent(
        model=ctx.coder_llm,
        tools=build_tools(ctx, subproblem_id, run_log),
        system_prompt=SYSTEM,
        response_format=ToolStrategy(SolveReport),
        middleware=[recorder, CompactHistoryMiddleware(), StepBudgetMiddleware(ctx.max_solver_steps, 4, REPORT_NUDGE),
                    TruncationGuardMiddleware()],
    )
    limit = ctx.max_solver_steps * 2 + 1
    fallback = ReportFallback(ctx.coder_llm, SolveReport, SYSTEM, recorder)
    status, error = "completed", None
    report: Dict = {}
    reviews: List[Dict] = []
    structured = None
    kept = None  # 最近一份有内容的报告
    dropped = False
    result = None
    try:
        result = await run_agent(agent, messages, limit, fallback=fallback)
        structured = result.get("structured_response")
        # 审查结果，必须修正的问题交回同一个循环（内核变量还在）。
        # 模型审查之后再多一轮只看确定性检查（登记、引用、置换对照）：返工时模型常常补了计算却忘了改报告
        rounds = ctx.max_review_rounds + 1 if ctx.max_review_rounds else 0
        for round_index in range(rounds):
            if structured is None:
                break
            current = structured.model_dump()
            subproblem = branch["subproblem"]
            fixes = report_issues(
                ctx, current, subproblem_id, subproblem.get("depends_on") or [],
                hypothesis=is_hypothesis_test(subproblem), figure_required=needs_figure(subproblem),
            )
            if round_index == ctx.max_review_rounds:
                if not fixes:
                    break
                review = ResultReview(sound=False, must_fix=fixes)
            else:
                gaps = [f"{gap}请把相应结论改写为“相对基线没有额外提升”并标为不显著，或者改进模型后重新评估。"
                        for gap in baseline_gaps(ctx, current)] if round_index == 0 else []
                try:
                    review = await review_solution(ctx, branch, current, run_log)
                except Exception as exc:  # 审查失败不能丢掉已经交上来的报告
                    logger.warning("审查 %s 失败，跳过审查: %s", subproblem_id, exc)
                    if not fixes and not gaps:
                        break
                    review = ResultReview(sound=False)
                if fixes or gaps:
                    review.must_fix = fixes + gaps + review.must_fix
                    review.sound = False
            reviews.append(review.model_dump())
            if not review.must_fix:
                break
            await ctx.events.emit("paper_review", f"{subproblem_id} 审查发现 {len(review.must_fix)} 个必须修正的问题，返工中")
            if not degenerate(current):
                kept = structured
            result = await run_agent(
                agent, result["messages"] + [HumanMessage(content=feedback_message(review))], limit, fallback=fallback
            )
            structured = result.get("structured_response") or structured
        if structured is not None and kept is not None and degenerate(structured.model_dump()):
            # 返工后交了空报告：退回返工前那份有内容的报告，没解决的审查意见写进局限
            structured = kept
            dropped = True
        if structured is None:
            status = "incomplete"
        elif not ctx.registry.for_subproblem(subproblem_id):
            # 返工后仍一项结果都没登记：报告里的数字和引用都不可信，丢掉报告，按未完成交给写作
            status, error = "incomplete", "没有登记任何结果"
        else:
            report = structured.model_dump()
            # 返工轮数用完仍未通过置换对照的、没有超过简单基线的，写进局限，写作时不能当作有效的预测结论
            leaks = control_issues(ctx, report)
            report["limitations"] = list(report.get("limitations") or []) + [
                f"预测效果没有通过对照检查，不能作为有效结论：{issue}" for issue in leaks
            ] + baseline_gaps(ctx, report)
            if dropped and reviews:
                report["limitations"].append("审查意见没有在返工中解决：" + "；".join(reviews[-1].get("must_fix") or []))
            if leaks:
                downgrade_findings(ctx, report)
    except GraphRecursionError:
        status, error = "incomplete", f"超过 {ctx.max_solver_steps} 步仍未完成"
    except Exception as exc:
        logger.exception("求解 %s 失败", subproblem_id)
        status, error = "failed", str(exc)
    if result is not None:
        save_transcript(ctx, subproblem_id, result["messages"])
    if not report and structured is not None and ctx.registry.for_subproblem(subproblem_id):
        # 返工超出步数或出错时保留返工前交的报告：v2h 的 Q2 登记了 20 项结果，返工超步数后整份报告被丢掉，结论表里没有 Q2
        report = structured.model_dump()
        report["limitations"] = list(report.get("limitations") or []) + [
            f"审查要求的返工没有完成（{error}），以下结论来自返工前的报告：" + "；".join((reviews[-1].get("must_fix") or [])[:3] if reviews else [])
        ]
    return status, error, report, reviews


def save_transcript(ctx: GraphContext, name: str, messages: List) -> None:
    """求解对话落盘（.system/transcripts/），读 trace 时能看到每一步模型写了什么、调用了什么工具"""
    try:
        ctx.write_json(f".system/transcripts/{name}.json", messages_to_dict(messages))
    except Exception as exc:  # 只是排查用的记录，失败不影响求解
        logger.warning("保存 %s 的求解对话失败: %s", name, exc)


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
