"""Evidence-bound document corrections, without changing research or fitting."""
import json,re,uuid,time
from typing import Literal
from collections import Counter
from pydantic import BaseModel,Field,ConfigDict
from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import as_messages,call_structured,cap_output
from ai_system.modules.writing.consistency import audit_sections,section_audit_prompt,AUDIT_SYSTEM
from ai_system.modules.writing.validation import check_sections
from ai_system.graph.registry import REF_PATTERN,MATH_PATTERN,publication_text
from ai_system.graph.state import ClaimAudit
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict
from ai_system.research.control import refresh_registry
from ai_system.research.method_facts import verified_methods,canonical_method_block
from ai_system.research.review_cache import ReviewCache,review_context,run_review
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


def unique_span(text,old):
    start=text.find(old)
    if start<0 or text.find(old,start+1)>=0:raise ValueError('原文片段必须存在且唯一（包括重叠匹配），请增加上下文消除歧义')
    return start,start+len(old)


def apply_edits(before,answer,blocks,*,normalize=True):
    grouped={k:[] for k in before}
    for edit in answer.edits:
        if edit.section not in before:raise ValueError('局部修改超出所选章节')
        original=before[edit.section]
        start,end=unique_span(original,edit.old)
        if edit.verified_method and (edit.section not in blocks or edit.replacement):raise ValueError('方法修正只能插入该任务的项目核验说明')
        if any(start<e and end>s for s,e,_ in grouped[edit.section]):raise ValueError('修改片段不能重叠')
        grouped[edit.section].append((start,end,blocks[edit.section] if edit.verified_method else edit.replacement))
    after={}
    for key,edits in grouped.items():
        if not edits:raise ValueError('所选章节必须有明确局部修改')
        text=before[key]
        for start,end,replacement in sorted(edits,reverse=True):text=text[:start]+replacement+text[end:]
        after[key]=publication_text(text) if normalize else text
    return after


class FragmentScope(BaseModel):
    model_config=ConfigDict(extra='forbid')
    section_revision:int=Field(ge=1)
    section_hash:str=Field(min_length=64,max_length=64)
    source_hash:str=Field(min_length=64,max_length=64)
    old:str=Field(min_length=1,max_length=600)


class FragmentReplacement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    replacement:str=Field(min_length=1,max_length=900)


class FragmentAudit(BaseModel):
    verdict:Literal['equivalent','changed','uncertain']
    reason:str=Field(min_length=1,max_length=240)


FRAGMENT_AUDIT_SYSTEM=('审查一次等义润色。只判断所选原文与替换文字是否保持事实、适用范围、结论强度和逻辑，且与邻近语境一致。'
    '程序另行保护全文数字、公式、引用和片段外原文不变。不要重审未修改全文或扩展实验。'
    '只要新增/遗漏主张、强度变化或语义改变，返回changed；无法确定则uncertain；确为等义才equivalent。reason简短，不超过240字符。')


def fragment_prompt(store,state,scope):
    before=store.snapshot()['sections'][scope['section']]['text'];after=state['sections'][scope['section']]
    start,end=scope['start'],scope['end'];replacement=after[start:len(after)-len(before)+end]
    if before[start:end]!=scope['old'] or after!=before[:start]+replacement+before[end:]:raise ResearchConflict('局部审查输入包含范围外变化')
    return json.dumps({'before':scope['old'],'replacement':replacement,'left_context':before[max(0,start-180):start],
        'right_context':before[end:end+180]},ensure_ascii=False)


class PatchFailure(ValueError):
    def __init__(self,attempt_id,reason,scope,recovery_allowed):
        super().__init__('局部提案未完成，原稿保留；不得原样重试。' if reason=='truncated' else '局部提案未通过契约，原稿保留。')
        self.detail={'message':str(self),'attempt_id':attempt_id,'reason':reason,'scope':scope,'recovery_allowed':recovery_allowed}


