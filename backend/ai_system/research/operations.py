"""Small research operations, each committed independently through ModuleRegistry."""
import json
import re
from pydantic import Field
from ai_system.modules.registry import Inputs, CapabilityProduct
from .models import ResearchConflict
from .questions import QuestionUpdate, apply_questions
from .domain import experiment_scope
from .store import digest


class QuestionCreate(Inputs):
    question: str = Field(min_length=1, description='用户要回答的一个实质问题；新建不提供编号。')
    title: str = ''


class QuestionEdit(QuestionCreate):
    question_id: str = Field(description='使用已返回的真实问题编号，如Q2。')
    base_version: int = Field(ge=1, description='读取到的当前问题version，用于保护并发修改。')


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


async def deliver(ctx,inputs):
    main=ctx.research.root/('paper.tex' if ctx.output_mode=='latex' else 'paper.md')
    if not main.is_file() or not main.read_text().strip():
        raise ValueError('请先用writing_edit保存正文，再交付。')
    from ai_system.modules.registry import capabilities
    product=await capabilities().execute('writing.export',{},ctx)
    return product.value


def ask_user(ctx,inputs):return _message(ctx,inputs,'waiting')
def pause(ctx,inputs):return _message(ctx,inputs,'paused')
def _message(ctx,inputs,kind):
    def edit(state):
        from .runtime import execution_links
        from uuid import uuid4
        request={'id':execution_links().get('tool_call_id') or uuid4().hex,'message':inputs['message']}
        state['pending_input']=request if kind=='waiting' else None
        return {'control':kind,**request}
    return _change(ctx,'input_requested' if kind=='waiting' else 'pause_requested',inputs,edit)


def read_question(ctx,inputs):
    from .views import questions,question_context,experiment_context
    state=ctx.research.snapshot()
    q=next((q for q in questions(state) if q['id']==inputs['question_id']),None)
    if not q:raise ValueError('没有该问题，请使用工具实际返回的question_id')
    scope=experiment_scope(state['experiments'],set(q['experiment_ids']))
    from .runs import run_context
    return {'question_id':q['id'],'question':question_context([q])[0],'experiments':experiment_context(state,scope),
            'runs':run_context(ctx.research, q['id'])}


def register(registry):
    from .plan import PLAN_TOOL_DESCRIPTION, PlanUpdate, update_plan
    from .runtime_tools import register as register_runtime
    register_runtime(registry)
    def register_tool(name,handler,inputs,purpose):
        async def run(ctx,values):
            import inspect
            value=handler(ctx,values)
            return CapabilityProduct('research_state',await value if inspect.isawaitable(value) else value)
        registry.register('research','research.'+name,'research_state',run,inputs,model_callable=True,purpose=purpose)
    register_tool('update_plan',update_plan,PlanUpdate,PLAN_TOOL_DESCRIPTION)
    register_tool('create_question',update_question,QuestionCreate,'新建一个用户问题，返回系统分配的编号；不接收question_id。可在同轮为多问分别调用。')
    register_tool('edit_question',update_question,QuestionEdit,'修改已有问题，须提供返回的question_id与当前base_version。')
    async def legacy_question(ctx, values):return CapabilityProduct('research_state', update_question(ctx, values))
    registry.register('research','research.update_question','research_state',legacy_question,QuestionUpdate)
    register_tool('read_question',read_question,QuestionTarget,'读取一个问题及相关实验的当前记录、有效结果、版本与历史定位；不重算。')
    register_tool('deliver',deliver,DeliveryInput,'正文已由当前Agent保存后交付；系统只检查文件、引用、编译与导出，不重新写作。')
    register_tool('ask_user',ask_user,MessageInput,'仅在缺少用户必须提供的信息或决定时提问并等待回应。')
    register_tool('pause',pause,MessageInput,'无可行继续路径时明确停止原因，保存已有成果并暂停。')
