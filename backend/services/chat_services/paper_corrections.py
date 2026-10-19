"""Evidence-bound document corrections, without changing research or fitting."""
import json,re,uuid
from collections import Counter
from pydantic import BaseModel,Field,ConfigDict
from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import as_messages,call_structured,cap_output
from ai_system.graph.nodes.write import writing_style
from ai_system.graph.nodes.consistency import audit_sections,section_audit_prompt,AUDIT_SYSTEM
from ai_system.graph.nodes.validate import check_sections
from ai_system.graph.registry import REF_PATTERN,MATH_PATTERN,publication_text
from ai_system.graph.state import ClaimAudit
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict
from ai_system.research.control import refresh_registry
from ai_system.research.method_facts import verified_methods,canonical_method_block
from ai_system.research.review_cache import ReviewCache,review_context
from ai_system.research.citations import audit_citations,citation_issues
from ai_system.research.literature import CITATION
from ai_system.graph.tracing import labelled


class AnchoredEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    section:str
    old:str=Field(min_length=1,max_length=7000)
    replacement:str=Field(default='',max_length=7000)
    verified_method:bool=False


class CorrectionEdits(BaseModel):
    edits:list[AnchoredEdit]=Field(min_length=1,max_length=24)


def apply_edits(before,answer,blocks):
    grouped={k:[] for k in before}
    for edit in answer.edits:
        if edit.section not in before:raise ValueError('局部修改超出所选章节')
        original=before[edit.section]
        if original.count(edit.old)!=1:raise ValueError('每个修改片段必须在原文中唯一且逐字存在')
        if edit.verified_method and (edit.section not in blocks or edit.replacement):raise ValueError('方法修正只能插入该任务的项目核验说明')
        start=original.index(edit.old);end=start+len(edit.old)
        if any(start<e and end>s for s,e,_ in grouped[edit.section]):raise ValueError('修改片段不能重叠')
        grouped[edit.section].append((start,end,blocks[edit.section] if edit.verified_method else edit.replacement))
    after={}
    for key,edits in grouped.items():
        if not edits:raise ValueError('所选章节必须有明确局部修改')
        text=before[key]
        for start,end,replacement in sorted(edits,reverse=True):text=text[:start]+replacement+text[end:]
        after[key]=publication_text(text)
    return after


def guard_correction(before,after,canonical,latex):
    from .paper_editor import guard_wording
    if not canonical:
        return guard_wording(before,after,latex)
    if after.count(canonical)!=1:raise ValueError('方法修正须完整保留项目提供的已核验公式说明，且只出现一次')
    signature=lambda s:(Counter(REF_PATTERN.findall(s)),Counter(CITATION.findall(s)),
        Counter(re.findall(r'\d+(?:\.\d+)?',MATH_PATTERN.sub('',REF_PATTERN.sub('',CITATION.sub('',s))))))
    if signature(before)!=signature(after):raise ValueError('依据修正不能改变实验数字、证据或来源引用')
    if not after.strip() or len(after)>100000:raise ValueError('候选正文长度无效')
    from ai_system.export.latex import fragment_issues
    if latex and fragment_issues(after):raise ValueError('；'.join(fragment_issues(after)))


def proposal_context(store,state,llm):
    ctx=GraphContext.create(str(store.root),planner_llm=llm,output_mode='latex' if str(store.snapshot().get('paper_version','')).endswith('.tex') else 'markdown')
    ctx.research=store;refresh_registry(ctx)
    return ctx


def review_binding(store,state,llm):
    ctx=proposal_context(store,state,llm);prepared=section_audit_prompt(state,ctx)
    if prepared is None:raise ValueError('没有可执行的章节一致性核查')
    prompt,_=prepared
    return review_context(ctx,state,'sections',AUDIT_SYSTEM,prompt,ClaimAudit,sections=state['sections'],extra={'non_blocking':True})


def require_review(store,proposal,state):
    """Recheck the exact candidate review and provenance before publication."""
    from types import SimpleNamespace
    if digest(verified_methods(store))!=proposal['method_facts_hash']:raise ResearchConflict('已核验的方法设置已改变，须重新生成候选')
    context=review_binding(store,state,SimpleNamespace(model_name=proposal['review_model_id']))
    hit=ReviewCache(store).get('sections',context,ClaimAudit)
    if not hit or hit[1]['id']!=proposal['review_id'] or hit[1]['context_hash']!=proposal['review_context_hash'] or hit[0].issues:
        raise ResearchConflict('候选缺少同上下文的有效一致性审查，禁止应用')


