"""Conservative offline migration of legacy checkpoint-backed review evidence.

A bare JSON file, an empty legacy result, or a gateway response alone is not
sufficient. This module never constructs a model client or invokes a model.
"""
from services.file_services.workspace_layout import workspace_path

from .domain import experiment_report, experiment_design

from ai_system.research.domain import findings as research_findings
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from .store import ResearchStore,digest
from .review_cache import ReviewCache,review_context
from .validation import protocol


def context_from_store(store,model_id):
    from ..graph.registry import ResultRegistry,Result,Claim
    from services.file_services.workspace_fs import WorkspaceFS
    payload=store.registry_payload()
    fs=WorkspaceFS(str(store.root))
    return SimpleNamespace(research=store,workspace=store.root,fs=fs,planner_llm=SimpleNamespace(model_name=model_id),
        registry=ResultRegistry(fs,results={r['id']:Result(**r) for r in payload['results']},claims={c['id']:Claim(**c) for c in payload['claims']}))


def validate_scientific_checkpoint(store,values):
    snapshot=store.snapshot();analysis=values.get('analysis') or {}
    if analysis.get('requirements')!=snapshot['spec']['requirements']:raise ValueError('检查点契约与当前研究不一致')
    if analysis.get('assumptions')!=[a for a in snapshot['assumptions'].values() if a['status']!='retired']:raise ValueError('检查点假设已变化')
    if analysis.get('subproblems')!=[t['subproblem'] for t in snapshot['experiments'].values() if t['kind']!='verify']:raise ValueError('检查点研究任务已变化')
    for key,task in snapshot['experiments'].items():
        if task['retired'] or task['kind']=='verify':continue
        if task['execution_status']!='succeeded' or task['validity']!='current' or (values.get('solutions') or {}).get(key)!=experiment_report(snapshot, task):raise ValueError('检查点求解结果与当前执行记录不一致')
        if (values.get('designs') or {}).get(key)!=experiment_design(snapshot, task):raise ValueError('检查点设计/冻结方案已变化')
        from ai_system.modules.experiments.legacy_comparisons import LegacyComparisons
        exploration=LegacyComparisons(store).read(key,task['revision']) if (experiment_design(snapshot, task) or {}).get('experiment_id') else None
        if exploration:
            from ai_system.modules.experiments.legacy_comparisons import LegacyComparisons
            if LegacyComparisons(store).policy()['version']!=exploration['policy_version']:raise ValueError('旧审查之后比较配置已变化')
        attempt=snapshot['executions'][task['execution_id']]
        if store._inputs(snapshot,task,store.file_inputs())!=attempt['input_refs'] or digest(protocol(snapshot,key))!=attempt['protocol_hash']:raise ValueError('检查点对应输入/冻结验收已变化')
        from .domain import execution_lineage,current_results
        receipts={}
        for aid in reversed(execution_lineage(snapshot,task['execution_id'])):
            for result in snapshot['executions'][aid]['result']['results']:
                receipts.setdefault(result['id'],{'type':'result','active':True,'producer_attempt':aid,'payload':result})
        original={r['id']:r for r in current_results({**snapshot,'artifacts':receipts},{key})}
        actual={r['id']:r for r in current_results(snapshot,{key})}
        if original!=actual:raise ValueError('检查点证据已变化')
        originals={c['id']:c for c in attempt['result']['claims']}
        if any(originals.get(cid)!=entry['payload'] for cid,entry in snapshot['claims'].items() if entry['producer_attempt']==task['execution_id']):raise ValueError('检查点结论已变化')
    return snapshot


def checkpoint_pair(pre,post,node,next_node):
    return (pre.next==(node,) and post.next==(next_node,) and post.parent_config==pre.config
            and post.metadata.get('source')=='loop' and post.metadata.get('step')==pre.metadata.get('step',-2)+1)