def resolve_scope(key,section,source,request):
    scope=FragmentScope.model_validate(request)
    if scope.section_revision!=section['revision'] or scope.section_hash!=section['content_hash'] or scope.source_hash!=digest(source):
        raise ResearchConflict('原文版本或哈希已变化，请刷新并重新选择片段')
    original=section['text']
    start,end=unique_span(original,scope.old)
    for pattern in (MATH_PATTERN,REF_PATTERN,CITATION):
        if any(m.start()<start<m.end() or m.start()<end<m.end() for m in pattern.finditer(original)):
            raise ValueError('片段边界不能截断公式或引用')
    result={'section':key,'section_revision':section['revision'],'section_hash':section['content_hash'],'source_hash':digest(source),
        'start':start,'end':end,'old':scope.old,'old_hash':digest(scope.old)}
    result['id']=digest(result)
    return result


def patch_attempt(store,scope,recovery_of=None):
    """Durable stop/once-only recovery; no repeat send for a truncated scope."""
    with store._transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS paper_patch_attempts(id TEXT PRIMARY KEY,research_id TEXT NOT NULL,payload TEXT NOT NULL)')
        rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM paper_patch_attempts WHERE research_id=?',(store.research_id,))]
        parent=next((r for r in rows if r['id']==recovery_of),None) if recovery_of else None
        if recovery_of:
            if not parent or parent['state']!='truncated' or parent.get('recovery_of') or parent.get('recovery_id'):raise ValueError('只能恢复一次明确截断的局部请求')
            old=parent['scope']
            if any(old[k]!=scope[k] for k in ('section','section_revision','section_hash','source_hash')) or not old['start']<=scope['start']<scope['end']<=old['end'] or len(scope['old'])>len(old['old'])//2:
                raise ValueError('恢复必须在同版本原片段内实质缩小到不超过一半，不能原样重试')
        elif any(r['scope']['id']==scope['id'] or r['state']=='truncated' and all(r['scope'][k]==scope[k] for k in ('section','section_hash','source_hash'))
                 and r['scope']['start']<=scope['start']<scope['end']<=r['scope']['end'] for r in rows):
            raise ValueError('该版本片段已经尝试过；请查看候选/失败记录，截断后仅可绑定原请求缩小一次')
        record={'id':uuid.uuid4().hex,'scope':scope,'state':'running','started_at':time.time(),'recovery_of':recovery_of}
        if parent:
            parent['recovery_id']=record['id'];db.execute('UPDATE paper_patch_attempts SET payload=? WHERE id=?',(json.dumps(parent,ensure_ascii=False),parent['id']))
        db.execute('INSERT INTO paper_patch_attempts VALUES (?,?,?)',(record['id'],store.research_id,json.dumps(record,ensure_ascii=False)))
    return record


def finish_patch(store,record,state,**fields):
    record.update(state=state,**fields,updated_at=time.time())
    with store._transaction() as db:db.execute('UPDATE paper_patch_attempts SET payload=? WHERE id=?',(json.dumps(record,ensure_ascii=False),record['id']))


