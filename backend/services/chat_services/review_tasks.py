"""Versioned views and recoveries over review_records, never a second queue store."""
import asyncio,json,time
from contextlib import closing
from ai_system.research.store import ResearchStore,digest
from ai_system.research.models import IssueReport,ResearchConflict
from ai_system.research.review_cache import ReviewCache,ReviewInProgress,ReviewSuperseded,review_context,science_version
from ai_system.research.review_migration import context_from_store
from ai_system.research.runtime import ResearchRuntime,budget_scope
from ai_system.graph.state import ClaimAudit,ConsistencyReview
from ai_system.graph.llm_utils import call_structured,as_messages
from ai_system.graph.tracing import LLMCallTrace,labelled

LABELS={'reconcile':'结论口径','sections':'章节一致性','citations':'文献支持'}
ACTIVE=set()


def paper_state(store):
    snapshot=store.snapshot()
    state={'task':snapshot['spec']['requirement'],'analysis':{'title':'研究','requirements':snapshot['spec']['requirements'],
        'assumptions':[a for a in snapshot['assumptions'].values() if a['status']!='retired'],
        'subproblems':[t['subproblem'] for t in snapshot['tasks'].values() if not t['retired']]},
        'sections':{k:v['text'] for k,v in snapshot['sections'].items()},'consistency':[]}
    saved=store.root/'.system/consistency.json'
    if saved.exists():
        try:state['consistency']=json.loads(saved.read_text()).get('conflicts',[])
        except (OSError,ValueError):pass
    return state


def material(store,stage):
    state=paper_state(store);ctx=context_from_store(store,'version-only')
    context=review_context(ctx,state,stage,'','',ClaimAudit,sections=state['sections'])
    snapshot=store.snapshot();sections={k:{p:v.get(p) for p in ('revision','content_hash','context_hash','validity')} for k,v in snapshot['sections'].items()}
    publication={k:snapshot.get(k) for k in ('paper_hash','paper_version','paper_conflict')}
    filename='paper.tex' if str(snapshot.get('paper_version','')).endswith('.tex') else 'paper.md'
    path=store.root/filename
    actual=digest(path.read_text()) if path.is_file() else None
    request_hash=None
    from ai_system.graph.nodes.consistency import section_audit_prompt,AUDIT_SYSTEM,reconcile_prompt,RECONCILE_SYSTEM
    if stage=='sections':
        from ai_system.research.segmented_review import needed
        if needed(state['sections']):
            from ai_system.research.segmented_review import aggregate_context
            try:request_hash=aggregate_context(ctx,state)['request_hash']
            except ValueError as exc:request_hash=digest({'unplannable':str(exc),'sections':context['section_hashes']})
        else:
            prompt=section_audit_prompt(state,ctx,force=True)
            if prompt:request_hash=digest({'system':AUDIT_SYSTEM,'prompt':prompt[0],'schema':ClaimAudit.model_json_schema()})
    elif stage=='reconcile':request_hash=digest({'system':RECONCILE_SYSTEM,'prompt':reconcile_prompt(state,ctx),'schema':ConsistencyReview.model_json_schema()})
    return {'science':science_version(context),'request_hash':request_hash,'sections':sections if stage!='reconcile' else {},
        'section_hashes':context['section_hashes'] if stage!='reconcile' else {},
        'citation_sources':context['citation_sources'] if stage!='reconcile' else {},
        'publication':publication if stage!='reconcile' else {},'source_conflict':bool(snapshot.get('paper_hash') and actual!=snapshot['paper_hash'])}


def records(store):
    ReviewCache(store)
    with closing(store._connect()) as db:
        return [{**json.loads(r[1]),'state':r[0]} for r in db.execute('SELECT state,payload FROM review_records WHERE research_id=? ORDER BY rowid DESC',(store.research_id,))]


def valid_result(row,store=None):
    if row['state']!='completed' or row.get('result_hash')!=digest(row.get('result')):return False
    if row.get('source')=='segmented-aggregate':
        from ai_system.research.segmented_review import verify_proof
        return bool(store and verify_proof(store,row))
    return True


def matches(row,current,stage):
    ctx=row.get('context',{})
    if science_version(ctx)!=current['science']:return False
    if stage=='reconcile':return True
    if stage=='sections' and row['stage']=='sections' and ctx.get('request_hash')!=current['request_hash']:return False
    if ctx.get('citation_sources',{})!=current['citation_sources']:return False
    hashes=ctx.get('section_hashes',{})
    return hashes==current['section_hashes'] or stage=='citations' and sorted(hashes.values())==sorted(current['section_hashes'].values())


