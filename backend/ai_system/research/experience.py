"""Deterministic experience cards. Historical advice never becomes current evidence."""
from services.file_services.workspace_layout import workspace_path

from ai_system.research.domain import findings as research_findings
import csv
import json
import re
import hashlib
from pathlib import Path

from .store import digest
from .validation import protocol


def conditions(root, question):
    files = [p for folder in ('data','attachment') for p in (Path(root)/folder).rglob('*') if p.is_file()]
    columns = set()
    for path in files[:20]:
        if path.suffix.lower()=='.csv' and path.stat().st_size<=2_000_000 and not path.is_symlink():
            try:
                with path.open(encoding='utf-8-sig') as f:
                    columns.update(c.strip()[:80] for c in next(csv.reader([f.readline(8192)])) if c.strip())
            except (OSError,UnicodeError,csv.Error,StopIteration):
                pass
    text=question.lower()
    structure = ('time_series' if re.search(r'时间序列|time series',text) else
                 'integer' if re.search(r'整数变量|整数规划|integer (?:variables|programming)',text) else
                 'continuous' if re.search(r'连续变量|连续实数|continuous (?:variables|real)',text) else 'unknown')
    return {'data_mode':'tabular' if columns else 'files' if files else 'self_contained',
            'columns':sorted(columns)[:40], 'structure':structure}


def problem_type(methods, question):
    if re.search(r'线性规划|linear programming',question,re.I):return 'linear_programming'
    if re.search(r'回归|regression',question,re.I):return 'regression'
    if re.search(r'时间序列|time series',question,re.I):return 'time_series'
    return next(iter(methods),'unspecified')


def grounded(root,state,refs):
    if root is None:return []
    root=Path(root).resolve()
    try:
        if (workspace_path(root, 'manifest.json')).stat().st_size>4_000_000:return []
        manifest=json.loads((workspace_path(root, 'manifest.json')).read_text())
        from ..graph.registry import value_in_output
        result=[]
        for ref in refs:
            row=state['artifacts'][ref]['payload'];run=manifest['runs'][row['run_id']]
            if run['status']!='success':return []
            path=workspace_path(root,run['stdout'])
            if not run.get('evidence_archived') or path.stat().st_size>4_000_000:return []
            raw=path.read_bytes()
            if row['kind']=='figure' or not value_in_output(str(row['value']),raw.decode('utf-8',errors='replace')):return []
            result.append({'id':ref,'run_id':row['run_id'],'stdout_hash':hashlib.sha256(raw).hexdigest()})
        return result
    except (OSError,ValueError,KeyError,TypeError):return []


def card_for(state, attempt_id, root=None):
    a=state['executions'][attempt_id]
    if not a.get('commit'):return None
    result=a.get('result',{});task=state['experiments'][a['task_id']]
    frozen=a.get('experience_basis',{})
    question=frozen.get('question',task['objective'])
    design=result.get('design') or {}
    chosen=design.get('chosen') or {}
    methods=sorted(set(chosen.get('method_keys') or design.get('methods') or []))
    validation=a.get('validation') or {}
    rules=frozen.get('protocol')
    if rules is None and task['revision']==a['revision']:
        rules=protocol(state,a['task_id'])
    checks=validation.get('checks') or []
    refs=sorted({r for c in checks for r in c['evidence_refs']})
    provenance=grounded(root,state,refs)
    check_keys=lambda rows:{(r['check_id'],r['owner_kind'],r['owner_id'],r['owner_revision']) for r in rows}
    expected={(r['check']['id'],r['owner_kind'],r['owner_id'],r['owner_revision']) for r in (rules or [])}
    producers={attempt_id,*[v for k,v in a['input_refs'].items() if k.startswith('task:')]}
    empirical=bool(checks and rules and provenance and provenance==a.get('experience_evidence')
        and validation.get('protocol_hash')==a['protocol_hash']==digest(rules)
        and len(checks)==len(expected) and check_keys(checks)==expected
        and all(c['evidence_refs'] and all(ref in state['artifacts'] and
            state['artifacts'][ref]['producer_attempt'] in producers and
            state['artifacts'][ref]['content_hash']==digest(state['artifacts'][ref]['payload'])
            for ref in c['evidence_refs']) for c in checks))
    current=bool(a['commit']['published'] and task['revision']==a['revision'] and task['execution_id']==attempt_id
        and task['validity']=='current' and a.get('validity','current')=='current' and not task['retired'])
    related={a['task_id']}
    while True:
        expanded=related|{dep for key in related for dep in state['experiments'][key]['dependencies']}
        if expanded==related:break
        related=expanded
    blocking=any(i['blocking'] and i['status'] not in {'resolved','dismissed','superseded'} and
        (i['task_id'] in related or related.intersection(i.get('repair_targets',{}))) for i in research_findings(state))
    execution=result.get('execution_status')
    if empirical and any(c['outcome']=='failed' for c in checks):level='counterexample'
    elif empirical and validation.get('outcome')=='accepted' and all(c['outcome']=='passed' for c in checks) and current and not blocking and execution=='succeeded':level='verified'
    elif execution=='failed':level='execution_error'
    else:level='unconfirmed'
    cancelled=bool(re.search(r'timeout|timed out|cancel|超时|执行超过.{0,12}秒|取消|中断',str(result.get('report',{}).get('error','')),re.I))
    if cancelled or execution=='cancelled':level='unconfirmed'
    earlier=sorted({i['producer_attempt'] for i in research_findings(state) if i.get('producer_attempt')
        and i.get('resolution_attempt')==attempt_id and i['status']=='resolved'})
    repaired_by=[i['resolution_attempt'] for i in research_findings(state) if i.get('producer_attempt')==attempt_id and i.get('resolution_attempt')]
    return {'problem_type':problem_type(methods,question),'question':question[:600],'method_keys':methods,
        'data_conditions':frozen.get('conditions',{'data_mode':'unknown','structure':'unknown','columns':[]}),
        'steps':chosen.get('steps',[])[:8], 'observations':[c['message'][:500] for c in checks][:8],
        'error':str(result.get('report',{}).get('error',''))[:800], 'level':level,
        'applicable':['仅在方法和已知数据条件匹配时参考；所有当前要求须重新执行检查'],
        'not_applicable':['不同数据结构、变量类型或方法；不能用历史结果替代本次证据',
                          '超时、取消或未确认观察不能证明方法无效'],
        'unknown_conditions':['未记录的分布、规模、单位和领域前提均待核验'],
        'source':{'research_id':state['research_id'],'task_id':a['task_id'],'attempt_id':attempt_id,
            'revision':a['revision'],'input_digest':a['input_digest'],'input_refs':a['input_refs'],
            'protocol_hash':a['protocol_hash'],'published':a['commit']['published']},
        'checks':checks,'evidence_refs':refs,'evidence_provenance':provenance,'repairs':earlier if level=='verified' else [],
        'repaired_by':sorted(set(repaired_by)), 'source_current':current,'blocking':blocking}


def tokens(text):
    latin=re.findall(r'[a-z0-9_]{3,}',text.lower())
    chinese=re.findall(r'[\u4e00-\u9fff]+',text)
    return set(latin+[word[i:i+2] for word in chinese for i in range(len(word)-1)])


def compatible(known, actual):
    for key in ('data_mode','structure'):
        if known.get(key,'unknown')!='unknown' and actual.get(key,'unknown')!='unknown' and known[key]!=actual[key]:return False
    return not known.get('columns') or not actual.get('columns') or set(known['columns'])<=set(actual['columns'])
