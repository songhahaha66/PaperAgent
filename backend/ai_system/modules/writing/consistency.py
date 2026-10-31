"""Optional semantic checks over current claims and manuscripts.

Review records retain actual scope and provenance. Findings and verdicts are
advisory; the research owner decides what to adopt, dismiss or retain as limits.
There is no mandatory reconcile/terminal-review production chain.
"""

from ai_system.research.domain import findings as research_findings

import json
import re
from typing import Dict, List

from langgraph.runtime import Runtime

from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import as_messages, call_structured
from ai_system.graph.registry import ResultRegistry, find_refs
from ai_system.research.models import IssueReport
from ai_system.graph.state import ClaimAudit, ConsistencyReview, PaperState

RECONCILE_SYSTEM = (
    "你是数学建模竞赛的评审老师。下面是一篇论文各子问题求解后登记的主要结论，"
    "数值已经替换成真实结果，证据强度由求解者标注。请找出：\n"
    "1. 不同子问题之间相互矛盾或口径不一的结论（例如一处检验认为某效应不显著，另一处却把它当作有效预测因素）；\n"
    "2. 证据强度与自身数值不符的结论（例如 p 值很大却标为显著，AUC 接近 0.5 却称预测有效）。\n"
    "对每个疑点说明实际依据与建议核查内容，不自动决定全文口径或改写事实。"
    "若发现上游模型、数据、假设错误，必须填写research_issue，引用实际登记证据与研究契约中的检查编号；不能靠统一措辞掩盖。"
    "只报告真实的矛盾，表述角度不同但不冲突的不要列入。"
)

AUDIT_SYSTEM = (
    "审查建议和统一口径不是实测证据，须用实际结果核对；建议与结果矛盾时不得要求正文服从建议。"
    "区分序列单调递增与增量递增，不能由前者推断后者；描述角度不同也不自动构成矛盾。"
    "区分实测数值差异与统计显著性：固定拆分上误差更低、相对均值基线改善，并不能单独支持统计显著。"
    "即使结论表的strength标为显著，仍要核查具体检验证据；不得把结果明确当作已做显著性检验。"
    "正文同时声明未做显著性检验却使用显著优于/显著改善时，须要求限定为该拆分上的实测差异。"
    "方法公式须对照实际核验的估计器设置：截距、是否惩罚截距、训练集中心化和标准化的拟合范围。"
    "明确的中心化方程加截距恢复，或含常数列且正确处理截距惩罚的方程均有效；不要要求固定符号写法。"
    "构造反例前先核对登记序列的对应关系：对象、日期和统计范围必须一致；逐日不等式须逐日比较，不能用某日最小值与全周均值比较来反证。"
    "若根因是数据、模型或假设错误，填写research_issue并说明实际代码、输出和文件依据，进入研究核验；仅写作错误只填section和problem。"
    "补写没有证据支持的数值、夸大结论或遗漏适用条件，应按已有证据重写，不能为此重算原本正确的上游。"
    "正文误解已有正确结果的因果关系也属于writing，例如把有限次模拟的随机误差归因于参数估计误差；"
    "应直接修正文稿解释，不用无关数值检查把措辞问题升级成模型错误。章节编号不是计算结果。"
    "research_issue只用于已有上游结果本身可能错误的情况；检查必须直接验证疑点，不能用目标值有限或其他无关检查代替。"
    "另一个例外是确实发现原题研究目的尚无实际产物回答：用research_issue.category=coverage说明缺少的原要求及实际依据，"
    "交回同一研究控制器；这不是已证明的上游错误，不虚构检查或阈值。仅文稿漏写已有结果时仍按writing处理，不要求新增实验。"
    "你是论文终审。下面给出主要结论、统一口径、已登记证据，以及保留原始引用的章节正文。"
    "正文的数值和含义以实际输出与结果文件为准。主要结论表只是关键结论摘录，不是证据全集；"
    "已登记的变量或辅助结果可以用于论证，不得因为它未被主要结论表列出就判为虚构。"
    "引用格式和编号存在性由程序检查，本次只审查语义矛盾，不要要求增加内部编号、照抄数值或逐句标注证据强度。"
    "围绕重要结论和已知矛盾核查这些章节：结论方向、显著与否、强弱程度、适用范围是否被改写或夸大。"
    "只报告与结论表或统一口径实际矛盾的句子；一致的、或只是没有提到某条结论的都不要列入，措辞风格问题也不要报告。"
)

