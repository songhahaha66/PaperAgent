"""Small research operations, each committed independently through ModuleRegistry."""
import json
import time
from typing import Literal
from pydantic import Field
from ai_system.modules.registry import Inputs, CapabilityProduct
from .models import ExperimentPlan, ResearchConflict
from .questions import QuestionUpdate, apply_questions
from .domain import experiment_scope, current_result_refs
from .store import digest


class ExperimentInput(Inputs):
    question_id: str
    plan: ExperimentPlan
    title: str = ''
    artifact_refs: list[str] = Field(default_factory=list)
    input_files: list[str] | None = Field(default=None, description='本实验需要的原始文件路径；省略表示全部，空列表表示仅使用题内数据。系统只提供这些原始文件，并按其版本判断失效。')
    reason: str = ''


class ContinueInput(Inputs):
    experiment_id: str
    information_need: str = ''


class AnswerUpdate(Inputs):
    question_id: str
    statement: str = ''
    artifact_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_step: str = ''
    coverage: Literal['answered','partial','unanswered'] | None = None
    stop_reason: str = ''


class DeliveryInput(Inputs):
    title: str = ''
    reason: str = ''


class MessageInput(Inputs):
    message: str = Field(min_length=1)


class QuestionTarget(Inputs):
    question_id: str


def _change(ctx,kind,inputs,edit):
    """Use the existing command receipts for idempotent state operations only."""
    from .runtime import execution_links,CURRENT_EXECUTION
    store=ctx.research;links=execution_links();key=CURRENT_EXECUTION.get().get('tool_call_id')
    signature=digest([kind,inputs])
    with store._transaction() as db:
        if key:
            old=db.execute('SELECT digest,response FROM commands WHERE research_id=? AND id=?',(store.research_id,key)).fetchone()
            if old:
                if old['digest']!=signature:raise ResearchConflict('该工具调用已按另一组参数提交，请读取当前记录')
                return json.loads(old['response'])
        state=store._read(db)
        expected=CURRENT_EXECUTION.get().get('expected_revision')
        if expected is not None and state['revision']!=expected:
            raise ResearchConflict('研究版本已更新，请读取当前记录后再操作')
        result=edit(state)
        if not result.get('unchanged'):
            state['revision']+=1
            store._save(db,state,kind,{**links,**result})
        result={**result,'revision':state['revision']}
        if key:db.execute('INSERT INTO commands VALUES (?,?,?,?)',(store.research_id,key,signature,json.dumps(result,ensure_ascii=False)))
    return result


def update_question(ctx,inputs):
    def edit(state):
        before=digest(state['questions'])
        qid=apply_questions(state,[QuestionUpdate.model_validate(inputs)])[0]
        return {'question_id':qid,'question':state['questions'][qid], 'unchanged':before==digest(state['questions'])}
    return _change(ctx,'question_updated',inputs,edit)


def _dispatchable(state):
    if (state.get('action') or {}).get('status') in {'planned','running','waiting'}:
        raise ResearchConflict('已有行动等待执行或回应，请先处理该行动，再安排其它实验或交付')


def attempt_budget_error(state, task=None):
    if len(state['executions']) >= state['max_total_attempts']:
        return '研究已达到总执行预算；请用已有结果更新答案，明确缺口后交付，不再安排计算。'
    if task is not None:
        count = sum(a['task_id'] == task['task_id'] and a['revision'] == task['revision']
                    for a in state['executions'].values())
        if count >= state['max_attempts']:
            return f"实验 {task['task_id']} 已达到尝试预算；请复用已有文件和结果，记录未完成部分后交付，不要换编号重复计算。"
    return None


