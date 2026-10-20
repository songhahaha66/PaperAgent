"""
求解结果审查（critic）：在写作之前检查结果本身靠不靠谱

求解循环只保证"数字确实是代码算出来的"，保证不了"算法用对了"。
2024_C 的 trace 里出现过：只用了一场比赛的数据、类别不平衡下用 F1 显得很高、训练/验证差距大。
这里让审查模型对照代码和登记结果逐项检查，有必须修正的问题就把意见交回同一个求解循环
（内核还在，变量不用重算），最多 max_review_rounds 轮。

模型主动提交标签置换对照时核对其执行来源和数值；是否需要这类实验取决于问题与冻结契约，
不能仅因报告出现 MAE、RMSE 等指标就给时序预测增加置换实验的硬性门槛。
"""

from ai_system.research.domain import findings as research_findings

import re
from typing import Dict, List, Optional

from ai_system.graph.context import GraphContext
from ai_system.graph.registry import GroundingError, find_refs
from ai_system.graph.llm_utils import as_messages, call_structured, clip
from ai_system.graph.state import BranchInput, ResultReview
from ai_system.graph.tracing import labelled
from ai_system.research.validation import requirement_evidence_gaps
from ai_system.research.models import DomainCheck

SYSTEM = (
    "你是数学建模竞赛的评审老师，负责在写论文之前审查一个子问题的求解结果。"
    "只依据给出的代码、输出和登记结果判断，不要臆测。只把真正影响结论可信度的问题列为必须修正，"
    "措辞风格、可有可无的扩展不要列入。"
    "以原题和冻结研究契约为验收边界，不增加题外实验或效果门槛。预测方法的局限、"
    "不同误差指标排序不同、按预先说明的规则选法、逐日预测碰巧同值，本身都不是交付缺失。"
    "检查真实的数据划分、预测时可用信息、代码计算与证据，以及结论是否如实说明局限。"
)

CHECKLIST = """逐项检查：
1. 数据范围：是否用了题目给出的全部相关数据（例如全部比赛、全部年份）；只用了子集时理由是否充分。
2. 数据泄漏与前视偏差：特征或标签是否用到了预测时刻之后的信息；标签是否由同一时刻的特征直接算出（如用动量变号定义转折、又把当期动量当特征）；训练/测试划分是否合理。
   窗口长度等超参数的选择也只能用训练数据；比较方法必须采用一致的固定起点或滚动更新协议。
3. 样本量与过拟合：样本是否太少；训练集与验证集指标差距是否过大。
4. 指标是否合适：类别不平衡时是否报告了 AUC、基准率或对照基线；回归是否有残差诊断。
5. 结论是否被结果支持：逐个核对引用编号对应的名称、值和单位，不能只因编号存在就通过；同值也不能拿无关指标作证据。
6. 交付物是否齐全：题目要求的每一项是否都有对应结果；要求完整序列时，标量均值不能代替序列。"""


def control_issues(ctx: GraphContext, report: Dict) -> List[str]:
    """Check declared controls' provenance and finite values, not scientific value."""
    import math
    issues=[]
    for control in report.get('controls') or []:
        fields=[('value','run_id'),('shuffled_value','shuffled_run_id')]
        if str(control.get('baseline_value') or '').strip():fields.append(('baseline_value','baseline_run_id'))
        for value_key,run_key in fields:
            try:
                value=str(control[value_key]).strip()
                number=float(value.rstrip('%').replace(',',''))
                if not math.isfinite(number):raise ValueError('对照值必须为有限数')
                ctx.registry.verify(value,control[run_key])
            except (KeyError,ValueError,GroundingError) as exc:
                issues.append(f"{control.get('metric','对照')} 的实际执行来源或数值无效：{exc}")
        if control.get('chance') not in (None,''):
            try:
                if not math.isfinite(float(str(control['chance']).rstrip('%'))):raise ValueError()
            except ValueError:issues.append('声明的参考水平必须为有限数；是否适用由研究判断。')
    return list(dict.fromkeys(issues))


