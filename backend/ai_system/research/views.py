"""Shared read-only question, provenance and execution projections."""
import re
from .store import digest
from .domain import findings, experiment_scope, current_result_refs
from .progress import issue_next_step
from .questions import answer_gaps
from ai_system.graph.registry import find_refs

CLOSED = {'resolved','dismissed','superseded'}
LABELS = {'research.update_question':'更新研究问题','research.schedule_experiment':'安排实验',
    'research.continue_experiment':'继续实验','research.update_answer':'更新问题答案','research.read_question':'读取问题与依据',
    'research.deliver':'交付研究','research.ask_user':'请求必要信息','research.pause':'暂停研究','research.tool_error':'工具调用未完成',
    'research.observe':'判断下一步','experiments.inspect':'查看输入材料','experiments.execute':'执行实验',
    'research.revise_interpretation':'修订实验解释','research.check_interpretation':'核查实验解释','research.read_artifact':'读取已有证据','literature.collect':'获取文献证据',
    'writing.write':'撰写论文',
    'writing.validate':'检查论文','writing.repair':'修订论文',
    'writing.render':'渲染论文','writing.export':'导出论文','run_python':'运行实验代码',
    'read_file':'读取已有文件','check_interpretation':'核查实验解释'}


def questions(snapshot):
    """Read-only question projection; preserve history for existing clients.

    Empty judgments stay empty. Available results never imply an answer, and
    recorded judgments retain their limitations and unresolved issues.
    """
    rows=[]
    for question in snapshot['questions'].values():
        experiments=[e for e in snapshot['experiments'].values() if e['question_id']==question['id'] and not e['retired']]
        ids={e['task_id'] for e in experiments} | set(question.get('related_experiment_ids', []))
        experiments=[e for eid,e in snapshot['experiments'].items() if eid in ids]
        scope=experiment_scope(snapshot['experiments'], ids)
        available=current_result_refs(snapshot, scope)
        issues=[i for i in findings(snapshot) if i['task_id'] in ids and i['status'] not in CLOSED]
        stale = (any(snapshot['artifacts'].get(ref,{}).get('content_hash') != version for ref,version in question.get('basis_versions',{}).items())
                 or any(snapshot['experiments'].get(eid,{}).get('report_ref') != version for eid,version in question.get('interpretation_versions',{}).items()))
        row={**question,
            'answer_status':'needs_update' if stale else question.get('answer_status','current' if question.get('judgment') else 'unanswered'),
            'experiment_ids':[e['task_id'] for e in experiments],
            'dependency_experiment_ids':sorted(scope - ids),
            'available_artifact_refs':sorted(available),
            'unavailable_artifact_refs':[ref for ref in question['artifact_refs'] if ref not in available],
            'sources':list(dict.fromkeys(r['source_quote'] for r in snapshot['spec']['requirements'] if ids.intersection(r['task_ids']))),
            'issues':[{'id':i['id'],'message':i['message'],'next_step':issue_next_step(i,snapshot)} for i in issues]}
        row['answer_gaps']=answer_gaps(row)
        if row['answer_gaps'] and (row.get('judgment') or row.get('coverage') in {'answered','partial'}):row['answer_status']='needs_update'
        rows.append(row)
    return rows


def question_context(rows, *, include_history=False):
    """Share current judgments and their evidence scope without report/history copies."""
    fields = ('id', 'title', 'question', 'judgment', 'artifact_refs', 'limitations', 'next_step',
              'experiment_ids', 'dependency_experiment_ids', 'available_artifact_refs',
              'unavailable_artifact_refs', 'sources', 'issues', 'answer_status', 'coverage', 'stop_reason', 'answer_gaps', 'retired', 'version')
    if include_history:fields += ('history','judgments')
    return [{key: row[key] for key in fields if key in row} for row in rows]



def result_context(snapshot,item):
    """Read the existing value, declared scope and immutable execution provenance."""
    artifact=snapshot['artifacts'][item['id']]
    attempt=snapshot['executions'][artifact['producer_attempt']]
    return {**item,'reference':'{{'+('F' if item['kind']=='figure' else 'R')+':'+item['id']+'}}',
            'producer_attempt':artifact['producer_attempt'],'content_hash':artifact['content_hash'],
            'input_refs':attempt['input_refs']}


def experiment_context(snapshot, experiment_ids=None):
    from .domain import experiment_report, experiment_design, current_results
    from .feedback import interpretation_version
    selected=set(experiment_ids) if experiment_ids is not None else {
        eid for eid,e in snapshot['experiments'].items() if not e['retired']}
    rows=[]
    results=current_results(snapshot,selected)
    for eid,e in snapshot['experiments'].items():
        if eid not in selected:continue
        artifact=snapshot['artifacts'].get(e['report_ref']) or {}
        rows.append({'task_id':eid,'question_id':e['question_id'],'objective':e['objective'],
            'execution_status':e['execution_status'],'acceptance':e['acceptance'],'validity':e['validity'],
            'method':e.get('method_instruction',''),
            'adopted_method':(experiment_design(snapshot,e) or {}).get('chosen'),
            'conditions':(e.get('experiment') or {}).get('conditions',''),
            'evaluation':(e.get('experiment') or {}).get('evaluation',''),
            'record':experiment_report(snapshot,e),'record_path':artifact.get('payload',{}).get('path'),
            'interpretation_version':interpretation_version(snapshot,e),
            'dependencies':list(e['dependencies']),
            'results':[result_context(snapshot,r) for r in results if r['subproblem']==eid],
            'history':[{'ref':ref,'path':a['payload'].get('path'),'status':a['report'].get('status','superseded')}
                for ref,a in snapshot['artifacts'].items() if a['type']=='report' and a['experiment_id']==eid and ref!=e['report_ref']]})
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