async def propose_correction(store,keys,instruction,llm,contents=None):
    from .paper_editor import paper_state,source_info
    snapshot=store.refresh_inputs();filename,source=source_info(store,snapshot)
    if not llm:raise ValueError('依据修正需要实际写作与审查模型')
    if not store.delivery_ready(snapshot):raise ResearchConflict('研究未通过验收，不能以文稿修改代替研究修复')
    keys=list(dict.fromkeys(keys))
    if not 1<=len(keys)<=6 or any(k not in snapshot['sections'] or snapshot['sections'][k]['validity']!='current' or k=='references' for k in keys):raise ValueError('请选择一至六个当前正文章节')
    facts=verified_methods(store)
    if not facts:raise ValueError('当前研究没有可用于依据修正的项目核验方法记录，请使用研究修改')
    state=await paper_state(store);ctx=proposal_context(store,state,llm)
    before={k:snapshot['sections'][k]['text'] for k in keys}
    blocks={k:canonical_method_block(facts,k.split(':',1)[1]) for k in keys if k.startswith('solve:')}
    def validate(answer):
        after=apply_edits(before,answer,blocks)
        for k,text in after.items():guard_correction(before[k],text,blocks.get(k),filename.endswith('.tex'))
    prompt=('仅依据当前核验证据修正文稿，不运行实验、不修改模型、数据、拆分或实测结果。'
        '只返回精确定位的局部替换edits，不要输出完整章节。old必须是原文中唯一、连续、逐字存在的片段；'
        '同一章节的片段不得重叠。保留全部原数字、R/F/C引用及其出现次数。'
        '只修改所指出的错误，避免无关重写。实测误差较低不能写成统计显著，不得把未做检验改成已做检验。'
        '汇总章节保持原有数学表达不变。求解章节定位原OLS/Ridge公式及其变量定义的连续片段，'
        '设置verified_method=true、replacement为空，项目会插入该任务完整核验公式说明；不要重新生成该说明。'
        '保留RMSE等其他公式。一般措辞修正使用verified_method=false和replacement。不得编造系数。'
        '\n修改要求：'+instruction+'\n实际方法事实：'+json.dumps(facts,ensure_ascii=False)
        +'\n可插入已核验公式的章节：'+json.dumps(list(blocks),ensure_ascii=False)
        +'\n当前章节：'+json.dumps(before,ensure_ascii=False))
    answer=None
    if contents is None:
        with labelled('paper-edit:correction'):
            answer=await call_structured(cap_output(llm,3500),CorrectionEdits,as_messages(writing_style(ctx),prompt),validate=validate,retries=0)
        after=apply_edits(before,answer,blocks)
    else:
        if set(contents)!=set(keys):raise ValueError('手动候选必须与所选章节逐一对应')
        after={k:publication_text(v) for k,v in contents.items()}
        for k,text in after.items():guard_correction(before[k],text,blocks.get(k),filename.endswith('.tex'))
    checks=await audit_citations(store.root,list(after.values()),llm)
    combined={**state,'sections':{**state['sections'],**after}}
    if any(CITATION.search(t) for t in after.values()) and 'references' in combined['sections']:
        from ai_system.graph.nodes.write import reference_section
        combined['sections']['references']=reference_section(combined,ctx)
    issues=[{'section':k,'message':m} for k,t in combined['sections'].items() for m in citation_issues(store.root,t)]
    issues+=check_sections(combined,ctx)
    with labelled('paper-edit:audit'):
        issues+=[{'section':i.section,'message':i.message} for i in await audit_sections(combined,ctx,candidate=True)]
    context=review_binding(store,combined,llm);hit=ReviewCache(store).get('sections',context,ClaimAudit)
    if not hit:issues.append({'section':'','message':'章节审查未完成，不能应用候选'})
    if hit and hit[0].issues and not issues:issues.append({'section':'','message':'章节审查存在尚未解决的意见'})
    primary=keys[0]
    proposal={'id':uuid.uuid4().hex,'section':primary,'section_keys':keys,'edit_kind':'evidence','instruction':instruction,
        'before':before[primary],'after':after[primary],'before_sections':before,'after_sections':after,
        'generation_contract':'anchored-edits-v1' if answer else 'manual-evidence-v1','generation_edits':answer.model_dump()['edits'] if answer else [],
        'base_commit':snapshot['commit_seq'],'source_hash':digest(source),'filename':filename,'status':'pending',
        'section_revision':snapshot['sections'][primary]['revision'],'citation_checks':checks,
        'citation_issues':list(dict.fromkeys(m for t in after.values() for m in citation_issues(store.root,t))),
        'review_issues':issues,'method_facts':facts,'method_facts_hash':digest(facts),
        'review_id':hit[1]['id'] if hit else None,'review_context_hash':digest(context),
        'review_model_id':getattr(llm,'model_name',None) or getattr(llm,'model',None)}
    with store._transaction() as db:
        if store._read(db)['commit_seq']!=snapshot['commit_seq']:raise ResearchConflict('研究已改变，原稿保留，请刷新后再生成候选')
        db.execute('INSERT INTO commands VALUES (?,?,?,?)',(store.research_id,'paper-edit:'+proposal['id'],digest(proposal),json.dumps(proposal,ensure_ascii=False)))
    return proposal
