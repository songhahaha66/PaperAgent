"""Version questions independently of experiments; reuse evidence across scope changes."""
from copy import deepcopy
from .models import ResearchConflict


def projected_questions(snapshot, updates):
    state = deepcopy(snapshot)
    apply_questions(state, updates)
    return state['questions']


def apply_questions(state, updates):
    resolved=[]
    for update in updates:
        if not set(update.experiment_ids) <= set(state['experiments']):
            raise ValueError('问题关联了未知实验')
        sources = set(update.source_question_ids)
        if not sources <= set(state['questions']) or not set(update.retire_source_ids) <= sources:
            raise ValueError('拆合来源或停用的问题无效')
        key = update.question_id
        if key:
            q = state['questions'].get(key)
            if not q or q.get('version', 1) != update.base_version:
                raise ResearchConflict('问题版本已改变')
            linked=list(dict.fromkeys([*q.get('related_experiment_ids',[]),*update.experiment_ids,
                *(e['task_id'] for e in state['experiments'].values() if e['question_id'] in sources)]))
            if (q['question']==update.question and q['title']==(update.title or update.question)
                and linked==q.get('related_experiment_ids',[]) and not update.retire_source_ids
                and sources<=set(q.get('source_question_ids',[])) and not q.get('retired')):
                resolved.append(key);continue
            previous = {k:deepcopy(v) for k,v in q.items() if k != 'history'}
            q.setdefault('history', []).append(previous)
            q.update(version=q.get('version',1)+1, question=update.question, title=update.title or update.question,
                     answer_status='needs_update' if q.get('judgment') else 'unanswered')
        else:
            from uuid import uuid4
            key = 'question:' + uuid4().hex[:12]
            q = {'id':key, 'question':update.question, 'title':update.title or update.question,
                 'version':1, 'judgment':'', 'artifact_refs':[], 'limitations':[],
                 'judgments':[], 'next_step':'', 'answer_status':'unanswered', 'history':[]}
            state['questions'][key] = q
        q.update(reason=update.reason, retired=False)
        q['source_question_ids'] = list(dict.fromkeys([*q.get('source_question_ids', []), *update.source_question_ids]))
        q['related_experiment_ids'] = list(dict.fromkeys([*q.get('related_experiment_ids', []), *update.experiment_ids,
            *(e['task_id'] for e in state['experiments'].values() if e['question_id'] in sources)]))
        resolved.append(key)
        for source in update.retire_source_ids:
            if source == key:raise ValueError('当前问题不能停用自身')
            old=state['questions'][source]
            old.update(retired=True, superseded_by=key)

    return resolved
