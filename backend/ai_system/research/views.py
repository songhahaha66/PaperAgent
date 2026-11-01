"""Shared read-only question, provenance and execution projections."""
from services.file_services.workspace_layout import workspace_path
import re
from .domain import findings
from .progress import issue_next_step
from ai_system.graph.registry import find_refs

CLOSED = {'resolved','dismissed','superseded'}
LABELS = {'experience.candidates':'更新经验索引','experience.search':'查找相关经验',
    'research.update_plan':'更新研究计划',
    'research.list_files':'查看工作区文件','research.search_files':'搜索工作区文件',
    'research.materialize_artifact':'准备计算输入','research.inspect_runtime':'查看沙箱状态','research.reset_runtime':'重置沙箱',
    'writing.preview':'预览论文 PDF','writing.use_asset':'引用论文素材','writing.propose_edits':'提出论文修改',
    'writing.save_source':'保存论文源码','writing.compile':'编译 PDF','research.update_question':'更新研究问题','research.create_question':'建立研究问题','research.edit_question':'修改研究问题','research.schedule_experiment':'安排实验',
    'research.continue_experiment':'继续实验','research.read_question':'读取问题与依据',
    'research.deliver':'交付研究','research.ask_user':'请求必要信息','research.pause':'暂停研究','research.tool_error':'工具调用失败',
    'research.run_python':'运行研究代码','research.observe':'研究计算与分析','experiments.inspect':'查看输入材料','experiments.execute':'执行实验',
    'research.revise_interpretation':'修订实验解释','research.check_interpretation':'核查实验解释','research.read_artifact':'读取已有文件','literature.collect':'获取文献证据',
    'writing.edit':'编辑研究正文','research.delegate':'执行独立子任务',
    'writing.validate':'检查正文文件与引用',
    'writing.render':'渲染论文','writing.export':'导出论文','run_python':'运行实验代码',
    'read_file':'读取已有文件','retry_outputs':'同步结果文件','check_interpretation':'核查实验解释'}


def questions(snapshot):
    """Topics locate existing runs and historical experiments; no answer ledger."""
    rows=[]
    for question in snapshot['questions'].values():
        ids={e['task_id'] for e in snapshot['experiments'].values()
             if e['question_id']==question['id'] and not e['retired']}
        ids.update(question.get('related_experiment_ids', []))
        issues=[i for i in findings(snapshot) if i['task_id'] in ids and i['status'] not in CLOSED]
        row={k:question[k] for k in ('id','title','question','version','retired','reason','history') if k in question}
        if 'history' in row:
            row['history']=[{k:v for k,v in entry.items() if k in {'id','title','question','version','retired','reason'}}
                            for entry in row['history']]
        row.update(experiment_ids=sorted(ids),
            sources=list(dict.fromkeys(r['source_quote'] for r in snapshot['spec']['requirements'] if ids.intersection(r['task_ids']))),
            issues=[{'id':i['id'],'message':i['message'],'next_step':issue_next_step(i,snapshot)} for i in issues])
        rows.append(row)
    return rows


def question_context(rows, *, include_history=False):
    """Research topics only; answers live in the conversation and manuscript."""
    fields=('id','title','question','experiment_ids','sources','issues','retired','version')
    if include_history:fields+=('history',)
    return [{key:row[key] for key in fields if key in row} for row in rows]


def result_context(snapshot,item):
    """Read the existing value, declared scope and immutable execution provenance."""
    artifact=snapshot['artifacts'][item['id']]
    attempt=snapshot['executions'][artifact['producer_attempt']]
    return {**item,'reference':'{{'+('F' if item['kind']=='figure' else 'R')+':'+item['id']+'}}',
            'producer_attempt':artifact['producer_attempt'],'content_hash':artifact['content_hash'],
            'input_refs':attempt['input_refs']}


