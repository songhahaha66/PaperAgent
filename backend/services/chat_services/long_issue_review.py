"""Compose segmented candidate coverage with a bounded, located source-issue verdict.

Uses the existing review_records and publication gate; never resolves an issue here.
"""

from ai_system.research.domain import findings as research_findings
import asyncio,json
from ai_system.research import segmented_review as seg
from ai_system.research.review_cache import ReviewCache,ReviewInProgress,review_context,science_version
from ai_system.research.store import digest
from ai_system.research.models import ResearchConflict
from ai_system.graph.state import ClaimAudit,ClaimIssue
from ai_system.modules.writing.consistency import AUDIT_SYSTEM,section_audit_prompt,validate_findings,NOT_A_CONFLICT
from ai_system.graph.llm_utils import as_messages,call_structured,cap_output

POLICY='long-issue-edit-v1'


def changed_span(before,after):
    start=0
    while start<min(len(before),len(after)) and before[start]==after[start]:start+=1
    end=0
    while end<min(len(before),len(after))-start and before[len(before)-end-1]==after[len(after)-end-1]:end+=1
    return start,len(before)-end,len(after)-end


def target_window(text,start,end):
    blocks=seg.plan_for({'target':text})['blocks']
    choices=[b for b in blocks if b['window_start']<=start and end<=b['window_end']]
    if not choices:raise seg.CoverageError('修改跨度超过有界定向核查窗口，请围绕源意见作一次局部修订')
    block=min(choices,key=lambda b:b['window_end']-b['window_start'])
    a,b=block['window_start'],block['window_end']
    return text[a:b],{'start':a,'end':b,'hash':digest(text[a:b])}


def material(store,state,llm,binding):
    from .issue_edits import require_binding,IssueAudit
    from .paper_corrections import proposal_context
    value=require_binding(store,binding);key=value['section'];before=store.snapshot()['sections'][key]['text'];after=state['sections'][key]
    if max(len(before),len(after))<=6000:
        old,new=before,after;location={'original':{'start':0,'end':len(before),'hash':digest(before)},'candidate':{'start':0,'end':len(after),'hash':digest(after)}}
    else:
        quote=value['quote']
        if not quote:raise seg.CoverageError('长章节源意见没有可验证的精确原文，请先确认定位，不能猜测定向核查范围')
        point=before.find(quote);start,old_end,new_end=changed_span(before,after)
        if start==old_end and start==new_end:raise ResearchConflict('候选尚未修改源意见对应的正文')
        old,old_location=target_window(before,min(point,start),max(point+len(quote),old_end))
        new,new_location=target_window(after,start,new_end);location={'original':old_location,'candidate':new_location}
    ctx=proposal_context(store,state,llm);selected={**state,'sections':{key:new}}
    prompt=section_audit_prompt(selected,ctx,force=True)[0]
    prompt+='\n# 定位源意见\n'+json.dumps({'issue_id':binding['id'],'source_version':binding['version'],'message':value['issue']['message'],
        'original_quote':value['quote'],'original_window':old,'candidate_window':new,'location':location},ensure_ascii=False)
    system=AUDIT_SYSTEM+'\n对已定位的源意见作独立判定，完整候选由另一份分段覆盖证明检查。明确返回issue_id、verdict、reason、issues；只有源意见确已解决且窗口没有引入新矛盾时为resolved；证据不足为uncertain。不能因程序覆盖完成就推断意见已解决。'
    target=review_context(ctx,selected,'issue_target',system,prompt,IssueAudit,sections=selected['sections'],extra={'non_blocking':True,'source_issue':binding,
        'policy':POLICY,'location':location,'full_candidate_hash':digest({k:digest(v) for k,v in state['sections'].items()})})
    coverage=seg.aggregate_context(ctx,state,candidate=True)
    others={i['id']:digest(i) for i in research_findings(store.snapshot()) if i['id']!=binding['id'] and i['category']=='writing' and i['status'] not in {'resolved','dismissed','superseded'}}
    composite=review_context(ctx,state,'sections',POLICY,digest({'coverage':digest(coverage),'target':digest(target)}),IssueAudit,sections=state['sections'],
        extra={'non_blocking':True,'scope':{'id':binding['id']},'source_issue':binding,'policy':POLICY,'coverage_context_hash':digest(coverage),
            'target_context_hash':digest(target),'location':location,'existing_issues':others})
    composite['section_coverage']={**coverage['section_coverage'],'policy':POLICY}
    return ctx,selected,system,prompt,target,coverage,composite


def known_findings(store,reports,context,sections):
    """Only the unchanged, already-known *other* writing findings may remain."""
    snapshot=store.snapshot();allowed=[];known=context['extra']['existing_issues']
    for finding in reports:
        if finding.research_issue:raise ResearchConflict('候选发现新的科研问题，不能以文稿修改放行')
        if not finding.contradicts or NOT_A_CONFLICT.search(finding.problem):continue
        match=None
        for issue in research_findings(snapshot):
            if issue['id'] not in known or digest(issue)!=known[issue['id']] or issue.get('section')!=finding.section:continue
            quote=(issue.get('paper_origin') or {}).get('quote')
            same_text=finding.problem==issue['message'] or issue['message']=='与结论表不一致：'+finding.problem
            same_quote=bool(quote and finding.quote==quote and sections[finding.section].count(quote)==1)
            unchanged=sections.get(finding.section)==snapshot['sections'].get(finding.section,{}).get('text')
            if same_quote or unchanged and same_text:match=issue['id'];break
        if match is None:raise ResearchConflict('候选完整覆盖核查仍有新的或源意见相关的问题，禁止应用：'+finding.problem)
        allowed.append(match)
    return sorted(set(allowed))


