"""Issue-to-candidate bindings over existing issues, reviews and editor commands."""

from ai_system.research.domain import findings as research_findings
import json
from contextlib import closing
from typing import Literal
from pydantic import BaseModel,Field
from ai_system.research.models import ResearchConflict
from ai_system.research.store import digest,ResearchStore
from ai_system.graph.state import ClaimIssue
from ai_system.research.review_cache import ReviewCache,run_review
from ai_system.modules.writing.consistency import AUDIT_SYSTEM,section_audit_prompt,validate_findings
from ai_system.graph.llm_utils import call_structured,as_messages,cap_output

TERMINAL={'resolved','dismissed','superseded'}

class IssueAudit(BaseModel):
    issue_id:str
    verdict:Literal['resolved','unresolved','uncertain']
    reason:str=Field(min_length=1,max_length=1200)
    issues:list[ClaimIssue]


def route(issue):return 'writing' if issue['category']=='writing' else 'service' if issue['category']=='service' else 'research'


def bind_findings(store,ids,sections):
    """Capture only actual review provenance and verifiable, unique raw quotes."""
    from .review_tasks import records,valid_result
    rows=records(store)
    with store._transaction() as db:
        state=store._read(db);changed=[]
        for issue in research_findings(state):
            if issue['id'] not in ids or issue.get('section') not in sections:continue
            text=sections[issue['section']];origin={'section':issue['section'],'section_hash':digest(text),'review_id':None,'quote':None}
            for row in rows:
                if row['stage']!='sections' or not valid_result(row,store) or row['context'].get('section_hashes',{}).get(issue['section'])!=digest(text):continue
                finding=next((f for f in row['result'].get('issues',[]) if f['section']==issue['section'] and f.get('problem') and (issue['message']==f['problem'] or issue['message']=='与结论表不一致：'+f['problem'])),None)
                if not finding:continue
                origin['review_id']=row['id'];quote=finding.get('quote') or ''
                if quote and text.count(quote)==1:origin['quote']=quote
                break
            if issue.get('paper_origin')!=origin:issue['paper_origin']=origin;changed.append(issue['id'])
        if changed:store._save(db,state,'issue_paper_context_bound',{'issue_ids':changed})


def context(store,issue_id):
    from .review_tasks import material
    state=store.snapshot();issue=next((i for i in research_findings(state) if i['id']==issue_id),None)
    if not issue:raise ValueError('意见不存在')
    key=issue.get('section');section=state['sections'].get(key);origin=issue.get('paper_origin') or {}
    same_origin=not origin.get('section_hash') or bool(section and origin['section_hash']==section['content_hash'])
    if route(issue)=='research':
        task=state['tasks'].get(issue['task_id'],{})
        expected=issue.get('repair_targets',{}).get(issue['task_id'],issue.get('task_revision'))
        same_origin=same_origin and task.get('revision')==expected and not task.get('retired',True)
    quote=origin.get('quote') if same_origin and section else None
    if quote and ((section['text'].find(quote)<0 or section['text'].find(quote,section['text'].find(quote)+1)>=0)):quote=None
    binding={'id':issue_id,'issue_hash':digest(issue),'research_id':store.research_id,'science':material(store,'sections')['science'],
        'source_hash':state.get('paper_hash'),'section':key,'section_hash':section['content_hash'] if section else None,
        'section_revision':section['revision'] if section else None,'origin':origin}
    refs=list(issue.get('evidence_refs',[])) or (section.get('evidence_refs',[]) if section else [])
    evidence=[]
    try:manifest=json.loads((store.root/'manifest.json').read_text()).get('runs',{})
    except (OSError,ValueError):manifest={}
    for ref in dict.fromkeys(refs):
        row=state['evidence'].get(ref)
        if not row:continue
        value=row['payload'];run=manifest.get(value.get('run_id'),{})
        evidence.append({**{k:value.get(k) for k in ('id','name','value','unit','run_id')},'current':row.get('active',False),'stdout_path':run.get('stdout')})
    return {'issue':issue,'route':route(issue),'version':digest(binding),'binding':binding,'section':key if section else None,
        'quote':quote,'location':('精确原文（唯一匹配）' if quote else '仅定位章节：该意见没有可验证的精确原文') if section else '该意见没有对应正文章节，定位研究任务',
        'origin_current':same_origin,'active':issue['status'] not in TERMINAL,'evidence':evidence[:30],
        'evidence_note':'意见指向的登记证据' if issue.get('evidence_refs') else '本节登记证据；不代表每条均直接支持此意见'}