# 审查模型常把"核对过、一致"的句子也列成问题（v2k 的 7 条全是"……无矛盾"），每条都会触发一次无谓的章节修复
NOT_A_CONFLICT = re.compile(r"无矛盾|没有矛盾|不矛盾|不存在矛盾|并无矛盾")

def section_coverage(sections):
    """Describe supplied source material; this is not proof of every sentence."""
    rows={k:{'total_chars':len(v),'included_chars':len(v)}
          for k,v in sections.items() if k!='references' and v}
    return {'policy':'key-claims-v1','full_document':False,'sections':rows,'complete':True}


class ReviewCoverageIncomplete(ValueError):pass


def execution_review_context(ctx):
    from ai_system.research.views import execution_context
    from ai_system.research.runs import run_context
    from services.file_services.workspace_layout import workspace_path
    runs=run_context(ctx.research)[-12:]
    for row in runs:
        for key,label in [('input_path','code'),('stdout_path','output')]:
            path=workspace_path(ctx.research.root,row[key]) if row.get(key) else None
            if path is not None and path.is_file():
                with path.open(encoding='utf-8',errors='replace') as stream:text=stream.read(6001)
                row[label]={'excerpt':text[:6000],'truncated':len(text)>6000}
    return json.dumps({'experiments':execution_context(ctx.research),'direct_runs':runs},ensure_ascii=False)


def reconcile_prompt(state,ctx):
    subproblems = "\n".join(f"- {sp['id']} {sp['title']}：{sp['question']}" for sp in state["analysis"]["subproblems"])
    contract = ctx.research.snapshot()
    records=execution_review_context(ctx)
    return f"# 实际执行输出、报告与结果文件\n{records}\n\n# 子问题\n{subproblems}\n\n# 结论表\n{ctx.registry.claims_table()}\n\n# 要求与假设\n{json.dumps({'requirements': contract['spec']['requirements'], 'assumptions': contract['assumptions']}, ensure_ascii=False)}"



def section_doubts(state, ctx):
    """Unverified chapter findings may be misclassified; proven faults may not."""
    store=getattr(ctx, 'research', None)
    if not store:return {}
    snapshot=store.snapshot()
    investigated={t.get('verifies_issue') for t in snapshot['experiments'].values()}
    doubts={i['id']:i for i in research_findings(snapshot)
            if i['source'] in {'sections','review-task:sections'} and i['section'] in (state.get('sections') or {})
            and i['category'] not in {'service','execution','coverage'}
            and (i['status'] in {'suspected','inconclusive'} or i['category']=='writing' and i['status']=='confirmed')
            and not i['repair_targets'] and i['id'] not in investigated
            and (i['task_id'] is None or snapshot['experiments'].get(i['task_id'],{}).get('execution_status')=='succeeded'
                 and snapshot['experiments'][i['task_id']]['validity']=='current')}
    generated={key:i['review_scope']['generated_by_review'] for key,i in doubts.items()
               if i['source']=='review-task:sections' and i.get('review_scope',{}).get('generated_by_review')}
    if generated:
        from contextlib import closing
        from ai_system.research.review_cache import review_context,science_version
        from ai_system.research.store import digest
        current=review_context(ctx,state,'sections','','',ClaimAudit,sections=state.get('sections') or {})
        with closing(store._connect()) as db:
            for key,review_id in generated.items():
                row=db.execute("SELECT state,payload FROM review_records WHERE research_id=? AND id=?",(store.research_id,review_id)).fetchone()
                if not row or row[0]!='completed':continue
                producer=json.loads(row[1]);origin=producer['context']
                # A finding is this completed review's output, not another input
                # requiring immediate recheck. New source/evidence or independent
                # doubts still change the normal material/request hashes.
                if (producer['stage']=='sections' and producer.get('result_hash')==digest(producer.get('result'))
                        and science_version(origin)==science_version(current)
                        and all(origin.get(k)==current.get(k) for k in
                                ('source_version','section_hashes','citation_sources','reconcile_projection_hash'))):
                    doubts.pop(key)
    return doubts