def adopted_review(store,current,rows):
    """A published editor decision names its review; never infer full coverage from a fragment."""
    if current['source_conflict'] or not store.snapshot().get('paper_hash'):return None
    by_id={r['id']:r for r in rows}
    with closing(store._connect()) as db:
        proposals=[json.loads(r[0]) for r in db.execute("SELECT response FROM commands WHERE research_id=? AND id LIKE 'paper-edit:%' ORDER BY rowid DESC",(store.research_id,))]
    for p in proposals:
        row=by_id.get(p.get('review_id'))
        if p.get('status')!='applied' or not row or not valid_result(row,store) or not matches(row,current,'sections'):continue
        if row['stage']=='sections' and p.get('review_kind') not in {'issue_section','issue_segmented'} and row['result'].get('issues')==[]:return row,'编辑时章节审查（完整覆盖）'
        if row['stage']=='fragment' and row['result'].get('verdict')=='equivalent' and p.get('generation_contract')=='scoped-anchored-edits-v2':
            if any(r['stage']=='reconcile' and valid_result(r) and matches(r,current,'reconcile') and r.get('completed_at',0)>row.get('started_at',0) for r in rows):continue
            return row,'局部等义审查覆盖本次编辑；未重审全文'
    return None


def expire_leases(store):
    cache=ReviewCache(store)
    for row in records(store):
        if row['state']=='running' and row.get('lease',{}).get('expires_at',row.get('started_at',0)+120)<time.time():
            if row['stage'] in {'section_blocks','section_global','issue_target'}:
                cache.failed(row,RuntimeError('interrupted'),diagnostic={'reason':'lease_expired','recoverable':True});continue
            if row['source']=='recovery-intent':current=intent_context(store,row['stage'])==row['context']
            elif row['context'].get('extra',{}).get('non_blocking',False):current=candidate_current(store,row)
            else:current=matches(row,material(store,row['stage']),row['stage']) if row['stage'] in LABELS else False
            if current:cache.failed(row,RuntimeError('interrupted'),diagnostic={'reason':'lease_expired','recoverable':True})
            else:
                row.update(superseded_by_context=digest(material(store,row['stage']) if row['stage'] in LABELS else {}),error_code='interrupted_old_version')
                with store._transaction() as db:db.execute("UPDATE review_records SET state='superseded',payload=? WHERE id=?",(json.dumps(row,ensure_ascii=False),row['id']))


def candidate_current(store,row):
    """Candidate bodies differ from published bodies; bind the saved edit instead."""
    current=material(store,'sections');snapshot=store.snapshot();context=row['context']
    if current['source_conflict'] or science_version(context)!=current['science'] or context.get('committed_section_hashes')!=current['sections']:return False
    with closing(store._connect()) as db:
        proposals=[json.loads(r[0]) for r in db.execute("SELECT response FROM commands WHERE research_id=? AND id LIKE 'paper-edit:%'",(store.research_id,))]
    return any(p.get('status')=='pending' and not p.get('review_id') and p.get('review_context_hash')==row['context_hash']
        and p.get('base_commit')==snapshot['commit_seq'] and p.get('source_hash')==snapshot.get('paper_hash') for p in proposals)


def intent_context(store,stage):
    return {'policy':'review-recovery-intent-v1','stage':stage,'version_material':material(store,stage),'extra':{'non_blocking':True}}


