"""Version questions independently of experiments; reuse evidence across scope changes."""
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


def answer_gaps(question):
    """One completeness rule for saved judgments, delivery and read views."""
    gaps=[];coverage=question.get('coverage')
    if coverage not in {'answered','partial','unanswered'}:
        gaps.append('尚未说明问题已答、部分回答或未答。')
    if not str(question.get('stop_reason') or '').strip():
        gaps.append('缺少停止理由：说明现有答案的范围，或为何仍有未知。')
    if coverage in {'answered','partial'} and not str(question.get('judgment') or '').strip():
        gaps.append('缺少当前答案；无答案时可明确标为未答并说明原因。')
    if question.get('unavailable_artifact_refs'):
        gaps.append('答案引用已失效或不在关联实验范围，请读取当前结果并更新答案引用。')
    elif question.get('answer_status')=='needs_update' and not gaps:
        gaps.append('问题范围、依据或解释已改变，请重新评估并更新问题答案。')
    return gaps


def delivery_status(state):
    from .views import questions
    from .domain import findings
    rows=[q for q in questions(state) if not q.get('retired')]
    blockers=[{'task_id':q['id'],'message':message,'action':'research','tool':'research_update_answer'}
              for q in rows for message in q['answer_gaps']]
    if not rows:blockers.append({'task_id':None,'message':'尚未建立用户要回答的问题。','action':'research','tool':'research_update_question'})
    blockers.extend({'task_id':i['task_id'],'message':i['message'],'action':i['action']}
        for i in findings(state) if i.get('repair_targets') and i['blocking'] and i['status'] not in {'resolved','dismissed','superseded'})
    return {'ready':not blockers,'blockers':blockers,
        'questions':[{k:q.get(k) for k in ('id','title','coverage','stop_reason','answer_status','answer_gaps')} for q in rows]}