async def generate_fragment(store,key,section,source,instruction,llm,request,recovery_of=None):
    from .paper_editor import guard_wording
    from ai_system.research.runtime import ResearchRuntime
    scope=resolve_scope(key,section,source,request)
    if len(instruction)>2000:raise ValueError('局部改写要求最多2000字符，请明确一个小范围修改')
    budget=ResearchRuntime(store.root,store.research_id).status()
    if budget.get('request_limit') is not None and budget['request_limit']-budget['used']<2:raise ValueError('局部改写至少需要保留生成与审查两次请求；原稿未改变')
    record=patch_attempt(store,scope,recovery_of)
    system='只润色给定的一个短片段。保持事实、结论强度、数字、公式和引用逐字不变。不要扩展背景或解释，只返回replacement字段，不返回原文、定位或其他章节。'
    payload={'instruction':instruction,'fragment':scope['old']}
    diagnostics={'input_chars':len(system)+len(json.dumps(payload,ensure_ascii=False)),'output_limit':2000}
    def validate(reply):
        if len(reply.replacement)>min(900,max(80,len(scope['old'])*2)):raise ValueError('替换超出局部长度范围')
        if reply.replacement==scope['old']:raise ValueError('模型未提出实际修改')
        guard_wording(scope['old'],reply.replacement,False)
        if Counter(CITATION.findall(scope['old']))!=Counter(CITATION.findall(reply.replacement)):raise ValueError('局部改写不得替换文献标记')
    try:
        with labelled('paper-edit:fragment'):
            answer=await call_structured(cap_output(llm,2000),FragmentReplacement,as_messages(system,json.dumps(payload,ensure_ascii=False)),validate=validate,retries=0)
        edits=CorrectionEdits(edits=[AnchoredEdit(section=key,old=scope['old'],replacement=answer.replacement)])
        after=apply_edits({key:section['text']},edits,{},normalize=False)
        assert after[key]==section['text'][:scope['start']]+answer.replacement+section['text'][scope['end']:]
        finish_patch(store,record,'generated',replacement_hash=digest(answer.replacement),**diagnostics)
        return after,edits,scope,record
    except Exception as exc:
        truncated=getattr(exc,'truncated',False) or getattr(exc.__cause__,'truncated',False)
        finish_patch(store,record,'truncated' if truncated else 'failed',error_code=type(exc).__name__,**diagnostics)
        raise PatchFailure(record['id'],'truncated' if truncated else 'contract_failed',scope,truncated and not recovery_of) from None


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


def review_binding(store,state,llm,scope=None,source_issue=None):
    if source_issue:
        from .issue_edits import review_context as issue_review_context
        return issue_review_context(store,state,llm,source_issue)
    ctx=proposal_context(store,state,llm)
    if scope:
        return review_context(ctx,state,'fragment',FRAGMENT_AUDIT_SYSTEM,fragment_prompt(store,state,scope),FragmentAudit,
            sections=state['sections'],extra={'non_blocking':True,'scope':scope,'policy':'exact-fragment-equivalence-v1'})
    prepared=section_audit_prompt(state,ctx,force=True)
    if prepared is None:raise ValueError('没有可执行的章节一致性核查')
    prompt,_=prepared
    return review_context(ctx,state,'sections',AUDIT_SYSTEM,prompt,ClaimAudit,sections=state['sections'],extra={'non_blocking':True})


def require_review(store,proposal,state):
    """Recheck the exact candidate review and provenance before publication."""
    from types import SimpleNamespace
    if digest(verified_methods(store))!=proposal['method_facts_hash']:raise ResearchConflict('已核验的方法设置已改变，须重新生成候选')
    if proposal.get('source_issue'):
        from .issue_edits import require_review as require_issue_review
        return require_issue_review(store,proposal,state)
    scope=proposal['scope'] if proposal.get('review_kind')=='fragment_equivalence' else None
    context=review_binding(store,state,SimpleNamespace(model_name=proposal['review_model_id']),scope)
    hit=ReviewCache(store).get('fragment' if scope else 'sections',context,FragmentAudit if scope else ClaimAudit)
    if not hit or hit[1]['id']!=proposal['review_id'] or hit[1]['context_hash']!=proposal['review_context_hash'] or (hit[0].verdict!='equivalent' if scope else bool(hit[0].issues)):
        raise ResearchConflict('候选缺少同上下文的有效一致性审查，禁止应用')


