"""Version research topics independently of their runs and manuscript."""
from copy import deepcopy
from .models import Contract, ResearchConflict
from pydantic import Field


class QuestionUpdate(Contract):
    question_id: str | None = None
    base_version: int = Field(default=1,ge=1)
    question: str = Field(min_length=1)
    title: str = ""
    reason: str = ""
    source_question_ids: list[str] = Field(default_factory=list)
    experiment_ids: list[str] = Field(default_factory=list)
    retire_source_ids: list[str] = Field(default_factory=list)


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
            if not q:
                raise ValueError('问题编号不存在；新建请使用research_create_question，修改请使用已有编号。')
            if q.get('version', 1) != update.base_version:
                raise ResearchConflict(f"问题版本已改变，当前版本为{q.get('version', 1)}；请读取后再修改。")
            linked=list(dict.fromkeys([*q.get('related_experiment_ids',[]),*update.experiment_ids,
                *(e['task_id'] for e in state['experiments'].values() if e['question_id'] in sources)]))
            if (q['question']==update.question and q['title']==(update.title or update.question)
                and linked==q.get('related_experiment_ids',[]) and not update.retire_source_ids
                and sources<=set(q.get('source_question_ids',[])) and not q.get('retired')):
                resolved.append(key);continue
            previous = {k:deepcopy(v) for k,v in q.items() if k != 'history'}
            q.setdefault('history', []).append(previous)
            q.update(version=q.get('version',1)+1, question=update.question, title=update.title or update.question)
        else:
            index = len(state['questions']) + 1
            while 'Q' + str(index) in state['questions'] or 'Q' + str(index) in state['experiments']:index += 1
            key = 'Q' + str(index)
            q = {'id':key, 'question':update.question, 'title':update.title or update.question,
                 'version':1, 'history':[]}
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


def delivery_status(state):
    """File readiness is checked by delivery, never by topic bookkeeping."""
    return {'ready':True,'blockers':[]}