def require_binding(store,request,*,writing=True):
    value=context(store,request['id'])
    if value['version']!=request['version']:raise ResearchConflict('意见、正文或研究依据已改变，请重新定位意见')
    if not value['active']:raise ResearchConflict('该意见已处理，不能再次应用旧修改')
    if not writing and not value['origin_current']:raise ResearchConflict('意见对应旧任务版本，须重新核验后再定向修改')
    if writing and (value['route']!='writing' or not value['section']):raise ResearchConflict('实质科研问题必须提交定向研究修改，不能以文字候选关闭')
    if writing and not value['origin_current']:raise ResearchConflict('意见对应的是旧正文版本，须重新核查，不能直接套用旧意见')
    return value


def ready(store):
    state=store.snapshot()
    return all(t['retired'] or t['acceptance']=='accepted' and t['validity']=='current' for t in state['tasks'].values()) and not any(
        i['blocking'] and i['status'] not in TERMINAL and (i['category']!='writing' or i.get('section')=='paper') for i in research_findings(state))


def review_material(store,state,llm,binding):
    from .paper_corrections import proposal_context
    value=require_binding(store,binding);key=value['section'];selected={**state,'sections':{key:state['sections'][key]}}
    ctx=proposal_context(store,selected,llm)
    prompt=section_audit_prompt(selected,ctx,force=True)[0]
    prompt+='\n# 必须逐项核对的源意见\n'+json.dumps({'issue_id':binding['id'],'message':value['issue']['message'],'original_quote':value['quote'],
        'original_section':store.snapshot()['sections'][key]['text']},ensure_ascii=False)
    system=AUDIT_SYSTEM+'\n只核查指定章节及给定源意见。明确返回issue_id、verdict和reason；只有原意见已依据登记证据解决且新节无矛盾时为resolved。未解决为unresolved，证据不足为uncertain。不得把缺少证据误判为已解决。issues必须明确列出残留问题。'
    return ctx,selected,system,prompt


def review_context(store,state,llm,binding):
    from ai_system.research.review_cache import review_context as make
    ctx,selected,system,prompt=review_material(store,state,llm,binding)
    return make(ctx,selected,'sections',system,prompt,IssueAudit,sections=selected['sections'],extra={'non_blocking':True,'scope':{'id':binding['id']},'source_issue':binding,'policy':'issue-section-review-v1'})


async def review(store,state,llm,binding):
    ctx,selected,system,prompt=review_material(store,state,llm,binding)
    def validate(result):
        if result.issue_id!=binding['id']:raise ValueError('核查返回了其他意见')
        if any(i.section not in selected['sections'] for i in result.issues):raise ValueError('核查意见超出指定章节')
        validate_findings([i.research_issue for i in result.issues if i.research_issue],selected,ctx)
        if result.verdict=='resolved' and result.issues:raise ValueError('仍有残留意见时不能判为解决')
    async def perform():return await call_structured(cap_output(llm,2400),IssueAudit,as_messages(system,prompt),validate=validate,retries=0)
    problems=[]
    try:
        answer=await run_review(ctx,selected,'sections',IssueAudit,system,prompt,perform,sections=selected['sections'],extra={'non_blocking':True,'scope':{'id':binding['id']},'source_issue':binding,'policy':'issue-section-review-v1'},validate=validate)
        if answer.verdict!='resolved':problems.append({'section':next(iter(selected['sections'])),'message':answer.reason})
    except Exception as exc:problems.append({'section':next(iter(selected['sections'])),'message':'源意见核查未完成：'+type(exc).__name__})
    material=review_context(store,state,llm,binding);hit=ReviewCache(store).get('sections',material,IssueAudit)
    if not hit:problems.append({'section':next(iter(selected['sections'])),'message':'缺少源意见的实际完成核查，禁止应用'})
    return problems,hit,material


def require_review(store,p,state):
    from types import SimpleNamespace
    require_binding(store,p['source_issue'])
    material=review_context(store,state,SimpleNamespace(model_name=p['review_model_id']),p['source_issue'])
    hit=ReviewCache(store).get('sections',material,IssueAudit)
    if not hit or hit[1]['id']!=p.get('review_id') or hit[0].issue_id!=p['source_issue']['id'] or hit[0].verdict!='resolved' or hit[0].issues:raise ResearchConflict('源意见未在当前候选完整章节上核查通过')
    return hit[1]


def resolve(state,p,record):
    issue=next(i for i in research_findings(state) if i['id']==p['source_issue']['id'])
    if issue['category']!='writing':raise ResearchConflict('科研问题不能靠文稿修改关闭')
    issue.update(status='resolved',review_resolution={'policy':'issue-edit-review-v1','source_version':p['source_issue']['version'],'candidate_id':p['id'],
        'review_id':record['id'],'review_context_hash':record['context_hash'],'review_result_hash':record['result_hash'],
        'section_hash':state['sections'][p['section']]['content_hash'],'section_revision':state['sections'][p['section']]['revision']})
    if record.get('source')=='issue-long-composition':
        issue['review_resolution'].update({k:record['provenance'][k] for k in ('coverage_review_id','target_review_id')})
    state['outcome']='completed' if ResearchStore.delivery_ready(state) else 'blocked'