def completed_reconcile_proof(store,ctx,pre,post,saved,trace,requests):
    from ai_system.modules.writing.consistency import reconcile_prompt,RECONCILE_SYSTEM,validate_findings
    from ..graph.state import ConsistencyReview
    if not checkpoint_pair(pre,post,'reconcile','write'):raise ValueError('缺少成功审查的相邻执行检查点')
    validate_scientific_checkpoint(store,pre.values);validate_scientific_checkpoint(store,post.values)
    # Non-empty actual findings cannot have been manufactured by a missing
    # verdict field's default. Empty legacy reviews need raw response evidence.
    conflicts=post.values.get('consistency')
    if not conflicts or saved!={'conflicts':conflicts} or post.values.get('research_restart'):raise ValueError('旧审查缺少可明确验证的有效输出')
    review=ConsistencyReview.model_validate({'conflicts':conflicts})
    if any(not c.claims or not set(c.claims)<=set(ctx.registry.claims) for c in review.conflicts):raise ValueError('旧审查含未知结论')
    validate_findings([c.research_issue for c in review.conflicts if c.research_issue],pre.values,ctx)
    if any(c.research_issue for c in review.conflicts):raise ValueError('旧审查仍要求研究核验，不能导入完成结果')
    begin=datetime.fromisoformat(pre.created_at).timestamp();end=datetime.fromisoformat(post.created_at).timestamp()
    sent=[r for r in requests if r.get('research_id')==store.research_id and begin<=r['sent_at']<=end and r.get('http_status')==200]
    if not sent:raise ValueError('缺少该检查点区间内的真实调用记录')
    last=max(sent,key=lambda r:r['sent_at'])
    completions=[c for c in trace if c.get('node')=='reconcile' and c.get('finish')=='stop' and not c.get('error') and abs((end-last['sent_at'])-c['seconds'])<3]
    if len(completions)!=1:raise ValueError('成功审查trace无法唯一关联到该检查点')
    prompt=reconcile_prompt(pre.values,ctx);context=review_context(ctx,pre.values,'reconcile',RECONCILE_SYSTEM,prompt,ConsistencyReview)
    return {'stage':'reconcile','context':context,'result':review.model_dump(),'provenance':{
        'policy':'legacy-checkpoint-review-v1','input_checkpoint':pre.config,'output_checkpoint':post.config,
        'input_checkpoint_hash':digest(pre.values),'output_checkpoint_hash':digest(post.values),
        'original_completed_at':post.created_at,'saved_result_hash':digest(saved),'model_request_sequence':last['sequence'],'trace_hash':digest(completions[0])}}


def section_failure_proof(store,ctx,pre,post,validation):
    from ai_system.modules.writing.consistency import section_audit_prompt,AUDIT_SYSTEM
    from ..graph.state import ClaimAudit
    if not checkpoint_pair(pre,post,'validate','observe'):raise ValueError('缺少审查失败的相邻执行检查点')
    snapshot=validate_scientific_checkpoint(store,pre.values)
    sections={k:v['text'] for k,v in snapshot['sections'].items()}
    if pre.values.get('sections')!=sections:raise ValueError('失败审查对应的章节版本已变化')
    recorded={i['id']:i for i in validation.get('issues',[])}
    checkpoint_issues={i['id']:i for i in post.values.get('issues',[])}
    candidates=[i for i in research_findings(snapshot) if i['category']=='service' and i['source']=='sections' and i['status']=='confirmed'
        and i['message'].startswith('章节审查未完成：') and i['id'] in recorded and checkpoint_issues.get(i['id'])==recorded[i['id']]]
    if len(candidates)!=1:raise ValueError('无法唯一关联旧章节服务故障')
    if '预算耗尽' not in candidates[0]['message']:raise ValueError('此迁移只绑定有明确证据的旧预算拒绝')
    prompt,_=section_audit_prompt(pre.values,ctx)
    context=review_context(ctx,pre.values,'sections',AUDIT_SYSTEM,prompt,ClaimAudit,sections=sections)
    return {'issue_id':candidates[0]['id'],'stage':'sections','context':context,'provenance':{
        'policy':'legacy-checkpoint-failure-binding-v1','input_checkpoint':pre.config,'output_checkpoint':post.config,
        'input_checkpoint_hash':digest(pre.values),'validation_hash':digest(validation),'error_code':'budget_exhausted'}}


