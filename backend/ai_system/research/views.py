"""Shared read-only question, provenance and execution projections."""
import re
from .store import digest
from .domain import findings
from .progress import issue_next_step
from ai_system.graph.registry import find_refs

CLOSED = {'resolved','dismissed','superseded'}
LABELS = {'research.observe':'判断下一步','experiments.inspect':'查看输入材料','experiments.execute':'执行实验',
    'research.review_report':'修订与复核报告','literature.collect':'获取文献证据',
    'writing.reconcile':'核对证据与结论','writing.write':'撰写草稿',
    'writing.validate':'检查草稿','writing.repair':'修订草稿',
    'writing.render':'渲染论文','writing.export':'导出论文','run_python':'运行实验代码'}


def questions(snapshot):
    rows=[]
    for question in snapshot['questions'].values():
        experiments=[e for e in snapshot['experiments'].values() if e['question_id']==question['id'] and not e['retired']]
        ids={e['task_id'] for e in experiments}
        issues=[i for i in findings(snapshot) if i['task_id'] in ids and i['status'] not in CLOSED]
        rows.append({**question, 'experiment_ids':[e['task_id'] for e in experiments],
            'sources':list(dict.fromkeys(r['source_quote'] for r in snapshot['spec']['requirements'] if ids.intersection(r['task_ids']))),
            'issues':[{'id':i['id'],'message':i['message'],'next_step':issue_next_step(i,snapshot)} for i in issues]})
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
    paused=work_status in {'paused','cancelled','failed','waiting'} or (work_status is None and snapshot['outcome']=='blocked')
    actions=[]
    for item in snapshot.get('activity',[])[-60:]:
        row={**item,'label':LABELS.get(item['capability'],'研究操作')}
        if row['status']=='running' and paused:row['status']='interrupted'
        actions.append(row)
    return {'actions':actions,'decisions':snapshot.get('decision_history',[])[-12:]}


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
    # Read only. No budget migration or connection metadata enters the response.
    import json
    with store._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='model_requests'").fetchone():return []
        rows=db.execute('SELECT payload FROM model_requests ORDER BY sequence DESC LIMIT 60').fetchall()
    result=[]
    for raw in rows:
        item=json.loads(raw[0])
        if item.get('research_id')!=store.research_id:continue
        label=item.get('label','');task=re.match(r'(?:E\d+|Q\d+[a-z]?)',label)
        phase=label.split(':')[-1]
        phase={'review':'核查实验结果','report':'修订报告','solve':'推导与计算',
            'design':'确定执行方法','research':'判断下一步','observe':'判断下一步'}.get(phase,'处理研究内容')
        status='recorded' if item.get('usage_recorded') and not item.get('finished_at') else item.get('state','unknown')
        if not item.get('finished_at') and work_status not in {'in_progress','running','processing'}:status='interrupted'
        result.append({'id':str(item['sequence']),'task_id':task[0] if task else None,
            'label':phase,'started_at':item['sent_at'],'finished_at':item.get('finished_at'),
            'status':status,'input_tokens':item.get('input_tokens'),'output_tokens':item.get('output_tokens')})
    return result


from contextlib import contextmanager

@contextmanager
def activity(ctx, capability, task_id=None):
    """An observation of the existing executor, never another scheduler."""
    import time
    from uuid import uuid4
    store=getattr(ctx,'experience_store',None) or getattr(ctx,'research',None)
    if store is not None and store.snapshot() is None:store=None
    item={'id':uuid4().hex,'capability':capability,'task_id':task_id,
        'started_at':time.time(),'status':'running'}
    if store:
        with store._transaction() as db:
            state=store._read(db)
            state['activity']=[*state.get('activity',[]),item][-120:]
            store._save(db,state,'activity_started',item)
    try:
        yield item
    except BaseException as exc:
        item['status']='interrupted' if type(exc).__name__=='CancelledError' else 'failed'
        item['error_type']=type(exc).__name__
        raise
    finally:
        item['finished_at']=time.time()
        if item['status']=='running':item['status']='completed'
        if store:
            with store._transaction() as db:
                state=store._read(db)
                state['activity']=[item if a['id']==item['id'] else a for a in state.get('activity',[])]
                store._save(db,state,'activity_finished',item)