def experiment_context(snapshot, experiment_ids=None):
    from .domain import experiment_report, experiment_design, current_results, execution_lineage
    from .feedback import interpretation_version
    selected=set(experiment_ids) if experiment_ids is not None else {
        eid for eid,e in snapshot['experiments'].items() if not e['retired']}
    rows=[]
    results=current_results(snapshot,selected)
    for eid,e in snapshot['experiments'].items():
        if eid not in selected:continue
        artifact=snapshot['artifacts'].get(e['report_ref']) or {}
        lineage = set(execution_lineage(snapshot, e['execution_id']))
        rows.append({'task_id':eid,'question_id':e['question_id'],'objective':e['objective'],
            'execution_status':e['execution_status'],'acceptance':e['acceptance'],'validity':e['validity'],
            'method':e.get('method_instruction',''),
            'adopted_method':(experiment_design(snapshot,e) or {}).get('chosen'),
            'adopted_premises':[{k:a[k] for k in ('id','statement','revision')} for a in snapshot['assumptions'].values()
                if a['status']=='active' and eid in a['task_ids']],
            'conditions':(e.get('experiment') or {}).get('conditions',''),
            'evaluation':(e.get('experiment') or {}).get('evaluation',''),
            'record':experiment_report(snapshot,e),'record_path':artifact.get('payload',{}).get('path'),
            'interpretation_version':interpretation_version(snapshot,e),
            'dependencies':list(e['dependencies']),
            'results':[result_context(snapshot,r) for r in results if r['subproblem']==eid],
            'files':[{'id':ref,'path':a['payload']['path'],'kind':a['type'],
                      'version':a['content_hash']}
                for ref,a in snapshot['artifacts'].items()
                if a['experiment_id']==eid and a['producer_attempt'] in lineage
                and a['type'] in {'code','stdout','stderr','data'} and a['payload'].get('path')],
            'history':[{'ref':ref,'path':a['payload'].get('path'),'status':a['report'].get('status','superseded')}
                for ref,a in snapshot['artifacts'].items() if a['type']=='report' and a['experiment_id']==eid and ref!=e['report_ref']]})
    return rows


def execution_context(store, experiment_ids=None):
    """Bounded real output and file inventory for planning, review and writing."""
    from services.file_services.workspace_layout import workspace_path
    snapshot=store.snapshot()
    rows=experiment_context(snapshot,experiment_ids)
    for row in rows:
        for file in row['files']:
            if '/artifacts/' not in file['path']:continue
            run_id=snapshot['artifacts'][file['id']]['payload']['run_id']
            public='outputs/research/'+run_id+'/'+file['path'].split('/artifacts/',1)[1]
            if (store.root/public).is_file():file['path']=public
        # Scalar registration records are historical only; the current report
        # and actual files are sufficient for new calculations.
        outputs=[]
        for file in [f for f in row['files'] if f['kind']=='stdout'][-6:]:
            path=workspace_path(store.root,file['path'])
            if path.is_file():
                with path.open('r',encoding='utf-8',errors='replace') as stream:
                    text=stream.read(6001)
                outputs.append({'path':file['path'],'excerpt':text[:6000],'truncated':len(text)>6000})
        row['outputs']=outputs
    return rows


def evidence_uses(snapshot, source_root=None):
    uses = []
    native=snapshot['spec'].get('output_mode')=='latex'
    sections=snapshot['sections']
    if native:
        if source_root is None:return []
        from services.chat_services.paper_project import files
        sections={name:{'text':row['text'],'content_hash':row['version'],'validity':'current'}
                  for name,row in files(source_root,main='paper.tex').items() if name.endswith('.tex')}
    for key, section in sections.items():
        if section['validity']!='current':continue
        text=section['text']
        refs=list(dict.fromkeys(find_refs(text)))
        if native:
            refs += re.findall(r'paperagent\.local/results/([RF]\d+)',text)
            refs += re.findall(r'\\label\{fig:(F\d+)\}',text)
            refs += [ref for group in re.findall(r'(?m)^% paperagent:evidence ([RF\d,]+)$',text) for ref in group.split(',')]
            refs=list(dict.fromkeys(refs))
        for ref in refs:
            entry = snapshot['artifacts'].get(ref)
            claim = snapshot['claims'].get(ref)
            task_ids = list(section.get('task_refs') or [])
            if entry:task_ids=[entry['payload']['subproblem']]
            elif claim:task_ids=[claim['payload']['subproblem']]
            else:continue
            uses.append({'ref':ref,'section':key,'section_version':section['content_hash'],
                'title':key if native else snapshot['experiments'].get(key.removeprefix('solve:'),{}).get('subproblem',{}).get('title') or {'abstract':'摘要','restatement':'问题重述','analysis':'问题分析','assumptions':'研究假设','symbols':'符号说明','sensitivity':'敏感性分析','evaluation':'评价与局限','references':'参考文献'}.get(key,'研究正文'),'task_ids':task_ids,
                'quote':'文件级实验依据，未定位到具体句子。' if native else next((line.strip() for line in text.splitlines() if ref in line),'')[:600]})
    return uses