def status(store):
    store.refresh_inputs();expire_leases(store);rows=records(store);snapshot=store.snapshot();runtime=ResearchRuntime(store.root,store.research_id);budget=runtime.status();tasks=[]
    from ai_system.research.citations import citation_issues
    state=paper_state(store)
    prerequisites=all(t['retired'] or t['acceptance']=='accepted' and t['validity']=='current' for t in snapshot['tasks'].values())
    for stage,label in LABELS.items():
        current=material(store,stage);context={'policy':'review-recovery-intent-v1','stage':stage,'version_material':current,'extra':{'non_blocking':True}}
        version=digest(context);same=[r for r in rows if r['source']=='recovery-intent' and r['context_hash']==version]
        root=[r for r in rows if r['stage']==stage and r['source']!='recovery-intent' and not r['context'].get('extra',{}).get('non_blocking',False) and matches(r,current,stage)]
        coverage={'reconcile':'当前登记结论与研究契约','sections':'当前章节一致性；每节最多6000字符','citations':'当前引用句与登记原文支持性'}[stage]
        item={'stage':stage,'label':label,'version':version,'state':'pending','coverage':coverage,'record_id':None,'reason':None,'can_resume':True}
        applicable=(stage!='reconcile' or len(context_from_store(store,'version').registry.claims)>=2) and (stage=='reconcile' or bool(state['sections']))
        if not applicable:item.update(state='not_required',coverage='当前材料尚不需要此审查',can_resume=False)
        elif same and same[0]['state']=='running':item.update(state='running',record_id=same[0]['id'],can_resume=False)
        elif same and same[0]['state']=='failed' and not same[0].get('recovered_by'):item.update(state='failed',record_id=same[0]['id'],reason=same[0].get('error_code','interrupted'))
        elif root and valid_result(root[0],store):
            findings=bool(root[0]['result'].get('issues') or any(c.get('research_issue') for c in root[0]['result'].get('conflicts',[])) or any(v.get('status')!='supported' for v in root[0]['result'].get('verdicts',[])))
            item.update(state='completed',record_id=root[0]['id'],outcome='findings' if findings else 'passed',can_resume=False)
        elif stage=='sections' and adopted_review(store,current,rows):
            adopted,description=adopted_review(store,current,rows)
            fragment=adopted['stage']=='fragment'
            item.update(state='partial' if fragment else 'completed',record_id=adopted['id'],coverage=description,
                outcome='incomplete' if fragment else 'passed',can_resume=fragment,
                reason='局部编辑已核查，当前章节全量审查仍待完成' if fragment else None)
        elif stage=='citations' and not any(citation_issues(store.root,t) for t in state['sections'].values()):
            item.update(state='completed',outcome='passed',coverage='当前引用句与原文逐项核查有效（或无引用）',can_resume=False)
        elif same and same[0]['state'] in {'failed','superseded'}:item.update(state='failed',record_id=same[0]['id'],reason=same[0].get('error_code','interrupted'))
        elif root and root[0]['state'] in {'failed','running','superseded'}:item.update(state='failed' if root[0]['state']!='running' else 'running',record_id=root[0]['id'],reason=root[0].get('error_code'),can_resume=root[0]['state']!='running')
        if stage=='sections':
            from ai_system.graph.nodes.consistency import section_coverage
            item['coverage_detail']=section_coverage(state['sections'])
            from ai_system.research.segmented_review import needed
            if needed(state['sections']):
                try:
                    from ai_system.research.segmented_review import progress
                    model_id=next((r['context'].get('model_id') for r in [*root,*rows] if r['stage'] in {'sections','section_blocks'} and r['context'].get('model_id')),'version-only')
                    segments=progress(context_from_store(store,model_id),state)
                    item.update(segmented=segments,coverage='逐块完整覆盖原文；跨块衔接及跨节主张分别核查')
                    item['coverage_detail']={'policy':'segmented-sections-v1','complete':segments['completed_blocks']==segments['total_blocks'] and segments['global']['state']=='completed',
                        'sections':{s['section']:{'total_chars':s['total_chars'],'included_chars':s['covered_chars'],'local_complete':s['local_complete']} for s in segments['sections']}}
                    if item['state'] not in {'running','failed','completed'}:item.update(state='partial',outcome='incomplete',reason='已完成块保留，恢复仅发送缺失或失效核查')
                    if segments['global']['state']=='blocked':item.update(can_resume=False,reason=segments['global']['reason'])
                except ValueError as exc:item.update(state='partial',outcome='incomplete',can_resume=False,reason=str(exc))
        if current['source_conflict']:
            item.update(state='pending',can_resume=False,reason='源文件有人工修改，先合并原稿')
        if item['can_resume']:
            if current['source_conflict']:item.update(can_resume=False,reason='源文件有人工修改，先合并原稿')
            elif not prerequisites:item.update(can_resume=False,reason='先完成当前研究任务的验收/重算')
            elif budget['state']=='provider_blocked':item.update(can_resume=False,reason='网关鉴权或额度拒绝，先修复配置')
            elif budget['used']>=budget['request_limit'] and item.get('segmented',{}).get('remaining_requests_upper_bound',1)>0:item.update(can_resume=False,reason=f"请求预算不足（{budget['used']}/{budget['request_limit']}），进度保留，追加授权后可恢复")
        tasks.append(item)
    history=[]
    for row in rows[:30]:
        replacement=row.get('superseded_by')
        detail=row['context'].get('section_coverage')
        coverage_note=('本次核查完整覆盖所列章节：'+', '.join(detail['sections']) if detail['complete'] else '覆盖不完整，不能判为全文通过') if detail else '历史记录未说明覆盖范围，不能据此判为全文通过' if row['stage']=='sections' and row['source']!='recovery-intent' else '仅局部等义核查，不是全文审查' if row['stage']=='fragment' else None
        history.append({'id':row['id'],'stage':row['stage'],'state':'superseded' if replacement else row['state'],'original_state':row['state'],
            'version':row['context_hash'],'replacement_review_id':replacement,'recovered_by_review_id':row.get('recovered_by'),'superseded_by_context':row.get('superseded_by_context'),
            'error_code':row.get('error_code'),'source':row['source'],'coverage_detail':detail,'coverage_note':coverage_note})
    pending_candidates=[]
    with closing(store._connect()) as db:
        for r in db.execute("SELECT response FROM commands WHERE research_id=? AND id LIKE 'paper-edit:%' ORDER BY rowid DESC LIMIT 10",(store.research_id,)):
            p=json.loads(r[0])
            if p.get('status')=='pending':pending_candidates.append({'id':p['id'],'section':p['section'],'review_kind':p.get('review_kind','full_document'),
                'state':'failed' if p.get('review_issues') else 'completed','issues':p.get('review_issues',[]),'origin':p.get('origin','manual')})
    return {'research_id':store.research_id,'research_revision':snapshot['revision'],'tasks':tasks,'history':history[:12],
        'issues':[{k:i.get(k) for k in ('id','category','section','status','message')} for i in snapshot['issues'] if i['status'] not in {'resolved','dismissed','superseded'}],
        'pending_candidates':pending_candidates,'budget':budget}