async def review_candidate(store,state,llm,scope=None,source_issue=None):
    if source_issue:
        from .issue_edits import review
        return await review(store,state,llm,source_issue)
    ctx=proposal_context(store,state,llm);issues=[]
    if scope:
        prompt=fragment_prompt(store,state,scope)
        async def perform():return await call_structured(cap_output(llm,1800),FragmentAudit,as_messages(FRAGMENT_AUDIT_SYSTEM,prompt),retries=0)
        try:
            with labelled('paper-edit:fragment-audit'):
                answer=await run_review(ctx,state,'fragment',FragmentAudit,FRAGMENT_AUDIT_SYSTEM,prompt,perform,sections=state['sections'],
                    extra={'non_blocking':True,'scope':scope,'policy':'exact-fragment-equivalence-v1'})
            if answer.verdict!='equivalent':issues.append({'section':scope['section'],'message':'局部等义性未通过：'+answer.reason})
        except Exception as exc:issues.append({'section':scope['section'],'message':'局部等义性审查未完成：'+type(exc).__name__})
    else:
        with labelled('paper-edit:audit'):
            issues=[{'section':i.section,'message':i.message} for i in await audit_sections(state,ctx,candidate=True)]
    context=review_binding(store,state,llm,scope)
    hit=ReviewCache(store).get('fragment' if scope else 'sections',context,FragmentAudit if scope else ClaimAudit)
    if not hit:issues.append({'section':scope['section'] if scope else '','message':'审查没有有效完成记录，不能应用候选'})
    if hit and not scope and hit[0].issues and not issues:issues.append({'section':'','message':'章节审查存在尚未解决的意见'})
    return issues,hit,context


def require_scope(store,proposal,snapshot,source):
    saved=proposal['scope'];key=proposal['section']
    current=resolve_scope(key,snapshot['sections'][key],source,{k:saved[k] for k in ('section_revision','section_hash','source_hash','old')})
    if current!=saved or proposal.get('origin')!='model':raise ResearchConflict('局部提案的定位或版本哈希不匹配')
    answer=CorrectionEdits.model_validate({'edits':proposal['generation_edits']})
    if len(answer.edits)!=1 or answer.edits[0].old!=saved['old'] or answer.edits[0].section!=key or answer.edits[0].verified_method:
        raise ResearchConflict('局部提案超出已绑定的片段范围')
    expected=apply_edits({key:snapshot['sections'][key]['text']},answer,{},normalize=False)
    if proposal.get('after_sections')!=expected:raise ResearchConflict('候选包含未选择片段的改动')
    with store._connect() as db:
        row=db.execute('SELECT payload FROM paper_patch_attempts WHERE id=? AND research_id=?',(proposal['patch_attempt_id'],store.research_id)).fetchone()
    record=json.loads(row[0]) if row else {}
    if record.get('state')!='proposed' or record.get('proposal_id')!=proposal['id'] or record.get('scope')!=saved or record.get('replacement_hash')!=digest(answer.edits[0].replacement):
        raise ResearchConflict('缺少对应的成功模型局部提案记录')


