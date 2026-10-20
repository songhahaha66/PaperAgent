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
    groups = {}
    for key, task in snapshot['tasks'].items():
        if task['retired'] or task['kind']=='verify':continue
        # Historical tasks are not guessed into one problem. New experiments
        # explicitly share research_question via the intent compiler.
        statement = task.get('research_question') or task['objective']
        identity = digest(task.get('question_key') or key)[:24]
        row = groups.setdefault(identity,{'id':identity,'question':statement,
            'title':statement if task.get('research_question') else task['subproblem'].get('title',statement),
            'task_ids':[],'sources':[],'judgment':'','next_step':'','issues':[],'judgments':[]})
        row['task_ids'].append(key)
        for req in snapshot['spec']['requirements']:
            if key in req['task_ids'] and req['source_quote'] not in row['sources']:row['sources'].append(req['source_quote'])
        report = task.get('report') or {}
        if report.get('summary'):row['judgment']=report['summary']
        issues = [i for i in findings(snapshot) if i['task_id']==key and i['status'] not in CLOSED]
        row['issues'] += [{'id':i['id'],'message':i['message'],'next_step':issue_next_step(i,snapshot)} for i in issues]
        row['judgments'] += [j for j in snapshot.get('judgments',[]) if j['task_id']==key]
    for row in groups.values():
        row['next_step']=' '.join(dict.fromkeys(i['next_step'] for i in row['issues']))
        row['judgments']=row['judgments'][-8:]
    return list(groups.values())


def evidence_uses(snapshot):
    uses = []
    for key, section in snapshot['sections'].items():
        if section['validity']!='current':continue
        text=section['text']
        refs=list(dict.fromkeys(find_refs(text)))
        for ref in refs:
            entry = snapshot['evidence'].get(ref)
            claim = snapshot['claims'].get(ref)
            task_ids = list(section.get('task_refs') or [])
            if entry:task_ids=[entry['payload']['subproblem']]
            elif claim:task_ids=[claim['payload']['subproblem']]
            else:continue
            uses.append({'ref':ref,'section':key,'section_version':section['content_hash'],
                'title':snapshot['tasks'].get(key.removeprefix('solve:'),{}).get('subproblem',{}).get('title') or {'abstract':'摘要','restatement':'问题重述','analysis':'问题分析','assumptions':'研究假设','symbols':'符号说明','sensitivity':'敏感性分析','evaluation':'评价与局限','references':'参考文献'}.get(key,'研究正文'),'task_ids':task_ids,
                'quote':next((line.strip() for line in text.splitlines() if ref in line),'')[:600]})
    return uses


def execution(snapshot, work_status=None):
    paused=work_status in {'paused','cancelled','failed','waiting'} or (work_status is None and snapshot['outcome']=='blocked')
    actions=[]
    for item in snapshot.get('activity',[])[-60:]:
        row={**item,'label':LABELS.get(item['capability'],'研究操作')}
        if row['status']=='running' and paused:row['status']='interrupted'
        actions.append(row)
    return {'actions':actions,'decisions':snapshot.get('decision_history',[])[-12:]}


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
        label=item.get('label','');task=re.match(r'Q\d+[a-z]?',label)
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
