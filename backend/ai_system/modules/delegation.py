"""Scoped child sessions using the same Agent, budget and execution infrastructure."""
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from typing import Literal
from pydantic import Field
from langchain_core.messages import HumanMessage
from .registry import Inputs,CapabilityProduct


class DelegateInput(Inputs):
    task: str = Field(min_length=1,description='明确的任务范围、输出要求及不能改变的条件。')
    task_id: str | None = Field(default=None,description='继续已有子任务时使用返回的task_id。')
    context_mode: Literal['isolated','fork'] = 'isolated'
    files: list[str] = Field(default_factory=list,description='子任务必需的原始资料或已保存结果文件路径。')
    purpose: Literal['research','document_review'] = 'research'
    source_version: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    pdf_sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')


async def delegate(ctx, inputs):
    if inputs.get('purpose')=='document_review':return await review_document(ctx,inputs)
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
    if record and record.get('purpose')=='document_review':raise ValueError('只读复核子任务不能改为计算任务，请创建新任务')
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
    aliases={name:('outputs/imported/'+version+'/'+Path(name).name if name.startswith('outputs/research/') else name)
             for name,version in sources.items()}
    for name in sources:
        target=local.fs.abs(aliases[name])
        if not record:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ctx.fs.canonical_artifact(name),target)
        local.fs.register_file(aliases[name],kind='data',visibility='user')
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
    messages=[*history,HumanMessage(content=json.dumps({'task':inputs['task'],'files':sources,'local_paths':aliases,'runtime_note':'新子任务使用独立Python上下文；继续已有子任务时优先重连原上下文，未确认恢复时只复用文件。不要重放已完成运行。'},ensure_ascii=False))]
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
        await local.kernels.disconnect()