def schedule_experiment(ctx,inputs):
    def edit(state):
        _dispatchable(state)
        if error := attempt_budget_error(state): raise ResearchConflict(error)
        q=state['questions'].get(inputs['question_id'])
        if not q or q.get('retired'):raise ValueError('question_id必须使用建立问题工具实际返回的当前编号')
        refs=inputs['artifact_refs']
        if not set(refs)<=current_result_refs(state,set(state['experiments'])):
            raise ValueError('依赖包含未知或已失效的结果，请读取当前问题/实验结果')
        selected_files = inputs.get('input_files')
        if selected_files is not None and not set(selected_files) <= set(ctx.research.file_inputs()):
            raise ValueError('input_files 包含未知原始文件，请使用输入目录中的真实路径')
        plan=ExperimentPlan.model_validate(inputs['plan'])
        key='E'+str(max((int(k[1:]) for k in state['experiments'] if k.startswith('E') and k[1:].isdigit()),default=0)+1)
        dependencies=list(dict.fromkeys(state['artifacts'][ref]['experiment_id'] for ref in refs))
        owners=set(dependencies)|{eid for eid,e in state['experiments'].items() if e['question_id']==q['id']}
        requirement_ids=[r['id'] for r in state['spec']['requirements'] if owners.intersection(r['task_ids'])]
        if not requirement_ids:requirement_ids=[r['id'] for r in state['spec']['requirements']]
        title=inputs['title'] or plan.candidates[0].name
        task={'task_id':key,'question_id':q['id'],'revision':1,'objective':q['question'],
            'source':state['spec']['requirement']+'\n'+'\n'.join(state['spec']['amendments']),
            'dependencies':dependencies,'requirement_ids':requirement_ids,'assumption_ids':[],
            'kind':'compute','retired':False,'execution_status':'pending','acceptance':'pending','validity':'current',
            'execution_id':None,'report_ref':None,'method_instruction':'；'.join(c.instruction for c in plan.candidates),
            'experiment':plan.model_dump(),'investigates':[],'reason':inputs['reason'],
            'subproblem':{'id':key,'title':title,'question':q['question'],'kind':'compute','depends_on':dependencies,'deliverables':[]}}
        if selected_files is not None: task['input_files'] = sorted(set(selected_files))
        state['experiments'][key]=task
        for requirement in state['spec']['requirements']:
            if requirement['id'] in requirement_ids:requirement['task_ids'].append(key)
        ctx.research._bind_assumption_inputs(state)
        state['publication']=None
        from .loop import _set_action
        action=_set_action(state,'experiments.execute',{},inputs['reason'],target=key)
        return {'experiment_id':key,'question_id':q['id'],'action_id':action['id']}
    return _change(ctx,'experiment_created',inputs,edit)


def continue_experiment(ctx,inputs):
    def edit(state):
        _dispatchable(state)
        task=state['experiments'].get(inputs['experiment_id'])
        if not task or task['retired'] or task['validity']!='current':raise ValueError('实验不存在或已失效，请读取当前实验')
        if error := attempt_budget_error(state, task): raise ResearchConflict(error)
        if task['execution_status']=='running':raise ResearchConflict('实验尚在执行，不能重复启动')
        if task.get('execution_id'):
            task['continuation']={'parent_attempt':task['execution_id'],'information_need':inputs['information_need']}
        task['execution_status']='pending'
        from .loop import _set_action
        action=_set_action(state,'experiments.execute',{},inputs['information_need'],target=task['task_id'])
        return {'experiment_id':task['task_id'],'question_id':task['question_id'],'action_id':action['id']}
    return _change(ctx,'experiment_continued',inputs,edit)


def update_answer(ctx,inputs):
    def edit(state):
        q=state['questions'].get(inputs['question_id'])
        if not q or q.get('retired'):raise ValueError('答案须关联当前已建立的问题')
        own={eid for eid,e in state['experiments'].items() if e['question_id']==q['id'] and not e['retired']}|set(q.get('related_experiment_ids',[]))
        allowed=current_result_refs(state,experiment_scope(state['experiments'],own))
        if not set(inputs['artifact_refs'])<=allowed:raise ValueError('答案只能引用关联实验及依赖的当前有效结果')
        from ai_system.graph.registry import find_refs
        if not set(find_refs(inputs['statement']))<=set(inputs['artifact_refs']):raise ValueError('答案正文引用须同时列入artifact_refs并属于当前实验范围')
        basis={ref:state['artifacts'][ref]['content_hash'] for ref in inputs['artifact_refs']}
        interpretations={state['artifacts'][ref]['experiment_id']:state['experiments'][state['artifacts'][ref]['experiment_id']]['report_ref'] for ref in inputs['artifact_refs']}
        values={k:v for k,v in inputs.items() if k!='question_id'}
        values['judgment']=values.pop('statement')
        from .questions import answer_gaps
        gaps=answer_gaps({**q,**values,'answer_status':'current','unavailable_artifact_refs':[]})
        status='needs_update' if gaps else 'current'
        unchanged=(all(q.get(k)==v for k,v in values.items()) and q.get('basis_versions',{})==basis
            and q.get('interpretation_versions',{})==interpretations and q.get('answer_status')==status)
        if not unchanged:
            q.update(values,answer_status=status,basis_versions=basis,interpretation_versions=interpretations)
            q['judgments'].append({**inputs,'recorded_at':time.time()})
        return {'question_id':q['id'],'judgments':[inputs],'artifact_refs':inputs['artifact_refs'],'answer_status':status,'answer_gaps':gaps,
                'repair_tool':'research_update_answer' if gaps else None,'unchanged':unchanged}
    return _change(ctx,'research_observed',inputs,edit)


