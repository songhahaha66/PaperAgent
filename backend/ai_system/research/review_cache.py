"""Versioned, context-bound review results and narrowly scoped service recovery."""
from services.file_services.workspace_layout import workspace_path

from ai_system.research.domain import findings as research_findings
import json,time,uuid,hashlib,re,asyncio
from dataclasses import asdict,is_dataclass
from contextlib import closing
from pathlib import Path
from .store import ResearchStore,digest
from .models import IssueReport

POLICY='review-context-cache-v1'
STAGE_LABELS={'reconcile':'结论审查','sections':'章节审查','citations':'文献支持核查','fragment':'局部等义性审查','section_blocks':'正文分块核查','section_global':'跨节结论核查','issue_target':'定位源意见判定'}
PROCESS_ID=uuid.uuid4().hex
SCIENCE_FIELDS=('inputs','tasks','contract_hash','evidence_hash','claims_hash','artifact_hashes','committed_evidence_hash','configuration_hash')


def science_version(context):return digest({k:context.get(k) for k in SCIENCE_FIELDS})


def coverage_material(stage,context):
    if context.get('policy')=='review-recovery-intent-v1':
        material=context['version_material'];science=material['science'];sections=material.get('section_hashes',{});sources=material.get('citation_sources',{});request=material.get('request_hash')
    else:science=science_version(context);sections=context.get('section_hashes',{});sources=context.get('citation_sources',{});request=context.get('request_hash')
    return digest({'science':science,'sections':sorted(sections.values()) if stage=='citations' else sections,
        'source_version':(context.get('version_material') or context).get('source_version') if stage!='reconcile' else None,'sources':sources,'request':request if stage!='citations' else None})


class ReviewInProgress(RuntimeError):
    def __init__(self,record):self.record=record;super().__init__('该版本审查正在运行，请等待原请求')


class ReviewSuperseded(ValueError):pass


def review_coverage(stage,context):
    candidate=context.get('extra',{}).get('non_blocking',False) and context.get('policy')!='review-recovery-intent-v1'
    scope=context.get('extra',{}).get('scope')
    return {'kind':'candidate' if candidate else 'research','stage':stage,
        'slot':f'{stage}:'+('candidate:'+str((scope or {}).get('id') or digest(context.get('section_hashes',{}))) if candidate else 'research'),
        'version':digest(context),'sections':context.get('section_hashes',{}),
        'input_hash':digest(context.get('inputs',{})),'material_version':coverage_material(stage,context),'policy':'review-coverage-v1',
        'content':context.get('section_coverage')}


def _payload(item):
    return asdict(item) if is_dataclass(item) else item.model_dump() if hasattr(item,'model_dump') else item


def _file(root,path):
    if not path:return None
    target=workspace_path(root,path)
    if not target.is_file():return {'unavailable':True}
    try:
        with target.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError:return {'unavailable':True}