async def review_document(ctx, inputs):
    """An optional read-only task in the existing session/checkpoint/budget system."""
    import copy
    from langchain.agents import create_agent
    from langchain_core.tools import StructuredTool
    from langchain_core.messages import ToolMessage
    from langchain.agents.middleware import wrap_tool_call
    from ai_system.graph.compaction import ContextMiddleware
    from ai_system.research.session import session_saver,run_session
    from ai_system.research.runtime import execution_links,execution_scope
    from ai_system.research.files import resolve,text_file
    from ai_system.sandbox.content_store import ContentStore
    from services.chat_services import paper_project as project
    from services.file_services.workspace_layout import workspace_path
    from .writing.document import render_pages,tool_content
    version,pdf_hash=inputs['source_version'],inputs['pdf_sha256']
    if not version or not pdf_hash:raise ValueError('先writing_preview取得实际source_version和pdf_sha256，再委派只读复核')
    archive=workspace_path(ctx.workspace,project.VERSIONS)/version
    build=archive/'builds'/f'{pdf_hash}.json'
    if not build.is_file():raise ValueError('指定版本没有已完成的PDF构建')
    tid=inputs['task_id'] or hashlib.sha256((execution_links().get('action_id') or json.dumps(inputs,sort_keys=True)).encode()).hexdigest()[:20]
    if len(tid)!=20 or any(c not in '0123456789abcdef' for c in tid):raise ValueError('task_id无效')
    store=ctx.research;key='delegation:'+tid
    with store._connect() as db:row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,key)).fetchone()
    previous=json.loads(row[0]) if row else None
    if inputs['task_id'] and previous is None:raise ValueError('复核子任务不存在')
    if previous and (previous.get('purpose'),previous.get('source_version'),previous.get('pdf_sha256'))!=('document_review',version,pdf_hash):
        raise ValueError('子任务不能换版本，请创建新复核任务')
    content=ContentStore(ctx.workspace,20_000_000)
    sources=dict(previous.get('inputs',{})) if previous else {}
    if previous and set(inputs['files'])-set(sources):raise ValueError('已有复核任务不能更换材料')
    if not previous:
        for name in inputs['files']:sources[name]=await asyncio.to_thread(content.put,resolve(ctx,name))
    record={**(previous or {}),'task_id':tid,'purpose':'document_review','task':inputs['task'],
            'source_version':version,'pdf_sha256':pdf_hash,'inputs':sources,'status':'running'}
    def save():
        with store._transaction() as db:db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
            (store.research_id,key,tid,json.dumps(record,ensure_ascii=False)))
    if previous and previous.get('status')=='completed' and previous['task']==inputs['task']:
        return CapabilityProduct('research_state',{**previous,'current':project.snapshot(ctx.workspace)['version']==version})
    manifest=json.loads((archive/'manifest.json').read_text());seen=set(record.get('reviewed_pages',[]))
    async def pages(pages:list[int]):
        if not 1<=len(pages)<=4:raise ValueError('每次读取1至4页')
        value=await asyncio.to_thread(render_pages,ctx.workspace,version,pdf_hash,pages)
        seen.update(pages);record['reviewed_pages']=sorted(seen);save()
        return value
    async def read(path:str,start:int=0,limit:int=12000):
        if path in manifest:source=archive/path
        elif path in sources:source=content.path(sources[path])
        else:raise ValueError('只能读取固定版本源码与指定材料')
        _,text=await asyncio.to_thread(text_file,source,Path(path).suffix)
        start=max(0,start);limit=min(max(limit,1),24000)
        return {'path':path,'source_version':version,'excerpt':text[start:start+limit],'total_chars':len(text)}
    tools=[StructuredTool.from_function(coroutine=pages,name='view_pdf_pages',description='读取固定PDF的实际页面；每次1至4页，页码从1开始。'),
           StructuredTool.from_function(coroutine=read,name='read_file',description='读取固定版本正文和分配材料，返回文本。')]
    @wrap_tool_call
    async def visual(request,handler):
        # StructuredTool serializes dictionaries; images hydrate only for the model.
        try:message=await handler(request)
        except (ValueError,KeyError,FileNotFoundError) as exc:
            return ToolMessage(content=str(exc),tool_call_id=request.tool_call['id'],name=request.tool_call['name'],status='error')
        if request.tool_call['name']=='view_pdf_pages':
            value=json.loads(message.content)
            return message.model_copy(update={'content':tool_content(ctx,value,ctx.coder_llm)})
        return message
    local=copy.copy(ctx);local.agent_messages=[]
    prompt=('你是主研究Agent按需调用的只读论文复核者，复用同一总预算。只检查指定固定版本，不能编辑正文、运行计算或再委派。'
            '先查看实际PDF页，再按疑点读取源码和指定材料。区分排版、原要求缺项、数值与计算口径疑点；没有图像能力时明确仅做文本复核。'
            '返回具体问题：source_version、pdf_sha256、页码、严重程度、类别、观察到什么、支持材料、建议主Agent怎么核实；说明已看页码与未检查范围。'
            '没有依据不能说计算通过。建议仅供主Agent判断，不生成改稿或自动验收结论。')
    save()
    try:
        with execution_scope(step='论文只读复核',subject=tid):
            async with session_saver(ctx) as saver:
                agent=create_agent(model=ctx.coder_llm,tools=tools,system_prompt=prompt,checkpointer=saver,
                    middleware=[ContextMiddleware(local,ctx.coder_llm,store.research_id+'-review-'+tid,pinned={
                        'source_version':version,'pdf_sha256':pdf_hash,'files':list(manifest),'materials':sources}),visual])
                result=await run_session(agent,store.research_id+'-review-'+tid,[HumanMessage(content=inputs['task'])])
        record.update(status='completed',answer=str(result['messages'][-1].content),reviewed_pages=sorted(seen))
    except BaseException:
        record['status']='interrupted';raise
    finally:save()
    return CapabilityProduct('research_state',{**record,'current':project.snapshot(ctx.workspace)['version']==version,
        'advisory':True,'note':'复核意见绑定该版本；由主Agent决定修改，未自动判定论文通过。'})


def register(registry):
    registry.register('research','research.delegate','research_state',delegate,DelegateInput,model_callable=True,
        purpose='按需委派调查、计算或只读PDF复核，复用现有会话和总预算。document_review需writing_preview返回的source_version和pdf_sha256；只能看固定版本与指定材料，不能改论文或运行代码。用task_id继续子任务。')
