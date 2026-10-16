"""
结论一致性（结论图）

结果登记表保证了"每个数字都是算出来的"，但保证不了"各处的结论说法一致"。
2024_C 的判分指出了两类问题：Q2 检验得出"不能拒绝纯随机"，Q1/Q3/Q5 却把动量当成可预测的状态；
摘要说 Q3 过拟合，正文说没有。

这里把每个子问题报告里的主要结论登记为结论节点（陈述 + 证据强度 + 指向登记结果的证据边），在两处检查：

1. reconcile（写作前）：审查模型通读全部结论，找出跨子问题的矛盾、以及强度与自身证据不符的结论，
   给出统一口径；写作时每一节都带着这份口径，从源头避免各写各的。
2. audit_sections（校验时）：检查摘要、问题分析、灵敏度分析、模型评价这些汇总性章节是否与结论表一致，
   不一致的作为校验问题交给修复环节，与数字校验走同一个修复回路。
"""

import json
import re
from typing import Dict, List

from langgraph.runtime import Runtime

from ..context import GraphContext
from ..llm_utils import as_messages, call_structured, clip
from ...research.models import IssueReport
from ..state import ClaimAudit, ConsistencyReview, PaperState

RECONCILE_SYSTEM = (
    "你是数学建模竞赛的评审老师。下面是一篇论文各子问题求解后登记的主要结论，"
    "数值已经替换成真实结果，证据强度由求解者标注。请找出：\n"
    "1. 不同子问题之间相互矛盾或口径不一的结论（例如一处检验认为某效应不显著，另一处却把它当作有效预测因素）；\n"
    "2. 证据强度与自身数值不符的结论（例如 p 值很大却标为显著，AUC 接近 0.5 却称预测有效）。\n"
    "对每个问题给出写作时统一采用的口径：怎样表述才能与全部证据一致。"
    "若发现上游模型、数据、假设错误，必须填写research_issue，引用实际登记证据与研究契约中的检查编号；不能靠统一措辞掩盖。"
    "只报告真实的矛盾，表述角度不同但不冲突的不要列入。"
)

AUDIT_SYSTEM = (
    "若根因是数据、模型或假设错误，填写research_issue并引用登记证据和检查编号，进入研究核验；仅写作错误只填section和problem。"
    "你是论文终审。下面给出全文统一的结论表和口径，以及几个汇总性章节的正文（引用已替换为数值）。"
    "逐句检查这些章节是否与结论表一致：结论方向、显著与否、强弱程度、适用范围是否被改写或夸大。"
    "只报告与结论表或统一口径实际矛盾的句子；一致的、或只是没有提到某条结论的都不要列入，措辞风格问题也不要报告。"
)

# 审查模型常把"核对过、一致"的句子也列成问题（v2k 的 7 条全是"……无矛盾"），每条都会触发一次无谓的章节修复
NOT_A_CONFLICT = re.compile(r"无矛盾|没有矛盾|不矛盾|不存在矛盾|并无矛盾")

# 需要与结论表保持一致的汇总性章节
AUDITED_SECTIONS = ("abstract", "analysis", "sensitivity", "evaluation")
AUDIT_SECTION_CHARS = 6000


def consistency_text(ctx: GraphContext, conflicts: List[Dict]) -> str:
    """给写作模型的统一口径"""
    if not ctx.registry.claims:
        return ""
    parts = [
        "# 全文结论表（各章节的结论必须与此一致，证据强度不能被改写）\n"
        "陈述里\"数值⟨Rn⟩\"表示这个数值对应引用 {{R:Rn}}；正文写引用，不要照抄数值，也不要把编号和数值错配。",
        ctx.registry.claims_table(),
    ]
    if conflicts:
        parts.append("# 统一口径（审查发现的矛盾及写法，必须遵守）")
        parts.extend(f"- {'、'.join(c['claims'])}：{c['issue']} → {c['resolution']}" for c in conflicts)
    return "\n\n".join(parts)