def execution(snapshot, work_status=None):
    active=work_status in {'in_progress','running','processing'} or (work_status is None and snapshot['outcome']=='running')
    actions=[]
    for item in snapshot.get('activity',[])[-120:]:
        row={**item,'label':LABELS.get(item['capability'],'研究操作')}
        if row['status']=='running' and not active:row['status']='interrupted'
        actions.append(row)
    current=next((row for row in reversed(actions) if row['status']=='running'),None)
    return {'actions':actions,'decisions':snapshot.get('decision_history',[])[-40:],
            'current':current, 'updated_at':snapshot.get('updated_at')}


def research_history(store, snapshot):
    """Important journal facts only. Missing historical clocks/links stay unknown."""
    import json
    labels={'question_updated':'问题已更新','experiment_created':'已安排实验','experiment_continued':'继续实验',
            'delivery_requested':'开始交付','research_observed':'问题判断已更新','attempt_committed':'实验执行结束',
            'inputs_changed':'输入已改变','research_revised':'研究要求已修改',
            'recovery_requested':'恢复研究','action_failed':'操作未完成',
            'clarification_requested':'等待补充信息','action_interrupted':'研究已暂停',
            'action_prepared':'暂停研究','activity_finished':'研究已暂停'}
    with store._connect() as db:
        rows=db.execute('SELECT seq,kind,payload FROM journal WHERE research_id=? AND kind IN ('+
                        ','.join('?' for _ in labels)+") AND (kind!='activity_finished' OR json_extract(payload,'$.status')='interrupted') AND (kind!='action_prepared' OR json_extract(payload,'$.capability')='pause') ORDER BY seq DESC LIMIT 100",
                        (store.research_id,*labels)).fetchall()
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
            'question_id':question_id,'experiment_id':eid,'recorded_at':data.get('recorded_at'),
            'reason':data.get('reason') or data.get('message') or data.get('purpose') or '',
            'artifact_refs':refs,'judgments':data.get('judgments',[])})
    return result


def model_usage(store):
    """Workspace conversation totals, including resumed and earlier research runs."""
    import json
    with store._connect() as db:
        rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM model_requests ORDER BY sequence DESC')]
    reported=[r for r in rows if r.get('usage_returned')]
    completed=[r for r in reported if r['state']=='completed' and r.get('finished_at',0)>r['sent_at']]
    seconds=sum(r['finished_at']-r['sent_at'] for r in completed)
    cached=[r for r in reported if 'cache_read_tokens' in r]
    cache_input=sum(r['input_tokens'] for r in cached)
    return {'input_tokens':sum(r['input_tokens'] for r in reported) if reported else None,
            'output_tokens':sum(r['output_tokens'] for r in reported) if reported else None,
            'tps':sum(r['output_tokens'] for r in completed)/seconds if seconds else None,
            'cache_hit_rate':sum(r['cache_read_tokens'] for r in cached)/cache_input if cache_input else None}


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
            'purpose':item.get('purpose'),
            'label':item.get('step') or ('接收模型响应' if status=='http_response' else '等待模型响应'),
            'started_at':item['sent_at'],'finished_at':item.get('finished_at'),
            'updated_at':max(item.get('finished_at') or 0,item.get('headers_at') or 0,item['sent_at']),
            'interrupted_at':item.get('interrupted_at'), 'status':status,
            'input_tokens':item.get('input_tokens'),'output_tokens':item.get('output_tokens')})
    return result


from contextlib import contextmanager

@contextmanager
def activity(ctx, capability, task_id=None, **links):
    """Persist one actual call; inherit explicit execution ownership, not UI state."""
    import time
    from uuid import uuid4
    from .runtime import execution_scope,execution_links,notify_runtime
    store=getattr(ctx,'experience_store',None) or getattr(ctx,'research',None)
    snapshot=store.snapshot() if store is not None else None
    if snapshot is None:store=None
    if task_id is not None:
        experiment=(snapshot or {}).get('experiments',{}).get(task_id) or {}
        links.update(experiment_id=task_id,question_id=experiment.get('question_id'))
    item={'id':uuid4().hex,'capability':capability,'task_id':task_id,
          'parent_call_id':execution_links().get('parent_call_id'),
          **execution_links(),**links,'started_at':time.time(),'status':'running'}
    with execution_scope(**links,events=getattr(ctx,'events',None),
                         research_id=store.research_id if store else None,parent_call_id=item['id']):
        if store:
            with store._transaction() as db:
                state=store._read(db)
                state['activity']=[*state.get('activity',[]),item][-240:]
                store._save(db,state,'activity_started',item)
            notify_runtime()
        try:
            yield item
        except BaseException as exc:
            item['status']='interrupted' if type(exc).__name__=='CancelledError' else 'failed'
            from .review_cache import error_detail
            item['error_type']=type(exc).__name__
            item['message']=error_detail(exc)
            raise
        finally:
            item['finished_at']=time.time()
            if item['status']=='running':item['status']='completed'
            if store:
                with store._transaction() as db:
                    state=store._read(db)
                    state['activity']=[item if a['id']==item['id'] else a for a in state.get('activity',[])]
                    store._save(db,state,'activity_finished',item)
                notify_runtime()