async def migrate(store,model_id,*,apply=False):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from ..graph.build import compile_graph
    ctx=context_from_store(store,model_id)
    async with AsyncSqliteSaver.from_conn_string(str(workspace_path(store.root, '.system/graph.sqlite'))) as saver:
        graph=compile_graph(saver)
        history=[cp async for cp in graph.aget_state_history({'configurable':{'thread_id':store.research_id}})]
    saved=json.loads((workspace_path(store.root, '.system/consistency.json')).read_text())
    trace=json.loads((workspace_path(store.root, '.system/llm_calls.json')).read_text())['calls']
    from .runtime import ResearchRuntime
    requests=ResearchRuntime(store.root,store.research_id).requests()
    validation=json.loads((workspace_path(store.root, '.system/validation.json')).read_text())
    successes=[];failures=[]
    for post in history:
        pre=next((cp for cp in history if cp.config==post.parent_config),None)
        if pre is None:continue
        if checkpoint_pair(pre,post,'reconcile','write'):
            try:successes.append(completed_reconcile_proof(store,ctx,pre,post,saved,trace,requests))
            except ValueError:pass
        if checkpoint_pair(pre,post,'validate','observe'):
            try:failures.append(section_failure_proof(store,ctx,pre,post,validation))
            except ValueError:pass
    if len(successes)!=1 or len(failures)!=1:raise ValueError('旧结果/服务故障不能唯一、有效地绑定当前版本')
    result={'completed_review':successes[0],'failed_review':failures[0],'applied':False}
    if not apply:return result
    cache=ReviewCache(store);success=successes[0]
    from ..graph.state import ConsistencyReview
    if not cache.get('reconcile',success['context'],ConsistencyReview):
        record=cache.start('reconcile',success['context'],source='legacy_checkpoint_migration')
        from ai_system.modules.writing.consistency import RECONCILE_SYSTEM,reconcile_prompt
        input_state=next(cp.values for cp in history if cp.config==success['provenance']['input_checkpoint'])
        current=lambda:review_context(ctx,input_state,'reconcile',RECONCILE_SYSTEM,reconcile_prompt(input_state,ctx),ConsistencyReview)
        cache.complete(record,success['result'],provenance=success['provenance'],current_context=current)
    failure=failures[0]
    with store._transaction() as db:
        state=store._read(db);issue=next(i for i in research_findings(state) if i['id']==failure['issue_id'])
        from ai_system.modules.writing.consistency import section_audit_prompt,AUDIT_SYSTEM
        from ..graph.state import ClaimAudit
        input_state=next(cp.values for cp in history if cp.config==failure['provenance']['input_checkpoint'])
        prompt,_=section_audit_prompt(input_state,ctx)
        current=review_context(ctx,input_state,'sections',AUDIT_SYSTEM,prompt,ClaimAudit,sections=input_state['sections'])
        if current!=failure['context']:raise ValueError('绑定前章节审查输入已变化')
        scope={'stage':'sections','context_hash':digest(failure['context']),'failed_review_id':'legacy:'+failure['issue_id']}
        if issue.get('review_scope') not in (None,scope):raise ValueError('旧故障已有不同版本绑定')
        if issue.get('review_scope') is None:
            issue['review_scope']=scope;issue['review_binding_provenance']=failure['provenance']
            store._save(db,state,'legacy_review_failure_bound',{'issue_id':issue['id'],'review_scope':scope,'provenance':failure['provenance']})
    result['applied']=True
    return result