def deliver(ctx,inputs):
    def edit(state):
        _dispatchable(state)
        from .questions import delivery_status
        status=delivery_status(state)
        if not status['ready']:
            raise ValueError('尚不能交付：'+'；'.join(b['message'] for b in status['blockers'])+
                             ' 请先用对应工具修复；仅修改交付 reason 不会补全答案。')
        from .loop import _set_action
        from .runtime import execution_links
        observation_id=execution_links().get('observation_id')
        state['publication']={'stage':'write','outline':[],'title':inputs['title'],'reason':inputs['reason'],
            'repair_inputs':[],'repairs':0,'observation_id':observation_id}
        action=_set_action(state,'writing.write',{},inputs['reason'],observation_id=observation_id)
        return {'action_id':action['id'],'reason':inputs['reason']}
    return _change(ctx,'delivery_requested',inputs,edit)


def ask_user(ctx,inputs):return _message(ctx,inputs,'ask')
def pause(ctx,inputs):return _message(ctx,inputs,'pause')
def _message(ctx,inputs,name):
    def edit(state):
        _dispatchable(state)
        from .loop import _set_action
        action=_set_action(state,name,{'message':inputs['message']},inputs['message'])
        return {'action_id':action['id'],'reason':inputs['message']}
    return _change(ctx,'action_prepared',inputs,edit)


def read_question(ctx,inputs):
    from .views import questions,question_context,experiment_context
    state=ctx.research.snapshot()
    q=next((q for q in questions(state) if q['id']==inputs['question_id']),None)
    if not q:raise ValueError('没有该问题，请使用工具实际返回的question_id')
    scope=experiment_scope(state['experiments'],set(q['experiment_ids']))
    return {'question_id':q['id'],'question':question_context([q])[0],'experiments':experiment_context(state,scope)}


def register(registry):
    def register_tool(name,handler,inputs,purpose):
        async def run(ctx,values):return CapabilityProduct('research_state',handler(ctx,values))
        registry.register('research','research.'+name,'research_state',run,inputs,model_callable=True,purpose=purpose)
    register_tool('update_question',update_question,QuestionUpdate,'建立或版本化修改一个用户问题，返回真实question_id。可关联已有实验、拆合来源并保留历史。')
    register_tool('schedule_experiment',schedule_experiment,ExperimentInput,'为真实question_id安排新方法/条件的实验。plan使用已有ExperimentPlan；普通实验一个候选。返回实验编号后转入执行层。')
    register_tool('continue_experiment',continue_experiment,ContinueInput,'继续或重试已有实验，保持方法和条件；补充有价值行动，复用已有计算。换方法须新实验。')
    register_tool('update_answer',update_answer,AnswerUpdate,'保存问题判断、依据、局限及覆盖状态。完整回答须有实际引用；缺项返回answer_gaps，可原地补全，不重算。')
    register_tool('read_question',read_question,QuestionTarget,'读取一个问题及相关实验的当前记录、有效结果、版本与历史定位；不重算。')
    register_tool('deliver',deliver,DeliveryInput,'各问题已明确回答/部分/未答及停止原因后开始研究级论文交付；不创建写作实验。')
    register_tool('ask_user',ask_user,MessageInput,'仅在缺少用户必须提供的信息或决定时提问并等待回应。')
    register_tool('pause',pause,MessageInput,'无可行继续路径时明确停止原因，保存已有成果并暂停。')