def foreign_refs(ctx: GraphContext, report: Dict, subproblem_id: str, dependencies: List[str]) -> List[str]:
    """报告里引用了不存在的、或者不属于本子问题及其依赖的结果

    2024_C 的 Q3 一项结果都没登记，结论却写成"验证集 AUC 为 {{R:R3}}"，R3 是 Q1 登记的"验证集场数"。
    """
    allowed = {subproblem_id, *dependencies}
    texts = [report.get(key, "") for key in ("summary", "approach", "sensitivity")]
    texts += [*report.get("limitations", []), *(f.get("statement", "") for f in report.get("key_findings") or [])]
    bad = []
    for ref in find_refs(" ".join(texts)):
        result = ctx.registry.get(ref)
        if (result is None or result.subproblem not in allowed) and ref not in bad:
            bad.append(ref)
    if not bad:
        return []
    return [
        f"报告引用的 {', '.join(bad)} 不是本子问题登记的结果（不存在或属于其他子问题）。"
        "引用只能使用 record_results / record_figure 返回给你的编号，请先登记本问的结果再改写报告。"
    ]


def degenerate(report: Dict) -> bool:
    return not str(report.get('summary') or '').strip()


# 题目要求出图的子问题
VISUAL_TASK = re.compile(r"可视化|画图|绘制|作图|图表|图示|visuali", re.IGNORECASE)


def needs_figure(subproblem: Dict) -> bool:
    text = " ".join([subproblem.get("title", ""), subproblem.get("question", ""), *(subproblem.get("deliverables") or [])])
    return bool(VISUAL_TASK.search(text))


def submission_issues(
    ctx: GraphContext,
    report: Dict,
    subproblem_id: str,
    dependencies: Optional[List[str]] = None,
) -> List[str]:
    """报告的结构与引用检查；数值是否满足领域条件仍由提交器验收。"""
    return (["报告概要为空：请说明实际研究结果及边界。"] if degenerate(report) else []) + foreign_refs(ctx, report, subproblem_id, dependencies or [])


def inventory_presence(ctx,subproblem_id,figure_required=False):
    """One owner for the required result/figure inventory."""
    own=ctx.registry.for_subproblem(subproblem_id);issues=[]
    if not own:
        issues.append("还没有登记任何结果：请完成方案中的建模计算，print 关键数值，用 record_results 和 record_figure 登记后再提交报告。")
    if figure_required and not any(r.kind=='figure' for r in own):
        issues.append("题目要求可视化，但还没有登记任何图：请画出要求的图，用 record_figure 登记后在报告里引用。")
    return issues


def submission_inventory_issues(ctx: GraphContext, branch: BranchInput, runs: List[Dict]) -> List[str]:
    """Check required metrics and their actual executions, without judging thresholds."""
    sid = branch["subproblem"]["id"]
    own = ctx.registry.for_subproblem(sid)
    issues = inventory_presence(ctx,sid,needs_figure(branch['subproblem']))
    allowed = {sid, *(branch["subproblem"].get("depends_on") or [])}
    successful = {run["run_id"] for run in runs if run.get("ok")}
    for result in own:
        try:
            if result.run_id not in successful:
                raise GroundingError("来源不是本次任务的成功执行")
            if result.kind == "figure":
                info = ctx.fs.get_run_info(result.run_id)
                if not info or info.get("status") != "success":
                    raise GroundingError("图片来源不是成功执行")
                if not result.path or not ctx.fs.exists(result.path):
                    raise GroundingError("登记的图片文件不存在")
            else:
                ctx.registry.verify(result.value, result.run_id)
        except (GroundingError, OSError, ValueError) as exc:
            issues.append(f"登记结果 {result.id}「{result.name}」来源无效：{exc}；请重新执行并登记")
    for rule in (branch.get("research_contract") or {}).get("checks", []):
        check = rule["check"]
        for operand in DomainCheck.model_validate(check).selectors():
            selector = operand.model_dump()
            target = selector["task_id"]
            if branch["subproblem"].get("kind") == "verify" and target == check["left"]["task_id"]:
                target = sid
            found = [r for r in ctx.registry.results.values() if r.subproblem == target and r.name == selector["name"]]
            if target not in allowed or len(found) != 1:
                issues.append(f"检查 {check['id']} 缺少唯一登记指标 {target}「{selector['name']}」；请按契约原名计算、print并登记，不能改名或放宽检查")
    return list(dict.fromkeys(issues))