def review_context(ctx,state,stage,system,prompt,schema,*,sections=None,extra=None):
    store=getattr(ctx,'research',None);snapshot=store.snapshot() if store else None
    registry=ctx.registry;results={k:_payload(v) for k,v in registry.results.items()};claims={k:_payload(v) for k,v in registry.claims.items()}
    root=store.root if store else Path(ctx.workspace)
    try:manifest=json.loads((workspace_path(root, 'manifest.json')).read_text()).get('runs',{})
    except (OSError,ValueError):manifest={}
    artifacts={}
    for key,row in results.items():
        run=manifest.get(row.get('run_id'),{})
        artifacts[key]={'status':run.get('status'),'input':_file(root,run.get('input')),'stdout':_file(root,run.get('stdout')),'artifact':_file(root,row.get('path'))}
    snapshot=snapshot or {}
    configuration={}
    if store:
        with closing(store._connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_policy'").fetchone():
                row=db.execute('SELECT version,payload FROM experiment_policy WHERE id=1').fetchone()
                if row:configuration['comparison']={'version':row[0],'policy':json.loads(row[1])}
    citations={}
    from ai_system.research.citations import citation_text
    for cid in set(re.findall(r'\{\{C:C(\d+)\}\}', citation_text('\n'.join((sections or {}).values())))):
        try:
            with closing(store._connect()) as db:
                exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='literature_passages'").fetchone()
                row=db.execute('SELECT payload FROM literature_passages WHERE id=?',(int(cid),)).fetchone() if exists else None
            citations['C'+cid]=digest(json.loads(row[0])) if row else None
        except (AttributeError,ValueError,KeyError):citations['C'+cid]=None
    consistency=digest(snapshot.get('consistency', [])) if stage in {'sections','fragment','section_blocks','section_global','issue_target'} else None
    from ai_system.modules.writing.consistency import section_coverage
    from services.chat_services.paper_project import is_latex, snapshot as source_snapshot
    source_version = source_snapshot(root)['version'] if store and is_latex(store) and stage!='reconcile' else None
    return {'source_version':source_version,'policy':POLICY,'stage':stage,'request_hash':digest({'system':system,'prompt':prompt,'schema':schema.model_json_schema()}),'reconcile_projection_hash':consistency,
        'section_coverage':section_coverage(sections or {}) if stage=='sections' else None,
        'model_id':getattr(ctx.planner_llm,'model_name',None) or getattr(ctx.planner_llm,'model',None),
        'inputs':store.file_inputs() if store else {},
        'experiments':{k:{p:v.get(p) for p in ('revision','execution_id','validity','subproblem','dependencies')} for k,v in snapshot.get('experiments',{}).items()},
        'contract_hash':digest({'requirements':snapshot.get('spec',{}).get('requirements',state.get('analysis',{}).get('requirements',[])),
                              'assumptions':snapshot.get('assumptions',state.get('analysis',{}).get('assumptions',[]))}),
        'evidence_hash':digest(results),'claims_hash':digest(claims),'artifact_hashes':artifacts,
        'committed_evidence_hash':digest({'evidence':snapshot.get('artifacts',{}),'claims':snapshot.get('claims',{})}),
        'configuration_hash':digest(configuration),
        'section_hashes':{k:digest(v) for k,v in (sections or {}).items()},
        'committed_section_hashes':{} if source_version else {k:{p:v.get(p) for p in ('content_hash','context_hash','revision','validity')} for k,v in snapshot.get('sections',{}).items()} if sections is not None else {},
        'citation_sources':citations,'extra':extra or {}}


def error_code(error):
    seen=set();current=error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__=='ResearchBudgetExhausted':return 'budget_exhausted'
        current=getattr(current,'__cause__',None) or getattr(current,'__context__',None)
    return type(error).__name__


def error_detail(error):
    """Keep actionable validation causes without dumping replies or HTTP secrets."""
    from pydantic import ValidationError
    current=error;seen=set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current,ValidationError):
            return '；'.join('.'.join(map(str,item['loc']))+': '+item['msg']
                           for item in current.errors(include_url=False,include_input=False))[:2000]
        if isinstance(current,ValueError):return str(current)[:2000]
        current=current.__cause__ or current.__context__
    return (str(error)[:2000] if isinstance(error,RuntimeError) and str(error) else error_code(error))