def execution(snapshot, work_status=None, source_root=None):
    from services.chat_services.conversation_timeline import execution_projection
    from services.file_services.workspace_fs import WorkspaceFS
    fs=WorkspaceFS(source_root) if source_root is not None else None
    active=work_status in {'in_progress','running','processing'} or (work_status is None and snapshot['outcome']=='running')
    actions=[]
    for item in snapshot.get('activity',[])[-120:]:
        row=execution_projection(source_root,snapshot['research_id'],item,fs=fs)
        if row['status']=='running' and not active:row['status']='interrupted'
        actions.append(row)
    current=next((row for row in reversed(actions) if row['status']=='running'),None)
    return {'actions':actions,'decisions':snapshot.get('decision_history',[])[-40:],
            'current':current, 'updated_at':max(snapshot.get('updated_at', 0), snapshot.get('activity_updated_at', 0))}


def research_history(store, snapshot):
    """Important journal facts only. Missing historical clocks/links stay unknown."""
    import json
    labels={'question_updated':'问题已更新','experiment_created':'已安排实验','experiment_continued':'继续实验',
            'delivery_requested':'开始交付','attempt_committed':'实验执行结束',
            'inputs_changed':'输入已改变','research_revised':'研究要求已修改',
            'recovery_requested':'恢复研究','action_failed':'操作失败',
            'clarification_requested':'等待补充信息','action_interrupted':'研究已暂停',
            'action_prepared':'暂停研究','activity_finished':'研究已暂停'}
    with store._connect() as db:
        rows=db.execute('SELECT seq,kind,payload FROM journal WHERE research_id=? AND kind IN ('+
                        ','.join('?' for _ in labels)+") AND (kind!='activity_finished' OR json_extract(payload,'$.status')='interrupted') AND (kind!='action_prepared' OR json_extract(payload,'$.capability')='pause') ORDER BY seq DESC LIMIT 100",
                        (store.research_id,*labels)).fetchall()
    known = {json.loads(row['payload']).get('id') for row in rows if row['kind'] == 'activity_finished'}
    with store._connect() as db:
        rows = list(rows) + [{'seq': 'activity:' + row['id'], 'kind': 'activity_finished',
                             'payload': row['payload']} for row in db.execute(
            "SELECT id,payload FROM research_activity WHERE research_id=? AND json_extract(payload,'$.status')='interrupted' ORDER BY updated_at DESC LIMIT 100",
            (store.research_id,)) if row['id'] not in known]
    result=[]
    for row in rows:
        data=json.loads(row['payload']);kind=row['kind']
        if kind=='action_prepared' and data.get('capability')!='pause':continue
        if kind=='activity_finished' and data.get('status')!='interrupted':continue
        eid=data.get('experiment_id') or data.get('task_id')
        if not eid and data.get('attempt_id'):
            eid=(snapshot['executions'].get(data['attempt_id']) or {}).get('task_id')
        question_id=data.get('question_id')
        refs=data.get('artifact_refs',[])
        if kind=='attempt_committed':
            refs=[ref for ref,artifact in snapshot['artifacts'].items()
                  if artifact['producer_attempt']==data.get('attempt_id') and artifact['type']=='result' and artifact['active']]
        result.append({'id':'journal:'+str(row['seq']),'kind':kind,'label':labels[kind],
            'action_id':data.get('action_id'),'observation_id':data.get('observation_id'),
            'question_id':question_id,'experiment_id':eid,'recorded_at':data.get('recorded_at') or data.get('finished_at') or data.get('interruption_detected_at'),
            'reason':data.get('reason') or data.get('message') or data.get('purpose') or '',
            'artifact_refs':refs})
    return sorted(result, key=lambda row: row["recorded_at"] or 0, reverse=True)[:100]