async def propose_correction(store,keys,instruction,llm,contents=None,*,fragment_scope=None,recovery_of=None,wording=False,source_issue=None):
    from .paper_editor import paper_state,source_info
    snapshot=store.refresh_inputs();filename,source=source_info(store,snapshot)
    if not llm:raise ValueError('依据修正需要实际写作与审查模型')
    if source_issue:
        from .issue_edits import require_binding,ready
        linked=require_binding(store,source_issue)
        if keys!=[linked['section']]:raise ResearchConflict('该候选只能修改源意见对应章节')
        if not ready(store):raise ResearchConflict('研究或服务问题尚未通过核查，不能以文稿修改绕过')
        from ai_system.research.runtime import ResearchRuntime
        budget=ResearchRuntime(store.root,store.research_id).status()
        if (budget.get('request_limit') is not None and budget['used']>=budget['request_limit']) or budget['state']=='provider_blocked':raise ResearchConflict('请求预算或网关不可用，意见与原稿保留')
    elif not store.delivery_ready(snapshot):raise ResearchConflict('研究未通过验收，不能以文稿修改代替研究修复')
    keys=list(dict.fromkeys(keys))
    if not 1<=len(keys)<=6 or any(k not in snapshot['sections'] or snapshot['sections'][k]['validity']!='current' or k=='references' for k in keys):raise ValueError('请选择一至六个当前正文章节')
    facts=verified_methods(store)
    if not facts and not wording:raise ValueError('当前研究没有可用于依据修正的项目核验方法记录，请使用研究修改')
    state=await paper_state(store);ctx=proposal_context(store,state,llm)
    before={k:snapshot['sections'][k]['text'] for k in keys}
    blocks={k:canonical_method_block(facts,k.split(':',1)[1]) for k in keys if k.startswith('solve:') and not wording}
    answer=None;scope=None;attempt=None
    if contents is None:
        if not fragment_scope or len(keys)!=1 or not wording:raise ValueError('自动改写仅支持一个明确选择的短片段；多节或公式修正请直接编辑候选并审查')
        after,answer,scope,attempt=await generate_fragment(store,keys[0],snapshot['sections'][keys[0]],source,instruction,llm,fragment_scope,recovery_of)
        guard_correction(before[keys[0]],after[keys[0]],None,filename.endswith('.tex'))
    else:
        if set(contents)!=set(keys):raise ValueError('手动候选必须与所选章节逐一对应')
        after={k:publication_text(v) for k,v in contents.items()}
        for k,text in after.items():guard_correction(before[k],text,blocks.get(k),filename.endswith('.tex'))
    checks=await audit_citations(store.root,list(after.values()),llm)
    combined={**state,'sections':{**state['sections'],**after}}
    if any(CITATION.search(t) for t in after.values()) and 'references' in combined['sections']:
        from ai_system.modules.writing.sections import reference_section
        combined['sections']['references']=reference_section(combined,ctx)
    issues=[{'section':k,'message':m} for k,t in combined['sections'].items() for m in citation_issues(store.root,t)]
    issues+=check_sections(combined,ctx)
    context=review_binding(store,combined,llm,scope,source_issue)
    primary=keys[0]
    proposal={'id':uuid.uuid4().hex,'section':primary,'section_keys':keys,'edit_kind':'wording' if wording else 'evidence','instruction':instruction,
        'before':before[primary],'after':after[primary],'before_sections':before,'after_sections':after,
        'generation_contract':'scoped-anchored-edits-v2' if answer else 'manual-evidence-v1','generation_edits':answer.model_dump()['edits'] if answer else [],
        'scope':scope,'patch_attempt_id':attempt['id'] if attempt else None,'origin':'model' if answer else 'manual',
        'review_kind':'issue_section' if source_issue else 'fragment_equivalence' if scope else 'full_document',
        **({'source_issue':source_issue,'source_issue_binding':linked['binding']} if source_issue else {}),
        'base_commit':snapshot['commit_seq'],'source_hash':digest(source),'filename':filename,'status':'pending',
        'section_revision':snapshot['sections'][primary]['revision'],'citation_checks':checks,
        'citation_issues':list(dict.fromkeys(m for t in after.values() for m in citation_issues(store.root,t))),
        'review_issues':[*issues,{'section':primary,'message':'候选已保存，审查尚未完成；刷新后可恢复'}],'review_state':'pending',
        'method_facts':facts,'method_facts_hash':digest(facts),
        'review_id':None,'review_context_hash':digest(context),
        'review_model_id':getattr(llm,'model_name',None) or getattr(llm,'model',None)}
    with store._transaction() as db:
        if store._read(db)['commit_seq']!=snapshot['commit_seq']:raise ResearchConflict('研究已改变，原稿保留，请刷新后再生成候选')
        db.execute('INSERT INTO commands VALUES (?,?,?,?)',(store.research_id,'paper-edit:'+proposal['id'],digest(proposal),json.dumps(proposal,ensure_ascii=False)))
        if attempt:
            attempt.update(state='proposed',proposal_id=proposal['id'],updated_at=time.time())
            db.execute('UPDATE paper_patch_attempts SET payload=? WHERE id=?',(json.dumps(attempt,ensure_ascii=False),attempt['id']))
    # Persist the exact candidate before the paid review, so cancellation cannot lose it.
    review_issues,hit,context=await review_candidate(store,combined,llm,scope,source_issue)
    proposal.update(review_issues=issues+review_issues,review_state='completed' if hit else 'failed',
        review_id=hit[1]['id'] if hit else None,review_context_hash=digest(context))
    with store._transaction() as db:
        latest=json.loads(db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,'paper-edit:'+proposal['id'])).fetchone()[0])
        if latest['status']!='pending':return latest
        if store._read(db)['commit_seq']!=snapshot['commit_seq'] or digest(source_info(store,store._read(db))[1])!=proposal['source_hash']:
            raise ResearchConflict('审查期间原稿改变，已保存候选未覆盖原稿')
        db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',(json.dumps(proposal,ensure_ascii=False),store.research_id,'paper-edit:'+proposal['id']))
    return proposal