class ReviewCache:
    def __init__(self,store):
        self.store=store
        with closing(store._connect()) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS review_records(id TEXT PRIMARY KEY,research_id TEXT NOT NULL,stage TEXT NOT NULL,context_hash TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS review_lookup ON review_records(research_id,stage,context_hash,state);''')

    def get(self,stage,context,schema):
        if stage=='sections' and not context.get('section_coverage',{}).get('complete',True):return None
        key=digest(context)
        with closing(self.store._connect()) as db:rows=db.execute("SELECT payload FROM review_records WHERE research_id=? AND stage=? AND context_hash=? AND state='completed' ORDER BY rowid DESC",(self.store.research_id,stage,key)).fetchall()
        for row in rows:
            try:
                record=json.loads(row[0])
                if record['context']!=context or record['result_hash']!=digest(record['result']):continue
                if record.get('source')=='segmented-aggregate':
                    from .segmented_review import verify_proof
                    if not verify_proof(self.store,record):continue
                if record.get('source')=='issue-long-composition':
                    from services.chat_services.long_issue_review import verify
                    if not verify(self.store,record):continue
                return schema.model_validate(record['result']),record
            except (KeyError,ValueError,TypeError):continue
        return None

    def start(self,stage,context,source='model',*,command_id=None):
        from .runtime import ResearchRuntime
        now=time.time();key=digest(context)
        record={'id':uuid.uuid4().hex,'stage':stage,'context':context,'context_hash':key,'started_at':now,'source':source,
            'coverage':review_coverage(stage,context),'lease':{'process':PROCESS_ID,'token':uuid.uuid4().hex,'expires_at':now+(ResearchRuntime(self.store.root).policy().get('timeout_seconds') or 600)+30}}
        if command_id:record['command_id']=command_id
        with self.store._transaction() as db:
            active=db.execute("SELECT id,payload FROM review_records WHERE research_id=? AND stage=? AND context_hash=? AND state='running' ORDER BY rowid DESC LIMIT 1",(self.store.research_id,stage,key)).fetchone()
            if active:
                prior=json.loads(active[1])
                if prior.get('lease',{}).get('expires_at',prior.get('started_at',0)+120)>now:raise ReviewInProgress(prior)
                prior.update(error_code='interrupted',failed_at=now,lease_expired=True)
                db.execute("UPDATE review_records SET state='failed',payload=? WHERE id=?",(json.dumps(prior,ensure_ascii=False),active[0]))
            db.execute('INSERT INTO review_records VALUES (?,?,?,?,?,?)',(record['id'],self.store.research_id,stage,key,'running',json.dumps(record,ensure_ascii=False)))
        return record

    async def wait(self,record,schema,current_context):
        while True:
            await asyncio.sleep(.05)
            with closing(self.store._connect()) as db:row=db.execute('SELECT state,payload FROM review_records WHERE id=? AND research_id=?',(record['id'],self.store.research_id)).fetchone()
            if not row:raise RuntimeError('审查记录不可用')
            if current_context()!=record['context']:raise ReviewSuperseded('等待期间版本已改变')
            if row[0]=='completed':
                hit=self.get(record['stage'],record['context'],schema)
                if hit:return hit[0]
            if row[0]!='running':raise RuntimeError('原审查未成功完成，请查看失败记录后恢复')
            active=json.loads(row[1])
            if active.get('lease',{}).get('expires_at',active.get('started_at',0)+120)<=time.time():raise RuntimeError('审查连接已中断，租约到期后可恢复')

    async def heartbeat(self,record,interval=10):
        """A live multi-call composition renews the existing lease; crashes do not."""
        from .runtime import ResearchRuntime
        duration=(ResearchRuntime(self.store.root).policy().get('timeout_seconds') or 600)+30
        while True:
            await asyncio.sleep(interval)
            with self.store._transaction() as db:
                row=db.execute('SELECT state,payload FROM review_records WHERE id=? AND research_id=?',(record['id'],self.store.research_id)).fetchone()
                if not row or row[0]!='running':return
                saved=json.loads(row[1])
                if saved.get('lease',{}).get('token')!=record.get('lease',{}).get('token'):return
                expiry=time.time()+duration
                saved['lease']['expires_at']=expiry;record['lease']['expires_at']=expiry
                db.execute('UPDATE review_records SET payload=? WHERE id=?',(json.dumps(saved,ensure_ascii=False),record['id']))

    def complete(self,record,result,*,provenance=None,current_context=None):
        if record['stage']=='sections' and not record['context'].get('section_coverage',{}).get('complete',True):
            from ai_system.modules.writing.consistency import ReviewCoverageIncomplete
            raise ReviewCoverageIncomplete('不完整章节审查不能登记为完成')
        record={**record,'result':result,'result_hash':digest(result),'completed_at':time.time(),'provenance':provenance or {}}
        if record.get('source')=='segmented-aggregate':
            from .segmented_review import verify_proof
            if not verify_proof(self.store,record):raise ValueError('分段覆盖证明不完整，不能登记章节审查完成')
        if record.get('source')=='issue-long-composition':
            from services.chat_services.long_issue_review import verify
            if not verify(self.store,record):raise ValueError('长节候选缺少分段覆盖或源意见定向判定证明')
        stale=False
        with self.store._transaction() as db:
            row=db.execute('SELECT state,payload FROM review_records WHERE id=? AND research_id=?',(record['id'],self.store.research_id)).fetchone()
            if not row or row[0]!='running' or json.loads(row[1]).get('lease')!=record.get('lease'):raise ReviewSuperseded('旧审查租约已经失效')
            current=current_context() if current_context else record['context']
            stale=current!=record['context']
            if stale:record.update(superseded_by_context=digest(current),late_response=True)
            db.execute('UPDATE review_records SET state=?,payload=? WHERE id=?',('superseded' if stale else 'completed',json.dumps(record,ensure_ascii=False),record['id']))
            state=self.store._read(db)
            if state and not stale:
                if record.get('source')=='recovery-intent' and not self._verified_children(db,record):raise ValueError('恢复任务缺少实际完成审查的证明')
                closed=self._close_matching(state,record)
                superseded=self._supersede_services(db,state,record)
                if closed or superseded:self.store._save(db,state,'review_completed',{'review_id':record['id'],'stage':record['stage'],'context_hash':record['context_hash'],'result_hash':record['result_hash'],**self._transitions(state,closed+superseded)})
        if stale:raise ReviewSuperseded('旧版本响应已保存，但不能覆盖新版本')
        return record

    def _supersede_services(self,db,state,record):
        coverage=record.get('coverage') or review_coverage(record['stage'],record['context'])
        if coverage['kind']!='research':return []
        replaced=[]
        for issue in research_findings(state):
            scope=issue.get('review_scope') or {}
            if issue['category']!='service' or issue['status'] in {'resolved','dismissed','superseded'} or issue['source'] not in {'review:'+record['stage'],'review-task:'+record['stage']} or scope.get('stage')!=record['stage']:continue
            prior=db.execute("SELECT state,payload FROM review_records WHERE id=? AND research_id=? AND context_hash=?",(scope.get('failed_review_id'),self.store.research_id,scope.get('context_hash'))).fetchone()
            if not prior or prior[0] not in {'failed','superseded'}:continue
            old=json.loads(prior[1]);old_coverage=old.get('coverage') or review_coverage(old['stage'],old['context'])
            if old_coverage['slot']!=coverage['slot']:continue
            same_material=coverage_material(record['stage'],old['context'])==coverage_material(record['stage'],record['context'])
            proof={'policy':'service-review-supersession-v1','old_review_id':old['id'],'old_context_hash':old['context_hash'],
                'replacement_review_id':record['id'],'replacement_context_hash':record['context_hash'],'result_hash':record['result_hash'],'original_issue_hash':digest(issue)}
            if same_material:
                proof['policy']='service-review-recovery-v1'
                issue.update(status='resolved',review_resolution=proof)
                old.update(recovered_by=record['id'],recovery=proof)
            else:
                issue.update(status='superseded',review_supersession=proof)
                old.update(superseded_by=record['id'],supersession=proof)
            db.execute('UPDATE review_records SET payload=? WHERE id=?',(json.dumps(old,ensure_ascii=False),old['id']));replaced.append(issue['id'])
        return replaced

    @staticmethod
    def _transitions(state,ids):
        return {name:[i['id'] for i in research_findings(state) if i['id'] in ids and i['status']==status]
            for name,status in [('resolved_service_issues','resolved'),('superseded_service_issues','superseded')]}

    def _verified_children(self,db,record):
        ids=record.get('provenance',{}).get('verified_records') or []
        if not ids:return False
        for rid in ids:
            row=db.execute("SELECT state,payload FROM review_records WHERE id=? AND research_id=? AND stage=?",(rid,self.store.research_id,record['stage'])).fetchone()
            if not row or row[0]!='completed':return False
            child=json.loads(row[1]);coverage=child.get('coverage') or review_coverage(child['stage'],child['context'])
            if child.get('source')=='recovery-intent' or coverage['kind']!='research' or child.get('result_hash')!=digest(child.get('result')):return False
            expected=record['context'].get('version_material',{}).get('science')
            if expected and science_version(child['context'])!=expected:return False
            if coverage_material(child['stage'],child['context'])!=coverage_material(record['stage'],record['context']):return False
            if child.get('source')=='segmented-aggregate':
                from .segmented_review import verify_proof
                if not verify_proof(self.store,child):return False
        return True

    def failed(self,record,error,*,diagnostic=None,current_context=None):
        code=error_code(error);detail=error_detail(error)
        record={**record,'failed_at':time.time(),'error_code':code,
                'diagnostic':{'reason':detail, **(diagnostic or {})}}
        with self.store._transaction() as db:
            row=db.execute('SELECT state,payload FROM review_records WHERE id=? AND research_id=?',(record['id'],self.store.research_id)).fetchone()
            if not row or row[0]!='running' or json.loads(row[1]).get('lease')!=record.get('lease'):return record
            current=current_context() if current_context else record['context'];stale=current!=record['context']
            if stale:record.update(superseded_by_context=digest(current),late_response=True)
            db.execute('UPDATE review_records SET state=?,payload=? WHERE id=?',('superseded' if stale else 'failed',json.dumps(record,ensure_ascii=False),record['id']))
            if stale:return record
            state=self.store._read(db)
            if state and state.get('experiments') and not record['context'].get('extra',{}).get('non_blocking',False):
                task=next(iter(state['experiments']));label=STAGE_LABELS[record['stage']]
                issue=self.store._record_issue(state,IssueReport(task_id=task,category='service',message=f'{label}未完成：{detail}',section=f"review:{record['stage']}:{record['context_hash']}"),'confirmed',source='review:'+record['stage'])
                issue['review_scope']={'stage':record['stage'],'context_hash':record['context_hash'],'failed_review_id':record['id']}
                self.store._save(db,state,'review_failed',{'review_id':record['id'],'issue_id':issue['id'],'stage':record['stage'],'context_hash':record['context_hash'],'error_code':code,'detail':detail})
        return record

    @staticmethod
    def _close_matching(state,record):
        closed=[]
        for issue in research_findings(state):
            scope=issue.get('review_scope',{})
            if issue['category']!='service' or issue['status'] in {'resolved','dismissed','superseded'} or scope.get('stage')!=record['stage'] or scope.get('context_hash')!=record['context_hash']:continue
            source_ok=issue['source']=='review:'+record['stage'] or (
                issue['source']=='sections' and record['stage']=='sections' and
                issue.get('review_binding_provenance',{}).get('policy')=='legacy-checkpoint-failure-binding-v1' and
                scope.get('failed_review_id')=='legacy:'+issue['id'])
            source_ok=source_ok or (issue['source']=='review-task:'+record['stage'] and record.get('source')=='recovery-intent' and scope.get('failed_review_id')==record['id'])
            if not source_ok:continue
            original=digest(issue)
            issue.update(status='resolved',review_resolution={'policy':POLICY,'review_id':record['id'],'stage':record['stage'],'context_hash':record['context_hash'],
                'result_hash':record['result_hash'],'completed_at':record['completed_at'],'original_issue_hash':original,'failed_review_id':scope.get('failed_review_id')})
            closed.append(issue['id'])
        return closed

    def reuse(self,record,*,current_context=None):
        with self.store._transaction() as db:
            if current_context is not None and current_context()!=record['context']:raise ValueError('review_context_changed_before_reuse')
            if record.get('source')=='segmented-aggregate':
                from .segmented_review import verify_proof
                if not verify_proof(self.store,record):raise ValueError('分段覆盖证明已失效')
            if record.get('source')=='issue-long-composition':
                from services.chat_services.long_issue_review import verify
                if not verify(self.store,record):raise ValueError('长节候选覆盖或源意见判定证明已失效')
            state=self.store._read(db)
            closed=self._close_matching(state,record) if state else []
            replaced=self._supersede_services(db,state,record) if state else []
            if closed or replaced:self.store._save(db,state,'review_cache_reused',{'review_id':record['id'],'stage':record['stage'],'context_hash':record['context_hash'],**self._transitions(state,closed+replaced)})


async def run_review(ctx,state,stage,schema,system,prompt,perform,*,sections=None,extra=None,validate=None):
    def context():return review_context(ctx,state,stage,system,prompt,schema,sections=sections,extra=extra)
    before=context();store=getattr(ctx,'research',None)
    if stage!='reconcile' and state.get('source_version') and state['source_version']!=before.get('source_version'):
        raise ReviewSuperseded('所检查的源码版本已改变')
    if store is None:
        return await perform()
    cache=ReviewCache(store);record=None;joined=False;heartbeat=None
    try:
        hit=cache.get(stage,before,schema)
        if hit:
            if validate is not None:validate(hit[0])
            cache.reuse(hit[1],current_context=context);return hit[0]
        try:record=cache.start(stage,before)
        except ReviewInProgress as pending:
            joined=True
            answer=await cache.wait(pending.record,schema,context)
            if validate is not None:validate(answer)
            return answer
        heartbeat=asyncio.create_task(cache.heartbeat(record))
        answer=await perform()
        verdict_field={'reconcile':'conflicts','sections':'issues','citations':'verdicts','fragment':'verdict','section_blocks':'issues','section_global':'issues','issue_target':'issues'}[stage]
        if hasattr(answer,'model_fields_set') and verdict_field not in answer.model_fields_set:raise ValueError('review_reply_missing_verdict_field')
        if isinstance(answer,dict) and verdict_field not in answer:raise ValueError('review_reply_missing_verdict_field')
        answer=schema.model_validate(answer.model_dump() if hasattr(answer,'model_dump') else answer)
        if validate is not None:validate(answer)
        cache.complete(record,answer.model_dump(),current_context=context);return answer
    except ReviewSuperseded:raise
    except asyncio.CancelledError:
        if record is not None and not joined:cache.failed(record,RuntimeError('interrupted'),diagnostic={'reason':'cancelled','recoverable':True},current_context=context)
        raise
    except Exception as exc:
        if joined:raise
        if record is None:record=cache.start(stage,before)
        cache.failed(record,exc,current_context=context);raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