def _save_findings(store,stage,reports,expected):
    if not reports:return []
    with store._transaction() as db:
        if intent_context(store,stage)!=expected:raise ReviewSuperseded('审查期间材料已变化')
        state=store._read(db);ids=[]
        for report in reports:ids.append(store._record_issue(state,report,'confirmed' if report.category=='writing' else 'suspected',source='review-task:'+stage)['id'])
        store._save(db,state,'review_task_findings',{'stage':stage,'issues':ids})
    return ids


async def execute(store,stage,llm):
    """Reuse each stage's schemas/prompts and cache, without resuming solve/write."""
    from ai_system.graph.nodes.consistency import RECONCILE_SYSTEM,AUDIT_SYSTEM,reconcile_prompt,section_audit_prompt,validate_findings,NOT_A_CONFLICT
    from ai_system.research.review_cache import run_review
    state=paper_state(store);ctx=context_from_store(store,getattr(llm,'model_name',None) or getattr(llm,'model',None));ctx.planner_llm=llm
    if stage=='citations':
        from ai_system.research.citations import audit_citations,citation_issues
        await audit_citations(store.root,list(state['sections'].values()),llm,active=True)
        reports=[IssueReport(task_id=next(iter(store.snapshot()['tasks'])),category='writing',section=k,message=m) for k,t in state['sections'].items() for m in citation_issues(store.root,t)]
    else:
        schema=ConsistencyReview if stage=='reconcile' else ClaimAudit
        system=RECONCILE_SYSTEM if stage=='reconcile' else AUDIT_SYSTEM
        prompt=reconcile_prompt(state,ctx) if stage=='reconcile' else section_audit_prompt(state,ctx,force=True)[0]
        def validate(result):
            if stage=='reconcile':
                if any(not c.claims or not set(c.claims)<=set(ctx.registry.claims) for c in result.conflicts):raise ValueError('未知结论')
                validate_findings([c.research_issue for c in result.conflicts if c.research_issue],state,ctx)
            else:
                if any(i.section not in state['sections'] or i.section=='references' for i in result.issues):raise ValueError('未知章节')
                validate_findings([i.research_issue for i in result.issues if i.research_issue],state,ctx)
        from ai_system.research.segmented_review import needed
        if stage=='sections' and needed(state['sections']):
            from ai_system.research.segmented_review import audit
            result=await audit(ctx,state)
        else:
            async def perform():return await call_structured(llm,schema,as_messages(system,prompt),validate=validate,retries=0)
            result=await run_review(ctx,state,stage,schema,system,prompt,perform,sections=None if stage=='reconcile' else state['sections'],validate=validate)
        if stage=='reconcile':reports=[c.research_issue for c in result.conflicts if c.research_issue]
        else:reports=[i.research_issue or IssueReport(task_id=next(iter(store.snapshot()['tasks'])),category='writing',section=i.section,message=i.problem) for i in result.issues if i.contradicts and not NOT_A_CONFLICT.search(i.problem)]
    current=material(store,stage)
    proof=[r['id'] for r in records(store) if r['source']!='recovery-intent' and r['stage']==stage and valid_result(r,store) and not r['context'].get('extra',{}).get('non_blocking',False) and matches(r,current,stage)]
    if not proof:raise RuntimeError('没有当前版本实际完成的审查记录')
    return reports,proof[:1]