def model_usage(store):
    """Workspace conversation totals, including resumed and earlier research runs."""
    import json
    with store._connect() as db:
        rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM model_requests ORDER BY sequence DESC')]
    def totals(items):
        reported=[r for r in items if r.get('usage_returned')]
        finished=[r for r in items if r.get('finished_at',0)>r['sent_at']]
        completed=[r for r in finished if r.get('state')=='completed']
        seconds=sum(r['finished_at']-r['sent_at'] for r in finished)
        measured=[r for r in completed if r.get('usage_returned')]
        measured_seconds=sum(r['finished_at']-r['sent_at'] for r in measured)
        cached=[r for r in reported if 'cache_read_tokens' in r]
        cache_input=sum(r['input_tokens'] for r in cached)
        hits=sum(r['cache_read_tokens'] for r in cached)
        return {'requests':len(items),'cache_reported_requests':len(cached),
                'unmeasured_requests':sum(bool(r.get('usage_unknown')) or not r.get('usage_returned') for r in finished),
                'reasoning_tokens':sum(r['reasoning_tokens'] for r in reported if r.get('reasoning_tokens') is not None) if any(r.get('reasoning_tokens') is not None for r in reported) else None,
                'known_cost_usd':sum(r['cost_usd'] for r in items if r.get('cost_usd') is not None) if any(r.get('cost_usd') is not None for r in items) else None,
                'input_tokens':sum(r['input_tokens'] for r in reported) if reported else None,
                'output_tokens':sum(r['output_tokens'] for r in reported) if reported else None,
                'request_seconds':seconds,
                'tps':sum(r['output_tokens'] for r in measured)/measured_seconds if measured_seconds else None,
                'cache_read_tokens':hits if cached else None,
                'cache_miss_tokens':max(0,cache_input-hits) if cached else None,
                'cache_hit_rate':hits/cache_input if cache_input else None}
    groups={}
    for row in rows:
        label=row.get('label') or ''
        scope='summary' if label=='context_summary' else 'delegate' if label.startswith('task:') else 'research' if label=='observe' else 'other'
        phase=(row.get('context_usage') or {}).get('cache_phase','unclassified')
        # Summary calls never inherit the parent's stable/cold classification.
        if scope=='summary':phase='unclassified'
        groups.setdefault((scope,phase),[]).append(row)
    return {**totals(rows),'cache_breakdown':[{'scope':scope,'phase':phase,**totals(items)}
                                            for (scope,phase),items in sorted(groups.items())]}


def model_activity(store, work_status):
    # Explicit ownership is frozen at request start; never parse historical labels.
    import json
    with store._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='model_requests'").fetchone():return []
        rows=db.execute('SELECT payload FROM model_requests ORDER BY sequence DESC LIMIT 120').fetchall()
    result=[]
    for raw in rows:
        item=json.loads(raw[0])
        if item.get('research_id')!=store.research_id:continue
        status=item.get('state','unknown')
        if not item.get('finished_at') and work_status not in {'in_progress','running','processing'}:status='interrupted'
        result.append({'id':'model:'+str(item['sequence']),
            **{key:item.get(key) for key in ('action_id','observation_id','question_id','experiment_id','parent_call_id')},
            'task_id':item.get('experiment_id'), 'kind':'model',
            'subject':item.get('subject'),
            'label':item.get('step') or ('接收模型响应' if status=='http_response' else '等待模型响应'),
            'started_at':item['sent_at'],'finished_at':item.get('finished_at'),
            'updated_at':max(item.get('finished_at') or 0,item.get('headers_at') or 0,item['sent_at']),
            'interrupted_at':item.get('interrupted_at'), 'status':status,
            'input_tokens':item.get('input_tokens'),'output_tokens':item.get('output_tokens'),
            'cache_read_tokens':item.get('cache_read_tokens'),'context_usage':item.get('context_usage'),
            'reasoning_tokens':item.get('reasoning_tokens'),'generation_limit':item.get('generation_limit'),
            'finish_reason':(item.get('diagnostic') or {}).get('finish_reason')})
    return result


from contextlib import contextmanager
from contextvars import ContextVar

_ACTIVITY_OWNER = ContextVar('paperagent_activity_owner', default=None)


def _activity_error(item, exc):
    from .review_cache import error_detail
    item.update(status='interrupted' if type(exc).__name__=='CancelledError' else 'failed',
                error_type=type(exc).__name__, message=error_detail(exc))

def activity_progress(ctx, item):
    """Persist a bounded execution preview; full logs stay in run files."""
    from .runtime import notify_runtime
    store = getattr(ctx, 'research', None)
    if store is None:
        return
    store.record_activity(item)
    from services.chat_services.conversation_timeline import execution_boundary
    execution_boundary(store.root, store.research_id, 'activity', item)
    notify_runtime()


def activity_metadata(ctx, capability, **output):
    """Enrich the existing tool row; direct helper calls create no extra activity."""
    owner = _ACTIVITY_OWNER.get()
    if owner and owner[0] is ctx and owner[1]['capability'] == capability:
        item = owner[1]
        item['output'] = {**item.get('output', {}), **output}
        activity_progress(ctx, item)