async def review_pending_fragment(store,proposal_id,llm):
    """Resume a saved candidate's interrupted service review; never retry findings."""
    from .paper_editor import get_proposal,source_info,paper_state
    p=get_proposal(store,proposal_id);snapshot=store.refresh_inputs();_,source=source_info(store,snapshot)
    if p['status']!='pending' or p.get('generation_contract') not in {'scoped-anchored-edits-v2','manual-evidence-v1'} or p.get('review_id'):
        raise ResearchConflict('只能恢复尚无有效审查结果的已保存候选；实际审查意见不能绕过')
    from ai_system.research.runtime import ResearchRuntime
    budget=ResearchRuntime(store.root,store.research_id).status()
    if budget['state']=='provider_blocked' or (budget.get('request_limit') is not None and budget['used']>=budget['request_limit']):raise ResearchConflict('审查预算或网关不可用；候选保留，恢复不会清零预算')
    from .issue_edits import ready
    if not (ready(store) if p.get('source_issue') else store.delivery_ready(snapshot)) or snapshot['commit_seq']!=p['base_commit'] or digest(source)!=p['source_hash']:raise ResearchConflict('研究或原稿版本已变化，不能重审旧提案')
    scope=p.get('scope') if p.get('generation_contract')=='scoped-anchored-edits-v2' else None
    if scope:require_scope(store,p,snapshot,source)
    with store._connect() as db:
        failed=db.execute("SELECT payload FROM review_records WHERE research_id=? AND context_hash=? AND state IN ('failed','running') ORDER BY rowid DESC LIMIT 1",(store.research_id,p['review_context_hash'])).fetchone()
    if not failed and p.get('review_state')!='pending':raise ResearchConflict('没有可恢复的对应服务审查失败记录')
    state=await paper_state(store);state['sections'].update(p['after_sections']);ctx=proposal_context(store,state,llm)
    problems=check_sections(state,ctx)
    if problems:raise ResearchConflict('候选仍有确定性或引用问题，不能以重审放行')
    issues,hit,context=await review_candidate(store,state,llm,scope,p.get('source_issue'))
    history=[*p.get('review_history',[]),{'kind':p.get('review_kind','full_document'),'context_hash':p['review_context_hash'],
        'failed_record_id':json.loads(failed[0])['id'] if failed else None,'issues':p['review_issues']}]
    p.update(review_kind='issue_section' if p.get('source_issue') else 'fragment_equivalence' if scope else 'full_document',review_history=history,review_state='completed' if hit else 'failed',review_issues=issues,review_id=hit[1]['id'] if hit else None,
        review_context_hash=digest(context),review_model_id=getattr(llm,'model_name',None) or getattr(llm,'model',None))
    with store._transaction() as db:
        current=store._read(db);latest=json.loads(db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,'paper-edit:'+proposal_id)).fetchone()[0])
        if latest['status']!='pending':return latest
        if current['commit_seq']!=p['base_commit'] or digest(source_info(store,current)[1])!=p['source_hash']:raise ResearchConflict('审查期间原稿改变，未覆盖')
        db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',(json.dumps(p,ensure_ascii=False),store.research_id,'paper-edit:'+proposal_id))
    return p