def valid_conflicts(conflicts: List[Dict], known: set) -> List[Dict]:
    """只保留指向已登记结论的冲突，编号写错的丢掉"""
    kept = []
    for conflict in conflicts:
        claims = [cid for cid in conflict.get("claims") or [] if cid in known]
        if claims and conflict.get("resolution"):
            kept.append({**conflict, "claims": claims})
    return kept


async def reconcile(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    claims = ctx.registry.claims
    if not ctx.check_claims or len(claims) < 2:
        return {"consistency": [], "research_restart": False, "log": ["reconcile: skipped"]}
    await ctx.events.stage(f"审查 {len(claims)} 条结论的一致性")
    subproblems = "\n".join(f"- {sp['id']} {sp['title']}：{sp['question']}" for sp in state["analysis"]["subproblems"])
    contract = ctx.research.snapshot()
    prompt = f"# 子问题\n{subproblems}\n\n# 结论表\n{ctx.registry.claims_table()}\n\n# 要求与假设\n{json.dumps({'requirements': contract['spec']['requirements'], 'assumptions': contract['assumptions']}, ensure_ascii=False)}"
    try:
        review = await call_structured(ctx.planner_llm, ConsistencyReview, as_messages(RECONCILE_SYSTEM, prompt))
        conflicts = valid_conflicts([c.model_dump() for c in review.conflicts], set(claims))
    except Exception as exc:
        ctx.write_json(".system/consistency.json", {"error": str(exc), "conflicts": []})
        ctx.research.report_issues([IssueReport(task_id=state["analysis"]["subproblems"][0]["id"],
            category="service", message=f"结论审查未完成：{exc}")], "reconcile")
        return {"consistency": [], "research_restart": True, "log": [f"reconcile: failed ({exc})"]}
    research_reports = [IssueReport.model_validate(c["research_issue"]) for c in conflicts if c.get("research_issue")]
    if research_reports:
        active = ctx.research.report_issues(research_reports, "reconcile")
        if active:
            return {"consistency": [], "research_restart": True, "log": ["reconcile: research verification requested"]}
        # Closed research findings must not become writing instructions that
        # resurrect a dismissed suspicion as an established conclusion.
        conflicts = [c for c in conflicts if not c.get("research_issue")]
    ctx.write_json(".system/consistency.json", {"conflicts": conflicts})
    await ctx.events.emit(
        "paper_consistency",
        f"结论一致性：发现 {len(conflicts)} 处矛盾，已定统一口径" if conflicts else "结论一致性：未发现矛盾",
    )
    return {"consistency": conflicts, "research_restart": False, "log": [f"reconcile: {len(conflicts)} conflicts"]}


async def audit_sections(state: PaperState, ctx: GraphContext) -> List[IssueReport]:
    """核对汇总性章节；审查服务失败时阻止交付，恢复后重试。"""
    sections = state.get("sections") or {}
    keys = [k for k in AUDITED_SECTIONS if sections.get(k)]
    if not ctx.registry.claims or not keys:
        return []
    body = "\n\n".join(
        f"## 章节 key: {k}\n{clip(ctx.registry.render(sections[k]), AUDIT_SECTION_CHARS)}" for k in keys
    )
    prompt = (
        f"{consistency_text(ctx, state.get('consistency') or [])}\n\n# 待检查的章节\n{body}\n\n"
        f"section 只能填这些 key：{json.dumps(keys)}\n"
        f"# 研究契约\n{json.dumps({'requirements': state['analysis']['requirements'], 'assumptions': state['analysis']['assumptions']}, ensure_ascii=False)}"
    )
    try:
        audit = await call_structured(ctx.planner_llm, ClaimAudit, as_messages(AUDIT_SYSTEM, prompt))
    except Exception as exc:
        return [IssueReport(task_id=state["analysis"]["subproblems"][0]["id"], category="service", message=f"章节审查未完成：{exc}")]
    return [
        issue.research_issue or IssueReport(task_id=issue.section.split(":", 1)[1] if issue.section.startswith("solve:") else state["analysis"]["subproblems"][0]["id"],
                    category="writing", section=issue.section, message=f"与结论表不一致：{issue.problem}")
        for issue in audit.issues
        if issue.section in keys and issue.contradicts and not NOT_A_CONFLICT.search(issue.problem)
    ]