@contextmanager
def activity(ctx, capability, task_id=None, *, reuse_parent=None, **links):
    """One visible owner per operation; reuse is explicit and context-local.

    A Python executor can enrich its registry call, while independent branches
    and standalone executors retain their own records. Only the owner finishes.
    """
    import time
    from uuid import uuid4
    from .runtime import execution_scope,execution_links,notify_runtime
    owner = _ACTIVITY_OWNER.get()
    if (reuse_parent and owner and owner[0] is ctx and owner[1]['capability']==reuse_parent
            and owner[1]['id']==execution_links().get('parent_call_id')
            and owner[1]['status']=='running' and not owner[1].get('finished_at')):
        item=owner[1]
        item.update({key:value for key,value in links.items() if key!='inputs' and value is not None})
        try:
            activity_progress(ctx,item)
            yield item
        except BaseException as exc:
            _activity_error(item,exc)
            raise
        return
    store=getattr(ctx,'research',None)
    snapshot=store.snapshot() if store is not None else None
    if snapshot is None:store=None
    if task_id is not None:
        experiment=(snapshot or {}).get('experiments',{}).get(task_id) or {}
        links.update(experiment_id=task_id,question_id=experiment.get('question_id'))
    question=((snapshot or {}).get('questions',{}).get(links.get('question_id')) or {})
    experiment=((snapshot or {}).get('experiments',{}).get(links.get('experiment_id')) or {})
    subject=(experiment.get('subproblem') or {}).get('title') or question.get('title') or ''
    item={'id':uuid4().hex,'capability':capability,'task_id':task_id,
          'parent_call_id':execution_links().get('parent_call_id'),
          **execution_links(),'subject':subject,**links,'started_at':time.time(),'status':'running'}
    with execution_scope(**{**links,'step':LABELS.get(capability,links.get('step')),'subject':subject},events=getattr(ctx,'events',None),
                         research_id=store.research_id if store else None,parent_call_id=item['id']):
        if store:
            store.record_activity(item)
            from services.chat_services.conversation_timeline import execution_boundary
            execution_boundary(store.root, store.research_id, 'activity', item)
            notify_runtime()
        token = _ACTIVITY_OWNER.set((ctx,item))
        try:
            yield item
        except BaseException as exc:
            _activity_error(item,exc)
            raise
        finally:
            _ACTIVITY_OWNER.reset(token)
            item['finished_at']=time.time()
            if item['status']=='running':item['status']='completed'
            if store:
                store.record_activity(item)
                from services.chat_services.conversation_timeline import execution_boundary
                execution_boundary(store.root, store.research_id, 'activity', item)
                notify_runtime()


def delivery_summary(snapshot, stages=None):
    """Derived only from current durable stages; no second business state."""
    stages = snapshot.get('delivery_stages', {}) if stages is None else stages
    # New deliveries expose one operation; old stage records remain readable.
    terminal = 'export' if 'export' in stages or snapshot.get('spec', {}).get('output_mode') != 'markdown' else 'render'
    required = ('export',) if 'export' in stages else ('write','validate','render')
    failed = next((name for name in required
                   if stages.get(name, {}).get('status') in {'blocked', 'failed', 'interrupted'}), None)
    labels = {'write': '报告撰写', 'validate': '报告校验', 'render': '报告发布', 'export': '报告导出'}
    if failed:
        return {'state': 'incomplete', 'stage': failed, 'label': labels[failed] + '未完成，已有计算和文件已保留'}
    current = next((name for name in required if stages.get(name, {}).get('status') == 'in_progress'), None)
    if current:
        return {'state': 'running', 'stage': current, 'label': labels.get(current, '报告处理') + '中'}
    if stages.get(terminal, {}).get('status') == 'completed':
        return {'state': 'completed', 'stage': terminal, 'label': '报告已生成，可查看和下载'}
    if stages.get('render', {}).get('status') == 'completed':
        return {'state': 'draft', 'stage': 'render', 'label': '正文已保存，导出尚未完成'}
    return {'state': 'pending', 'stage': None, 'label': '报告尚未生成'}


def completed_work_status(root):
    """Called after the graph exits; an ended coroutine is not a delivered report."""
    from .store import ResearchStore
    import json
    from pathlib import Path
    thread = workspace_path(Path(root), '.system/graph_thread.json')
    if not thread.is_file(): return 'paused'
    research_id = json.loads(thread.read_text())['thread_id']
    state = ResearchStore(root, research_id, read_only=True).snapshot() or {}
    if state.get('outcome') == 'completed':
        return 'completed'
    action = state.get('action') or {}
    if action.get('capability') == 'ask' and action.get('status') == 'waiting':
        return 'waiting'
    return 'paused'