def section_audit_prompt(state,ctx,*,force=False):
    sections = state.get("sections") or {}
    keys = [k for k in sections if k!='references' and sections.get(k)]
    if not keys:return None
    applicable=bool(state.get('solutions') or ctx.registry.claims or force or state.get('consistency'))
    if not applicable and getattr(ctx,'research',None) is not None:
        applicable=any(i['category']=='writing' and i.get('section') in keys and
            i['status'] not in {'resolved','dismissed','superseded'} for i in research_findings(ctx.research.snapshot()))
    if not applicable:return None
    if state.get('source_version'):
        from services.chat_services.paper_project import source_prompt
        body = json.dumps(source_prompt(state), ensure_ascii=False)
    else:
        body = "\n\n".join(f"## 章节 key: {k}\n{sections[k]}" for k in keys)
    refs = (set(ctx.registry.results) if state.get('source_version') else set(find_refs(body))) | {ref for c in ctx.registry.claims.values() for ref in c.evidence}
    evidence = ResultRegistry(ctx.fs, results={rid: r for rid, r in ctx.registry.results.items() if rid in refs})
    records=execution_review_context(ctx)
    prompt = (
        f"# 实际执行输出、报告与结果文件\n{records}\n\n"
        f"# 主要结论\n{ctx.registry.claims_table()}\n\n# 已登记证据（包含辅助结果）\n{evidence.to_prompt_table()}\n\n"
        f"# 统一口径\n{json.dumps(state.get('consistency') or [], ensure_ascii=False)}\n\n# 待检查的章节\n{body}\n\n"
        f"section 只能填这些 key：{json.dumps(keys)}\n"
        "全文或文件级意见的 task_id 留空；只有明确关联所提供实验的意见才填写实验编号。\n"
        f"# 研究契约\n{json.dumps({'requirements': state['analysis']['requirements'], 'assumptions': state['analysis']['assumptions']}, ensure_ascii=False)}"
    )
    if state.get('source_version'):
        prompt += ('\n正文来自当前版本实际文件，section 填文件路径。核查完整原要求与实际实验依据。'
                   '有具体问题时 targets 选择提供的一个或多个片段标记，无需复制原文。'
                   '不能定位到片段时 targets 留空，保留为文件级意见，不因此否定意见。'
                   '不要推测 PDF 坐标与源码位置。\n# 项目依赖文本\n'+json.dumps(
                       {r['path']:r['text'] for r in state.get('source_files', [])
                        if r['text'] is not None and not r['path'].endswith('.tex')}, ensure_ascii=False))
    doubts=section_doubts(state,ctx)
    if doubts:
        prompt+='\n# 必须逐项重新核对的旧章节疑点\n'+json.dumps(list(doubts.values()),ensure_ascii=False)
        prompt+=('\n对每个旧疑点在issue_reviews填写issue_id、verdict和reason。仅文稿解释错误选writing，'
                 '上游结果本身确有疑点选research；这两种情况直接在本项 review.finding 给出具体问题，'
                 'writing的research_issue留空，research提供真实证据和可执行检查。'
                 'writing可以只是需要消除的误导解释，不要求属于严重矛盾；contradicts=false不否定明确的writing处理结论。'
                 '旧意见不成立或原文已有依据地纠正时选dismissed并说明理由，finding 为空。'
                 'issues 只登记本次新发现，不填写旧意见编号，不重复列出 finding。'
                 '不能省略旧意见或通过改稿掩盖已确认的计算错误。')
    return prompt,keys


