"""Versioned, context-bound review results and narrowly scoped service recovery."""
import json,time,uuid,hashlib,re
from dataclasses import asdict,is_dataclass
from contextlib import closing
from pathlib import Path
from .store import ResearchStore,digest
from .models import IssueReport

POLICY='review-context-cache-v1'
STAGE_LABELS={'reconcile':'结论审查','sections':'章节审查','citations':'文献支持核查'}


def _payload(item):
    return asdict(item) if is_dataclass(item) else item.model_dump() if hasattr(item,'model_dump') else item


def _file(root,path):
    if not path:return None
    target=(root/path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():return {'unavailable':True}
    try:
        with target.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError:return {'unavailable':True}


def review_context(ctx,state,stage,system,prompt,schema,*,sections=None,extra=None):
    store=getattr(ctx,'research',None);snapshot=store.snapshot() if store else None
    registry=ctx.registry;results={k:_payload(v) for k,v in registry.results.items()};claims={k:_payload(v) for k,v in registry.claims.items()}
    root=store.root if store else Path(ctx.workspace)
    try:manifest=json.loads((root/'manifest.json').read_text()).get('runs',{})
    except (OSError,ValueError):manifest={}
    artifacts={}
    for key,row in results.items():
        run=manifest.get(row.get('run_id'),{})
        artifacts[key]={'status':run.get('status'),'input':_file(root,run.get('input')),'stdout':_file(root,run.get('stdout')),'artifact':_file(root,row.get('path'))}
    snapshot=snapshot or {}
    configuration={}
    if store:
        with closing(store._connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='comparison_policy'").fetchone():
                row=db.execute('SELECT version,payload FROM comparison_policy WHERE id=1').fetchone()
                if row:configuration['comparison']={'version':row[0],'policy':json.loads(row[1])}
    citations={}
    for cid in set(re.findall(r'\{\{C:C(\d+)\}\}', '\n'.join((sections or {}).values()))):
        try:
            with closing(store._connect()) as db:
                exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='literature_passages'").fetchone()
                row=db.execute('SELECT payload FROM literature_passages WHERE id=?',(int(cid),)).fetchone() if exists else None
            citations['C'+cid]=digest(json.loads(row[0])) if row else None
        except (AttributeError,ValueError,KeyError):citations['C'+cid]=None
    return {'policy':POLICY,'stage':stage,'request_hash':digest({'system':system,'prompt':prompt,'schema':schema.model_json_schema()}),
        'model_id':getattr(ctx.planner_llm,'model_name',None) or getattr(ctx.planner_llm,'model',None),
        'inputs':store.file_inputs() if store else {},
        'tasks':{k:{p:v.get(p) for p in ('revision','active_attempt','validity','subproblem','dependencies')} for k,v in snapshot.get('tasks',{}).items()},
        'contract_hash':digest({'requirements':snapshot.get('spec',{}).get('requirements',state.get('analysis',{}).get('requirements',[])),
                              'assumptions':snapshot.get('assumptions',state.get('analysis',{}).get('assumptions',[]))}),
        'evidence_hash':digest(results),'claims_hash':digest(claims),'artifact_hashes':artifacts,
        'committed_evidence_hash':digest({'evidence':snapshot.get('evidence',{}),'claims':snapshot.get('claims',{})}),
        'configuration_hash':digest(configuration),
        'section_hashes':{k:digest(v) for k,v in (sections or {}).items()},
        'committed_section_hashes':{k:{p:v.get(p) for p in ('content_hash','context_hash','revision','validity')} for k,v in snapshot.get('sections',{}).items()} if sections is not None else {},
        'citation_sources':citations,'extra':extra or {}}


def error_code(error):
    seen=set();current=error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__=='ResearchBudgetExhausted':return 'budget_exhausted'
        current=getattr(current,'__cause__',None) or getattr(current,'__context__',None)
    return type(error).__name__


class ReviewCache:
    def __init__(self,store):
        self.store=store
        with closing(store._connect()) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS review_records(id TEXT PRIMARY KEY,research_id TEXT NOT NULL,stage TEXT NOT NULL,context_hash TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS review_lookup ON review_records(research_id,stage,context_hash,state);''')

    def get(self,stage,context,schema):
        key=digest(context)
        with closing(self.store._connect()) as db:rows=db.execute("SELECT payload FROM review_records WHERE research_id=? AND stage=? AND context_hash=? AND state='completed' ORDER BY rowid DESC",(self.store.research_id,stage,key)).fetchall()
        for row in rows:
            try:
                record=json.loads(row[0])
                if record['context']!=context or record['result_hash']!=digest(record['result']):continue
                return schema.model_validate(record['result']),record
            except (KeyError,ValueError,TypeError):continue
        return None

    def start(self,stage,context,source='model'):
        record={'id':uuid.uuid4().hex,'stage':stage,'context':context,'context_hash':digest(context),'started_at':time.time(),'source':source}
        with self.store._transaction() as db:db.execute('INSERT INTO review_records VALUES (?,?,?,?,?,?)',(record['id'],self.store.research_id,stage,record['context_hash'],'running',json.dumps(record,ensure_ascii=False)))
        return record

    def complete(self,record,result,*,provenance=None,current_context=None):
        record={**record,'result':result,'result_hash':digest(result),'completed_at':time.time(),'provenance':provenance or {}}
        with self.store._transaction() as db:
            if current_context is not None and current_context()!=record['context']:raise ValueError('review_context_changed_before_commit')
            db.execute("UPDATE review_records SET state='completed',payload=? WHERE id=?",(json.dumps(record,ensure_ascii=False),record['id']))
            state=self.store._read(db)
            if state:
                closed=self._close_matching(state,record)
                if closed:self.store._save(db,state,'review_completed',{'review_id':record['id'],'stage':record['stage'],'context_hash':record['context_hash'],'result_hash':record['result_hash'],'resolved_service_issues':closed})
        return record

    def failed(self,record,error,*,diagnostic=None):
        code=error_code(error);record={**record,'failed_at':time.time(),'error_code':code,'diagnostic':diagnostic or {}}
        with self.store._transaction() as db:
            db.execute("UPDATE review_records SET state='failed',payload=? WHERE id=?",(json.dumps(record,ensure_ascii=False),record['id']))
            state=self.store._read(db)
            if state and state.get('tasks') and not record['context'].get('extra',{}).get('non_blocking',False):
                task=next(iter(state['tasks']));label=STAGE_LABELS[record['stage']]
                issue=self.store._record_issue(state,IssueReport(task_id=task,category='service',message=f'{label}未完成：{code}',section=f"review:{record['stage']}:{record['context_hash']}"),'confirmed',source='review:'+record['stage'])
                issue['review_scope']={'stage':record['stage'],'context_hash':record['context_hash'],'failed_review_id':record['id']}
                self.store._save(db,state,'review_failed',{'review_id':record['id'],'issue_id':issue['id'],'stage':record['stage'],'context_hash':record['context_hash'],'error_code':code})
        return record

    @staticmethod
    def _close_matching(state,record):
        closed=[]
        for issue in state['issues']:
            scope=issue.get('review_scope',{})
            if issue['category']!='service' or issue['status'] in {'resolved','dismissed'} or scope.get('stage')!=record['stage'] or scope.get('context_hash')!=record['context_hash']:continue
            source_ok=issue['source']=='review:'+record['stage'] or (
                issue['source']=='sections' and record['stage']=='sections' and
                issue.get('review_binding_provenance',{}).get('policy')=='legacy-checkpoint-failure-binding-v1' and
                scope.get('failed_review_id')=='legacy:'+issue['id'])
            if not source_ok:continue
            original=digest(issue)
            issue.update(status='resolved',review_resolution={'policy':POLICY,'review_id':record['id'],'stage':record['stage'],'context_hash':record['context_hash'],
                'result_hash':record['result_hash'],'completed_at':record['completed_at'],'original_issue_hash':original,'failed_review_id':scope.get('failed_review_id')})
            closed.append(issue['id'])
        return closed

    def reuse(self,record,*,current_context=None):
        with self.store._transaction() as db:
            if current_context is not None and current_context()!=record['context']:raise ValueError('review_context_changed_before_reuse')
            state=self.store._read(db)
            closed=self._close_matching(state,record) if state else []
            if closed:self.store._save(db,state,'review_cache_reused',{'review_id':record['id'],'stage':record['stage'],'context_hash':record['context_hash'],'resolved_service_issues':closed})


async def run_review(ctx,state,stage,schema,system,prompt,perform,*,sections=None,extra=None,validate=None):
    def context():return review_context(ctx,state,stage,system,prompt,schema,sections=sections,extra=extra)
    before=context();store=getattr(ctx,'research',None)
    if store is None:
        return await perform()
    cache=ReviewCache(store);record=None
    try:
        hit=cache.get(stage,before,schema)
        if hit:
            if validate is not None:validate(hit[0])
            cache.reuse(hit[1],current_context=context);return hit[0]
        record=cache.start(stage,before)
        answer=await perform()
        verdict_field={'reconcile':'conflicts','sections':'issues','citations':'verdicts'}[stage]
        if hasattr(answer,'model_fields_set') and verdict_field not in answer.model_fields_set:raise ValueError('review_reply_missing_verdict_field')
        if isinstance(answer,dict) and verdict_field not in answer:raise ValueError('review_reply_missing_verdict_field')
        answer=schema.model_validate(answer.model_dump() if hasattr(answer,'model_dump') else answer)
        if validate is not None:validate(answer)
        if context()!=before:raise ValueError('review_context_changed_during_call')
        cache.complete(record,answer.model_dump(),current_context=context);return answer
    except Exception as exc:
        if record is None:record=cache.start(stage,before)
        cache.failed(record,exc);raise
