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

from ai_system.research.domain import findings as research_findings

import json
import re
from typing import Dict, List

from langgraph.runtime import Runtime

from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import as_messages, call_structured, clip
from ai_system.graph.registry import ResultRegistry, find_refs
from ai_system.research.models import IssueReport
from ai_system.graph.state import ClaimAudit, ConsistencyReview, PaperState

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
    "区分实测数值差异与统计显著性：固定拆分上误差更低、相对均值基线改善，并不能单独支持统计显著。"
    "即使结论表的strength标为显著，仍要核查具体检验证据；不得把结果明确当作已做显著性检验。"
    "正文同时声明未做显著性检验却使用显著优于/显著改善时，须要求限定为该拆分上的实测差异。"
    "方法公式须对照实际核验的估计器设置：截距、是否惩罚截距、训练集中心化和标准化的拟合范围。"
    "明确的中心化方程加截距恢复，或含常数列且正确处理截距惩罚的方程均有效；不要要求固定符号写法。"
    "构造反例前先核对登记序列的对应关系：对象、日期和统计范围必须一致；逐日不等式须逐日比较，不能用某日最小值与全周均值比较来反证。"
    "若根因是数据、模型或假设错误，填写research_issue并引用登记证据和检查编号，进入研究核验；仅写作错误只填section和problem。"
    "补写没有证据支持的数值、夸大结论或遗漏适用条件，应按已有证据重写，不能为此重算原本正确的上游。"
    "research_issue只用于已有上游结果本身可能错误的情况；检查必须直接验证疑点，不能用目标值有限或其他无关检查代替。"
    "你是论文终审。下面给出主要结论、统一口径、已登记证据，以及保留原始引用的章节正文。"
    "正文引用对应的数值和含义以登记证据表为准。主要结论表只是关键结论摘录，不是证据全集；"
    "已登记的变量或辅助结果可以用于论证，不得因为它未被主要结论表列出就判为虚构。"
    "引用格式和编号存在性由程序检查，本次只审查语义矛盾，不要要求增加内部编号、照抄数值或逐句标注证据强度。"
    "逐句检查这些章节是否与结论表一致：结论方向、显著与否、强弱程度、适用范围是否被改写或夸大。"
    "只报告与结论表或统一口径实际矛盾的句子；一致的、或只是没有提到某条结论的都不要列入，措辞风格问题也不要报告。"
)

# 审查模型常把"核对过、一致"的句子也列成问题（v2k 的 7 条全是"……无矛盾"），每条都会触发一次无谓的章节修复
NOT_A_CONFLICT = re.compile(r"无矛盾|没有矛盾|不矛盾|不存在矛盾|并无矛盾")

# 需要与结论表保持一致的汇总性章节
AUDITED_SECTIONS = ("abstract", "analysis", "sensitivity", "evaluation")
AUDIT_SECTION_CHARS = 6000


def section_coverage(sections):
    rows={k:{'total_chars':len(v),'included_chars':min(len(v),AUDIT_SECTION_CHARS)}
        for k,v in sections.items() if k!='references' and v}
    return {'policy':'section-coverage-v1','limit':AUDIT_SECTION_CHARS,'sections':rows,
        'complete':all(r['total_chars']==r['included_chars'] for r in rows.values())}


class ReviewCoverageIncomplete(ValueError):pass


def require_complete_sections(sections):
    coverage=section_coverage(sections)
    if not coverage['complete']:raise ReviewCoverageIncomplete('章节超过6000字符，现有审查覆盖不完整；不能视为全文通过，请缩小审查范围或等待分段核查')


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


def reconcile_prompt(state,ctx):
    subproblems = "\n".join(f"- {sp['id']} {sp['title']}：{sp['question']}" for sp in state["analysis"]["subproblems"])
    contract = ctx.research.snapshot()
    return f"# 子问题\n{subproblems}\n\n# 结论表\n{ctx.registry.claims_table()}\n\n# 要求与假设\n{json.dumps({'requirements': contract['spec']['requirements'], 'assumptions': contract['assumptions']}, ensure_ascii=False)}"