def validate_findings(issues,state,ctx):
    tasks={s['id'] for s in state['analysis']['subproblems']}
    for issue in issues:
        if issue.task_id is not None and issue.task_id not in tasks or not set(issue.evidence_refs)<=set(ctx.research.snapshot()['artifacts']):raise ValueError('审查问题超出所提供的任务或执行记录范围')


async def audit_sections(state: PaperState, ctx: GraphContext,*,candidate=False,force=False) -> List[IssueReport]:
    """Completed results are reusable only for the same complete review context."""
    prepared=section_audit_prompt(state,ctx,force=candidate or force)
    if prepared is None:return []
    prompt,keys=prepared
    doubts=section_doubts(state,ctx)
    try:
        from ai_system.research.review_cache import run_review
        def validate(result):
            if 'issues' not in result.model_fields_set:raise ValueError('章节审查没有明确返回issues字段')
            all_findings = [*result.issues, *(r.finding for r in result.issue_reviews if r.finding)]
            if any(i.section not in keys for i in all_findings):raise ValueError('章节审查引用了未提供的章节')
            if state.get('source_version'):
                from services.chat_services.paper_project import source_targets
                targets=source_targets(state)
                for finding in all_findings:
                    if any(key not in targets or targets[key]['file']!=finding.section for key in finding.targets):
                        raise ValueError('targets 包含未提供或属于其他文件的源码片段；请选择本文件片段，不能定位时留空')
            validate_findings([i.research_issue for i in all_findings if i.research_issue],state,ctx)
            reviews={r.issue_id:r for r in result.issue_reviews}
            if len(reviews)!=len(result.issue_reviews) or set(reviews)!=set(doubts):raise ValueError('必须逐项核对给定的旧章节疑点')
            for key,review in reviews.items():
                if review.verdict=='dismissed':
                    if review.finding:raise ValueError('已撤销的旧疑点不能同时保留为问题')
                    continue
                finding=review.finding
                if not finding or finding.section!=doubts[key]['section']:raise ValueError('归类后须明确保留对应章节的问题')
                if (review.verdict=='research')!=bool(finding.research_issue):raise ValueError('意见归类与具体问题不一致')
        async def perform():
            return await call_structured(ctx.planner_llm,ClaimAudit,as_messages(AUDIT_SYSTEM,prompt),validate=validate,**({'retries':0} if candidate else {}))
        audit = await run_review(ctx,state,'sections',ClaimAudit,AUDIT_SYSTEM,prompt,perform,sections=state.get('sections') or {},extra={'non_blocking':True} if candidate else None,validate=validate)
    except Exception as exc:
        from ai_system.research.review_cache import error_detail
        if ctx.research is not None and not candidate:
            failures=[i for i in research_findings(ctx.research.snapshot()) if i['source']=='review:sections' and i['category']=='service' and i['status'] not in {'resolved','dismissed','superseded'}]
            if failures:return []  # A persisted failure remains a publication blocker.
        return [IssueReport(category="service", message=f"章节审查未完成：{error_detail(exc)}")]
    reports = [
        issue.research_issue or IssueReport(task_id=issue.section.split(":", 1)[1] if issue.section.startswith("solve:") else None,
                    category="writing", section=issue.section, message=f"与结论表不一致：{issue.problem}")
        for issue in audit.issues
        if issue.section in keys and ((issue.research_issue and issue.research_issue.category=='coverage') or
                                     issue.contradicts and not NOT_A_CONFLICT.search(issue.problem))
    ]
    # Verdicts about earlier doubts remain suggestions in the versioned review
    # record. The research owner explicitly accepts/dismisses them with reasons.
    reports.extend(review.finding.research_issue or IssueReport(task_id=doubts[review.issue_id]['task_id'],
        category='writing',section=review.finding.section,message=review.finding.problem)
        for review in audit.issue_reviews if review.finding)
    return reports