def verify(store,record):
    from .issue_edits import IssueAudit
    from services.chat_services.review_tasks import records
    try:
        context=record['context'];extra=context['extra'];proof=record['provenance']
        if record['context_hash']!=digest(context) or extra.get('policy')!=POLICY:return False
        rows={r['id']:r for r in records(store)};coverage=rows[proof['coverage_review_id']];target=rows[proof['target_review_id']]
        for row,expected in [(coverage,extra['coverage_context_hash']),(target,extra['target_context_hash'])]:
            if row['state']!='completed' or row['context_hash']!=expected or expected!=digest(row['context']) or row.get('result_hash')!=digest(row.get('result')):return False
            if science_version(row['context'])!=science_version(context) or row['context'].get('model_id')!=context.get('model_id'):return False
        if coverage['source']!='segmented-aggregate' or coverage['context']['section_hashes']!=context['section_hashes'] or not seg.verify_proof(store,coverage):return False
        if target['stage']!='issue_target' or target['context']['extra']['source_issue']!=extra['source_issue'] or target['context']['extra']['full_candidate_hash']!=digest(context['section_hashes']):return False
        verdict=IssueAudit.model_validate(target['result'])
        if verdict.issue_id!=extra['source_issue']['id'] or verdict.verdict=='resolved' and verdict.issues or record['result']!=target['result']:return False
        plan=coverage['context']['extra']['segmented_plan'];sections={k:''.join(b['text'] for b in plan['blocks'] if b['section']==k) for k in plan['sections']}
        allowed=known_findings(store,[ClaimIssue.model_validate(i) for i in coverage['result']['issues']],context,sections)
        return allowed==proof.get('retained_issue_ids',[])
    except (KeyError,ValueError,TypeError,ResearchConflict):return False


async def review(store,state,llm,binding):
    from .issue_edits import IssueAudit
    ctx,selected,system,prompt,target,coverage,context=material(store,state,llm,binding);cache=ReviewCache(store)
    def current():
        try:return material(store,state,llm,binding)[-1]
        except (ValueError,ResearchConflict):return {'invalid_source_issue_version':binding['version']}
    hit=cache.get('sections',context,IssueAudit)
    if hit:return ([] if hit[0].verdict=='resolved' else [{'section':next(iter(selected['sections'])),'message':hit[0].reason}]),hit,context
    try:record=cache.start('sections',context,'issue-long-composition')
    except ReviewInProgress as running:
        await cache.wait(running.record,IssueAudit,current)
        return await review(store,state,llm,binding)
    heartbeat=asyncio.create_task(cache.heartbeat(record));problems=[]
    try:
        result=await seg.audit(ctx,state,candidate=True);cover=cache.get('sections',coverage,ClaimAudit)
        if not cover:raise seg.CoverageError('候选缺少实际完成的分段覆盖证明')
        retained=known_findings(store,result.issues,context,state['sections'])
        def validate(answer):
            if answer.issue_id!=binding['id'] or any(i.section not in selected['sections'] for i in answer.issues):raise ValueError('源意见判定范围不匹配')
            validate_findings([i.research_issue for i in answer.issues if i.research_issue],selected,ctx)
            if answer.verdict=='resolved' and answer.issues:raise ValueError('存在新问题时不能判为解决')
        async def perform():
            seg._budget(ctx)
            return await call_structured(cap_output(llm,2400),IssueAudit,as_messages(system,prompt),validate=validate,retries=0)
        answer=await run_target(ctx,selected,system,prompt,target,perform,validate)
        target_hit=cache.get('issue_target',target,IssueAudit)
        if not target_hit:raise seg.CoverageError('缺少源意见实际判定记录')
        cache.complete(record,answer.model_dump(),provenance={'coverage_review_id':cover[1]['id'],'target_review_id':target_hit[1]['id'],'retained_issue_ids':retained},current_context=current)
        if answer.verdict!='resolved':problems.append({'section':next(iter(selected['sections'])),'message':answer.reason})
    except asyncio.CancelledError:
        cache.failed(record,RuntimeError('interrupted'),current_context=current);raise
    except Exception as exc:
        cache.failed(record,exc,current_context=current)
        reason=str(exc)[:1200] if isinstance(exc,(ResearchConflict,seg.CoverageError)) else type(exc).__name__
        problems.append({'section':next(iter(selected['sections'])),'message':'长节候选覆盖或源意见核查未完成：'+reason})
    finally:
        heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)
    return problems,cache.get('sections',context,IssueAudit),context


async def run_target(ctx,selected,system,prompt,target,perform,validate):
    from .issue_edits import IssueAudit
    from ai_system.research.review_cache import run_review
    return await run_review(ctx,selected,'issue_target',IssueAudit,system,prompt,perform,sections=selected['sections'],extra=target['extra'],validate=validate)


def progress(store,state,llm,binding):
    ctx,_,_,_,target,_,context=material(store,state,llm,binding)
    from services.chat_services.review_tasks import records
    from .issue_edits import IssueAudit
    local=seg.progress(ctx,state);rows=records(store)
    completed=ReviewCache(store).get('issue_target',target,IssueAudit)
    latest=next((r for r in rows if r['stage']=='issue_target' and r['context_hash']==digest(target)),None)
    return {'segmented':local,'target_state':'completed' if completed else latest['state'] if latest else 'pending','target_verdict':completed[0].verdict if completed else None,'composition_context_hash':digest(context)}