async def reconcile(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    claims = ctx.registry.claims
    if not ctx.check_claims or len(claims) < 2:
        return {"consistency": [], "research_restart": False, "log": ["reconcile: skipped"]}
    await ctx.events.stage(f"审查 {len(claims)} 条结论的一致性")
    prompt = reconcile_prompt(state,ctx)
    try:
        from ai_system.research.review_cache import run_review
        def validate(result):
            if 'conflicts' not in result.model_fields_set:raise ValueError('结论审查没有明确返回conflicts字段')
            if any(not c.claims or not set(c.claims)<=set(claims) for c in result.conflicts):raise ValueError('结论审查引用了未知结论')
            validate_findings([c.research_issue for c in result.conflicts if c.research_issue],state,ctx)
        async def perform():return await call_structured(ctx.planner_llm, ConsistencyReview, as_messages(RECONCILE_SYSTEM, prompt),validate=validate)
        review = await run_review(ctx,state,'reconcile',ConsistencyReview,RECONCILE_SYSTEM,prompt,perform,validate=validate)
        conflicts = valid_conflicts([c.model_dump() for c in review.conflicts], set(claims))
    except Exception as exc:
        from ai_system.research.review_cache import error_code
        ctx.write_json(".system/consistency.json", {"error": error_code(exc), "conflicts": []})
        return {"consistency": [], "research_restart": True, "log": [f"reconcile: failed ({error_code(exc)})"]}
    research_reports = [IssueReport.model_validate(c["research_issue"]) for c in conflicts if c.get("research_issue")]
    if research_reports:
        active = ctx.research.report_issues(research_reports, "reconcile")
        if active:
            return {"consistency": [], "research_restart": True, "log": ["reconcile: research verification requested"]}
        # Closed research findings must not become writing instructions that
        # resurrect a dismissed suspicion as an established conclusion.
        conflicts = [c for c in conflicts if not c.get("research_issue")]
    ctx.write_json(".system/consistency.json", {"conflicts": conflicts})
    if ctx.research:
        ctx.research.save_consistency(conflicts)
    await ctx.events.emit(
        "paper_consistency",
        f"结论一致性：发现 {len(conflicts)} 处矛盾，已定统一口径" if conflicts else "结论一致性：未发现矛盾",
    )
    return {"consistency": conflicts, "research_restart": False, "log": [f"reconcile: {len(conflicts)} conflicts"]}


def section_audit_prompt(state,ctx,*,force=False):
    sections = state.get("sections") or {}
    keys = [k for k in sections if k!='references' and sections.get(k)]
    from ai_system.research.method_facts import method_context
    methods=method_context(getattr(ctx,'research',None))
    if not keys or not ctx.registry.claims and not methods and not force:return None
    body = "\n\n".join(
        f"## 章节 key: {k}\n{clip(sections[k], AUDIT_SECTION_CHARS)}" for k in keys
    )
    refs = set(find_refs(body)) | {ref for c in ctx.registry.claims.values() for ref in c.evidence}
    evidence = ResultRegistry(ctx.fs, results={rid: r for rid, r in ctx.registry.results.items() if rid in refs})
    prompt = (
        f"# 主要结论\n{ctx.registry.claims_table()}\n\n# 已登记证据（包含辅助结果）\n{evidence.to_prompt_table()}\n\n"
        f"# 统一口径\n{json.dumps(state.get('consistency') or [], ensure_ascii=False)}\n\n# 待检查的章节\n{body}\n\n"
        f"section 只能填这些 key：{json.dumps(keys)}\n"
        f"# 研究契约\n{json.dumps({'requirements': state['analysis']['requirements'], 'assumptions': state['analysis']['assumptions']}, ensure_ascii=False)}"
    )
    prompt+=methods
    return prompt,keys


def validate_findings(issues,state,ctx):
    tasks={s['id'] for s in state['analysis']['subproblems']}
    for issue in issues:
        if issue.task_id not in tasks or not set(issue.evidence_refs)<=set(ctx.registry.results):raise ValueError('审查问题超出冻结任务/证据范围')


async def audit_sections(state: PaperState, ctx: GraphContext,*,candidate=False) -> List[IssueReport]:
    """Completed results are reusable only for the same complete review context."""
    from ...research.segmented_review import needed
    segmented=needed(state.get('sections') or {})
    if segmented:keys=list(state['sections']);prompt=''
    else:
        prepared=section_audit_prompt(state,ctx,force=candidate)
        if prepared is None:return []
        prompt,keys=prepared
    try:
        from ai_system.research.review_cache import run_review
        def validate(result):
            if 'issues' not in result.model_fields_set:raise ValueError('章节审查没有明确返回issues字段')
            if any(i.section not in keys for i in result.issues):raise ValueError('章节审查引用了未提供的章节')
            validate_findings([i.research_issue for i in result.issues if i.research_issue],state,ctx)
        async def perform():
            return await call_structured(ctx.planner_llm,ClaimAudit,as_messages(AUDIT_SYSTEM,prompt),validate=validate,**({'retries':0} if candidate else {}))
        if segmented:
            from ai_system.research.segmented_review import audit as segmented_audit
            audit=await segmented_audit(ctx,state,candidate=candidate)
            keys=list(state['sections'])
        else:audit = await run_review(ctx,state,'sections',ClaimAudit,AUDIT_SYSTEM,prompt,perform,sections=state.get('sections') or {},extra={'non_blocking':True} if candidate else None,validate=validate)
    except Exception as exc:
        from ai_system.research.review_cache import error_code
        if ctx.research is not None and not candidate:
            failures=[i for i in research_findings(ctx.research.snapshot()) if i['source']=='review:sections' and i['category']=='service' and i['status'] not in {'resolved','dismissed','superseded'}]
            if failures:return []  # A persisted failure remains a publication blocker.
        return [IssueReport(task_id=state["analysis"]["subproblems"][0]["id"], category="service", message=f"章节审查未完成：{error_code(exc)}")]
    return [
        issue.research_issue or IssueReport(task_id=issue.section.split(":", 1)[1] if issue.section.startswith("solve:") else state["analysis"]["subproblems"][0]["id"],
                    category="writing", section=issue.section, message=f"与结论表不一致：{issue.problem}")
        for issue in audit.issues
        if issue.section in keys and issue.contradicts and not NOT_A_CONFLICT.search(issue.problem)
    ]