def report_submission_issues(ctx: GraphContext, branch: BranchInput, report: Dict, runs: List[Dict]) -> List[str]:
    subproblem = branch["subproblem"]
    sid = subproblem["id"]
    deps = subproblem.get("depends_on") or []
    issues = submission_issues(ctx, report, sid, deps)
    issues += submission_inventory_issues(ctx, branch, runs)
    issues += control_issues(ctx,report)
    available = {r.id for r in ctx.registry.results.values() if r.subproblem in {sid, *deps}}
    requirements = (branch.get("research_contract") or {}).get("requirements", [])
    issues += list(requirement_evidence_gaps([r["id"] for r in requirements], report.get("requirement_evidence", []), available).values())
    return list(dict.fromkeys(issues))


def report_issues(
    ctx: GraphContext, report: Dict, subproblem_id: str, dependencies: Optional[List[str]] = None,
    figure_required: bool = False,
) -> List[str]:
    return submission_issues(ctx, report, subproblem_id, dependencies) + inventory_presence(ctx,subproblem_id,figure_required) + control_issues(ctx, report)


def review_prompt(branch: BranchInput, report: Dict, ctx: GraphContext, runs: List[Dict]) -> str:
    subproblem = branch["subproblem"]
    code = "\n\n".join(
        f"## {r['run_id']}（{'成功' if r['ok'] else '失败'}）\n```python\n{r['code']}\n```\n"
        + ("执行输出：\n" + clip(ctx.fs.read_text(info['stdout']), 1500) if (info := ctx.fs.get_run_info(r['run_id'])) and info.get('stdout') else "")
        for r in runs
    )
    if len(code) > 48000:
        code = code[:30000] + "\n[中间执行记录因上下文预算省略；不能据未展示的计算确认正确]\n" + code[-18000:]
    return (
        f"# 原题（含题内数据）\n{branch['source']}\n\n"
        f"# 子问题 {subproblem['id']}：{subproblem['title']}\n{subproblem['question']}\n"
        f"交付物：{'；'.join(subproblem.get('deliverables') or [])}\n\n"
        f"# 数据概览\n{clip(branch['data_profile'], 4000)}\n\n"
        f"# 选定方案\n{branch['design']['chosen']['name']}：{branch['design']['chosen']['idea']}\n\n"
        f"# 求解报告\n{report}\n\n"
        f"# 登记结果\n{ctx.registry.to_prompt_table(subproblem['id'])}\n\n"
        f"# 本次执行代码与输出（按时间顺序，含最初建模计算）\n{code or '（无）'}\n\n{CHECKLIST}\n\n"
        f"# 研究契约\n{branch.get('research_contract', {})}\n"
        f"# 必须逐项重新核对的先前意见\n{branch.get('review_findings', [])}\n"
        "只返回仍未解决的问题，填写目标任务、实际证据引用、适用的check_ids和assumption_id。"
        "问题没有验证依据时保留疑点；不能仅因求解者称已修复就清空。全部有依据地解决后issues留空。"
    )


async def review_solution(ctx: GraphContext, branch: BranchInput, report: Dict, runs: List[Dict]) -> ResultReview:
    prompt = review_prompt(branch, report, ctx, runs)
    with labelled(f"{branch['subproblem']['id']}:review"):
        return await call_structured(ctx.planner_llm, ResultReview, as_messages(SYSTEM, prompt))


def feedback_message(review: ResultReview) -> str:
    items = "\n".join(f"{i + 1}. [{item.category}] {item.task_id}：{item.message}" for i, item in enumerate(review.issues))
    return (
        "评审老师审查了你的结果，以下问题必须修正：\n"
        f"{items}\n\n"
        "内核中的变量仍然存在。请补充或重做相应计算，登记新的结果，然后重新提交完整的求解报告。"
    )
