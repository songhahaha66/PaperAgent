"""Scoped child sessions using the same Agent, budget and execution infrastructure."""
import asyncio
import hashlib
import json
import shutil
from typing import Literal
from pydantic import Field
from langchain_core.messages import HumanMessage
from .registry import Inputs,CapabilityProduct


class DelegateInput(Inputs):
    task: str = Field(min_length=1,description='明确的任务范围、输出要求及不能改变的条件。')
    task_id: str | None = Field(default=None,description='继续已有子任务时使用返回的task_id。')
    context_mode: Literal['isolated','fork'] = 'isolated'
    files: list[str] = Field(default_factory=list,description='子任务必需的原始资料或已保存结果文件路径。')


async def delegate(ctx, inputs):
    from langchain.agents import create_agent
    from langchain.agents.middleware import wrap_tool_call
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import StructuredTool
    from ai_system.graph.context import GraphContext
    from ai_system.graph.compaction import ContextMiddleware
    from ai_system.modules.experiments.execution import build_tools,export_solution_code
    from ai_system.research.session import session_saver,run_session
    from ai_system.research.runtime import execution_links,execution_scope
    from services.file_services.workspace_layout import workspace_path
    from ai_system.research.feedback import read_artifact
    store=ctx.research
    tid=inputs['task_id'] or hashlib.sha256((execution_links().get('action_id') or inputs['task']).encode()).hexdigest()[:20]
    if len(tid)!=20 or any(c not in '0123456789abcdef' for c in tid):raise ValueError('task_id无效，请使用返回的子任务编号')
    key='delegation:'+tid
    with store._connect() as db:
        row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,key)).fetchone()
    record=json.loads(row[0]) if row else None
    if inputs['task_id'] and record is None:raise ValueError('子任务不存在')
    sources={}
    for name in inputs['files']:
        if name.split('/')[0] not in {'data','attachment','outputs','code'} and name not in {'paper.md','paper.tex','paper-sources.bib'}:
            raise ValueError('子任务只能接收公开资料、结果、代码或正文，不能复制内部状态')
        path=ctx.fs.canonical_artifact(name)
        if not path.is_file() or path.stat().st_size>20_000_000:raise ValueError('子任务资料不存在或超过20MB：'+name)
        sources[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    if record:
        # Follow-ups reuse the same frozen materials and conversation.
        for name,version in record['inputs'].items():
            path=ctx.fs.canonical_artifact(name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=version:
                raise ValueError('子任务输入已变化，请创建新任务；历史结果未合并为当前结论')
        if sources and sources!=record['inputs']:raise ValueError('已有子任务不能更换输入，请创建新任务')
        sources=record['inputs']
        if record['status']=='completed' and record['task']==inputs['task']:return CapabilityProduct('research_state',record)
    folder=workspace_path(store.root,'.system/tasks/'+tid+'/workspace')
    folder.mkdir(parents=True,exist_ok=True)
    local=GraphContext.create(str(folder),planner_llm=ctx.planner_llm,coder_llm=ctx.coder_llm,
                              experience_scope=ctx.experience_scope)
    local.research=store
    for name in sources:
        target=local.fs.abs(name)
        if not record:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ctx.fs.canonical_artifact(name),target)
        local.fs.register_file(name,kind='data',visibility='user')
    record={**(record or {}),'task_id':tid,'task':inputs['task'],'context_mode':inputs['context_mode'],
            'inputs':sources,'initial_task':(record or {}).get('initial_task',inputs['task']),'status':'running'}
    def save():
        with store._transaction() as db:
            db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',(store.research_id,key,tid,json.dumps(record,ensure_ascii=False)))
    save()
    runs=[]
    def remember_run(run_id):
        local.fs.get_run_info(run_id).update(research_id=store.research_id, scope='delegated', task_id=tid)
        local.fs._save_manifest()
        action=execution_links().get('action_id')
        if action:
            with store._transaction() as db:
                db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                    (store.research_id,'task-run:'+action,action,json.dumps({'run_id':run_id})))
    tools=build_tools(local,'task-'+tid,runs,kernel_key='task-'+tid,on_run=remember_run)
    tool_lock=asyncio.Lock()
    @wrap_tool_call
    async def receipts(request,handler):
        call=request.tool_call;identity=tid+':'+call['id'];response_key='task-response:'+identity
        async with tool_lock:
            with store._connect() as db:
                response=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,response_key)).fetchone()
                submitted=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,'task-run:'+identity)).fetchone()
            if response:return ToolMessage.model_validate(json.loads(response[0]))
            if submitted:
                run_id=json.loads(submitted[0])['run_id'];info=local.fs.get_run_info(run_id) or {}
                output=local.fs.read_text(info['stdout']) if info.get('stdout') and local.fs.exists(info['stdout']) else ''
                message=ToolMessage(content='复用已有运行'+run_id+'，未重放代码。状态：'+str(info.get('status','unknown'))+'\n'+output[-6000:],tool_call_id=call['id'],name=call['name'])
            else:
                try:
                    with execution_scope(action_id=identity):message=await handler(request)
                except (ValueError,KeyError,FileNotFoundError) as exc:
                    message=ToolMessage(content=str(exc),tool_call_id=call['id'],name=call['name'],status='error')
            with store._transaction() as db:
                db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                    (store.research_id,response_key,identity,json.dumps(message.model_dump(),ensure_ascii=False,default=str)))
            return message
    async def read(path:str,start:int=0,limit:int=12000):
        value=await read_artifact(local,{'path':path,'task_id':None,'start':start,'limit':min(max(limit,1),24000)})
        return value.value
    tools=[t for t in tools if t.name!='read_file']+[StructuredTool.from_function(coroutine=read,name='read_file',description='按需读取分配的资料或本任务产物，返回文本及版本。')]
    prompt=('你是研究主Agent委派的子任务执行者。只处理分配的范围；可读取指定材料、运行Python、检索资料。'
            '不要改写主论文，不再委派。返回实际结论、使用的条件、结果文件路径、已做的校验、错误和未知。'
            '执行成功不代表结论正确，检查失败必须明确说明。不要假定恢复后Python变量还存在。')
    history=list(getattr(ctx,'agent_messages',[])) if inputs['context_mode']=='fork' and not row else []
    messages=[*history,HumanMessage(content=json.dumps({'task':inputs['task'],'files':sources,'runtime_note':'本次调用使用新Python上下文；已有文件仍在，读取必要代码和文件，不重放已完成运行。'},ensure_ascii=False))]
    try:
        if record.get('model_task')!=inputs['task'] or 'answer' not in record:
            from ai_system.graph.tracing import labelled
            with execution_scope(step='独立子任务',subject=tid),labelled('task:'+tid):
                async with session_saver(ctx) as saver:
                    agent=create_agent(model=ctx.coder_llm,tools=tools,system_prompt=prompt,checkpointer=saver,
                                       middleware=[ContextMiddleware(local,ctx.coder_llm,store.research_id+'-task-'+tid,
                                           pinned={'initial_task':record['initial_task'],'follow_up':inputs['task'],'files':sources}),receipts])
                    result=await run_session(agent,store.research_id+'-task-'+tid,messages)
            record.update(answer=str(result['messages'][-1].content),model_task=inputs['task'],status='delivering')
            save()
        export_solution_code(local,'task',runs)
        # Reuse the existing immutable run publisher, including logs and code.
        from ai_system.sandbox.artifacts import capture_run_files
        for name,version in sources.items():
            if hashlib.sha256(ctx.fs.canonical_artifact(name).read_bytes()).hexdigest()!=version:
                raise ValueError('子任务执行期间输入发生变化，结果保留在子任务目录，未合并')
        published=capture_run_files(local.fs,ctx.fs)
        code='code/task_solution.py'
        if local.fs.exists(code):
            target='outputs/tasks/'+tid+'/solution.py'
            ctx.fs.write_text(target,local.fs.read_text(code))
            ctx.fs.register_file(target,kind='code',created_by='ai',visibility='user')
            published.append(target)
        for target in published:await ctx.events.file_changed(target)
        inventory=local.fs._manifest.get('runs',{})
        run_states=[{'run_id':rid,'status':info.get('status'),'failure_kind':info.get('failure_kind'),
                     'error':info.get('error')} for rid,info in inventory.items()]
        unknown=any(r['failure_kind']=='transport_unknown' for r in run_states)
        latest=run_states[-1] if run_states else None
        execution_status='interrupted' if unknown else 'failed' if latest and latest['status']!='success' else 'succeeded'
        record.update(status='completed' if execution_status=='succeeded' else execution_status,
                      execution_status=execution_status,files=published,run_ids=list(inventory),runs=run_states)
        if execution_status!='succeeded':
            record['execution_warning']='计算失败或执行结果未确认；模型回答不构成执行成功的回执。请以runs与实际files为准，不得声称校验通过。'
        return CapabilityProduct('research_state',record,'completed' if execution_status=='succeeded' else execution_status,
                                 record.get('execution_warning'))
    except BaseException:
        record['status']='interrupted'
        raise
    finally:
        save()
        await local.kernels.close()


def register(registry):
    registry.register('research','research.delegate','research_state',delegate,DelegateInput,model_callable=True,
        purpose='按需委派独立调查、计算或复核，返回实际结论与文件；isolated独立上下文，fork继承当前会话。指定files；用task_id继续原子任务。连续建模纠错由主会话完成。')