async def _run(store,stage,record,llm):
    heartbeat=asyncio.create_task(ReviewCache(store).heartbeat(record))
    try:await _run_owned(store,stage,record,llm)
    finally:
        heartbeat.cancel();await asyncio.gather(heartbeat,return_exceptions=True)


async def _run_owned(store,stage,record,llm):
    cache=ReviewCache(store);context=record['context'];trace=LLMCallTrace()
    if hasattr(llm,'model_copy'):
        callbacks=getattr(llm,'callbacks',None);llm=llm.model_copy(update={'callbacks':[*(callbacks if isinstance(callbacks,list) else []),trace]})
    try:
        if intent_context(store,stage)!=context:raise ReviewSuperseded('尚未发送，材料已变化')
        with budget_scope(store.root,store.research_id),labelled('review-task:'+stage):reports,proof=await execute(store,stage,llm)
        if stage=='reconcile':
            row=next(r for r in records(store) if r['id']==proof[0])
            with store._transaction():
                if intent_context(store,stage)!=context:raise ReviewSuperseded('结论审查完成时材料已变化')
                path=store.root/'.system/consistency.json';temporary=path.with_suffix('.review-tmp')
                temporary.write_text(json.dumps(row['result'],ensure_ascii=False));temporary.replace(path)
        finding_ids=_save_findings(store,stage,reports,context)
        if reports:
            from .issue_edits import bind_findings
            bind_findings(store,finding_ids,paper_state(store)['sections'])
        cache.complete(record,{'outcome':'findings' if reports else 'passed','finding_count':len(reports)},provenance={'verified_records':proof},current_context=lambda:intent_context(store,stage))
    except ReviewSuperseded:
        try:cache.complete(record,{'discarded':True},current_context=lambda:intent_context(store,stage))
        except (ReviewSuperseded,ValueError):pass
    except asyncio.CancelledError:
        cache.failed(record,RuntimeError('interrupted'),diagnostic={'reason':'worker_cancelled','recoverable':True},current_context=lambda:intent_context(store,stage));raise
    except Exception as exc:cache.failed(record,exc,current_context=lambda:intent_context(store,stage))
    finally:trace.dump(store.root/f'.system/review-task-{record["id"]}-llm_calls.json')


def start(store,stage,version,command_id,llm=None,*,model_factory=None):
    if stage not in LABELS:raise ValueError('未知审查类型')
    current=status(store);task=next(t for t in current['tasks'] if t['stage']==stage)
    if task['version']!=version:raise ResearchConflict('材料版本已改变，请刷新后恢复')
    prior=next((r for r in records(store) if r.get('command_id')==command_id),None)
    if prior:
        if prior['context_hash']!=version:raise ResearchConflict('恢复命令已绑定其他版本')
        return {'id':prior['id'],'state':prior['state'],'deduplicated':True}
    if task['state']=='running':return {'id':task['record_id'],'state':'running','deduplicated':True}
    if task['state'] in {'completed','not_required'}:return {'id':task['record_id'],'state':task['state'],'deduplicated':True}
    if not task['can_resume']:raise ResearchConflict(task['reason'] or '暂不能恢复')
    if model_factory is not None:llm=model_factory()
    context=intent_context(store,stage);cache=ReviewCache(store)
    try:record=cache.start(stage,context,'recovery-intent',command_id=command_id)
    except ReviewInProgress as active:return {'id':active.record['id'],'state':'running','deduplicated':True}
    with store._transaction() as db:
        state=store._read(db)
        issue=store._record_issue(state,IssueReport(task_id=next(iter(state['tasks'])),category='service',section='review-task:'+record['id'],message=LABELS[stage]+'待完成；恢复不会清零预算'),'confirmed',source='review-task:'+stage)
        issue['review_scope']={'stage':stage,'context_hash':record['context_hash'],'failed_review_id':record['id']}
        store._save(db,state,'review_task_started',{'review_id':record['id'],'version':version,'stage':stage,'issue_id':issue['id']})
    worker=asyncio.create_task(_run(store,stage,record,llm));ACTIVE.add(worker);worker.add_done_callback(ACTIVE.discard)
    return {'id':record['id'],'state':'running','deduplicated':False}
