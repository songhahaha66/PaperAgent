"""One sustained research Agent with scoped records and direct execution."""
import asyncio
import json
from .models import Contract, Requirement, ResearchRevision
from .domain import findings, experiment_scope
from .store import digest
from ai_system.agent.commentary import PROGRESS_PROMPT

PROMPT = """你是持续工作的研究Agent，自己读取材料、运行代码、观察结果、修订方法并回答用户。
默认执行主Agent职责。框架在会话末尾明确本次为fork子任务时，前面的会话只作背景，只完成委派范围；总体计划、正文、用户交互和最终交付仍由主Agent负责，执行权限由框架限制。
读清需求并确定做法后，用research_update_plan列出简短的总体工作计划。完成一个阶段、开始下一阶段或调整方案时更新完整计划，保留已完成项；提问、暂停或最终回复前同步实际状态。已有progress_plan时接着推进，不重新归零。按阶段更新，不为每次读文件或工具调用单列工作项；尚未完成的计算、写作保持待完成。如包含系统导出，将最后一项kind设为delivery并保持pending，系统会按实际交付结果更新它。
工作区文件用research_list_files定位、research_search_files搜索、research_read_artifact分页读取。JSON结果先用view=structure查看实际字段，再以json_pointer读取所需值，例如/checks、/q2/0/optimum；只读与当前疑点有关的数据。正文和日志在工作区工具中可读，Python仅负责计算，不能通过搜索沙箱根目录判断正文是否存在。
先直接用 research_read_artifact 读取原始材料，PDF图表指定pages查看实际页图，无需在计算沙箱安装解析库，不需要实验编号。按需用research_create_question建立研究主题来组织运行；修改已有主题用research_edit_question。用research_run_python在独立Python进程计算；每次执行不共享内存变量，公共逻辑保存到code/模块，数据和模型结果保存为文件后复用；公共数据分析可以省略question_id，不把代码挂到不相关问题下。
research_run_python 返回真实输出、错误和文件路径。复用保存的代码与文件，必要时在代码中核对单位、数据范围、约束和预测时可用信息。实际数值、适用条件、局限和未完成部分直接写入回复及正文。
用research_list_skills查看可用方法指导，按需research_read_skill读取，再用research_run_python完成计算与方法比较；结果表、预测、图表保存为实际文件。方法指导不创建额外实验流程。
普通计算仅保存运行回执，不做实验提交或验收。需要继续就读取已有代码和文件，不为读取、同步或排版重算。
可复用源码用workspace_edit直接保存在code/，参数与单位保存在config/，用research_run_file运行入口；短计算仍可research_run_python。不要为创建或补丁源码把整段程序嵌入Python字符串。数值计算先核对单位、形状与资源规模，先小规模验证再扩大；按需读取numerical_computation技能。
程序故障不证明方法无效，语义核查只供核实。无变化的重复读取、反复重算和没有新信息的核查应停止。
文件同步由系统重试；执行结果未知时不得自动重放代码。恢复后先读取已有日志和结果，不假定旧Python内存还存在。
所有模型请求共用项目运行预算。预算不足时保留已完成成果和未完成部分，不通过换编号或重新开始绕过限制。
用实际工具返回的ID；参数错误在当前上下文纠正。在回复和正文中说明实际完成内容、适用范围与尚未解决的问题。
研究、正文均由你负责：有可用结果就用writing_edit保存提纲与已确定的章节，未完成部分明确标注。长稿每次保存一节：Markdown用append追加；LaTeX先写paper.tex骨架与章节input引用，再逐节写sections/*.tex。恢复时根据document.source_files中已保存的稿件文件继续；已有章节只做必要的局部修订。已有文件先读取，系统自动绑定读取回执的版本保护人工修改，不要复制哈希。多处修订使用edits集中应用。不要用Python生成论文。引用图表前可用writing_use_asset绑定实际文件及版本。原生TeX定稿前用writing_preview查看当前稿件的实际PDF与警告：先看完相关页，再集中修订，修改后重新预览。按需research_delegate purpose=document_review委派只读复核当前稿件，等待复核完成后再修改；复核与主会话共用预算，正文仍由你改。模型无图像能力时明确仅检查PDF文本。编译成功不代表图表或结论正确。正文完成后给出最终回复或research_deliver，进入相同交付流程；明确局限后可以交付部分结果。
需要独立调查或复核时research_delegate：isolated提供明确任务与材料；fork继承已有上下文接手工作。不要将连续纠错拆给多个Agent，子任务结果仍由你整合。
确需用户提供必要信息才 research_ask_user；无可行路径时 research_pause。交付或提问后等待真实结果。
"""


def ready_tasks(snapshot):
    experiments = snapshot['experiments']
    return [key for key, item in experiments.items() if not item['retired']
        and item['execution_status'] == 'pending' and item['validity']=='current'
        and all(experiments[dep]['execution_status']=='succeeded' and experiments[dep]['validity']=='current'
                for dep in item['dependencies'])]


def report_context(report, *, full=False):
    if full:return report
    return {k:report[k] for k in ('summary','key_findings','validation','files','runs','limitations','status','error') if k in report}


def decision_context(store, snapshot):
    from .literature import LiteratureStore
    from ai_system.modules.methods import allowed_method_keys
    from .views import questions,question_context,experiment_context
    files=store.file_inputs()
    rows=[q for q in questions(snapshot) if not q.get('retired')]
    last=snapshot.get('action') or {}
    focused=last.get('question_id')
    selected=next((q for q in rows if q['id']==focused),None)
    own=set(selected['experiment_ids']) if selected else {eid for q in rows for eid in q['experiment_ids']}
    scope=experiment_scope(snapshot['experiments'],own)
    records=experiment_context(snapshot,scope)
    sources=LiteratureStore(store.root).retrieve(selected['question'] if selected else snapshot['spec']['requirement'],limit=12)
    # Current question summaries stay global for goal coverage. Full records and
    # actual values are restricted to the selected question/dependency scope.
    context={'source':snapshot['spec'],'progress_plan':snapshot.get('progress_plan'),'questions':question_context(rows),'current_question_id':selected['id'] if selected else None,
        'experiments':[{**row,'record':report_context(row['record'])} for row in records],
        'literature':sources,'issues':[i for i in findings(snapshot) if (i['task_id'] is None or i['task_id'] in scope) and i['status'] not in {'resolved','dismissed','superseded'}],
        'inputs':files,'materials':snapshot['materials']['profile'] if snapshot['materials']['inputs']==files else '',
        'last_action':{k:last.get(k) for k in ('id','capability','status','question_id','target','reason','message','error_type')},'clarifications':snapshot.get('clarifications',[]),
        'user_message':snapshot.get('conversation_input'),
        'skill_policy':store.skill_preferences()['policy']}
    context['available_method_keys'] = sorted(allowed_method_keys(context['skill_policy']))
    context['recent_operations']=[{k:a.get(k) for k in ('tool_name','question_id','experiment_id','status','message','error_type')} for a in snapshot.get('items',[]) if a.get('tool_name')!='research.observe' and (a.get('question_id') in {None,selected['id'] if selected else None} or a.get('experiment_id') in scope)][-12:]
    if last.get('capability','').startswith('writing.'):
        context['document_diagnostics']=(last.get('result') or {}).get('issues',[])
    from .runs import run_context
    context['runs'] = run_context(store)[-12:]
    context['literature_mode'] = LiteratureStore(store.root).policy()
    main=store.root/('paper.tex' if snapshot['spec'].get('output_mode')=='latex' else 'paper.md')
    if main.is_file():
        import hashlib
        context['document']={'path':main.name,'version':hashlib.sha256(main.read_bytes()).hexdigest()}
    return 'action:' + digest(context), context

def preserved_progress(snapshot):
    runs={a['run_id'] for a in snapshot.get('items', []) if a.get('run_id')}
    if runs:return f'已保留 {len(runs)} 次计算的代码、日志和实际结果文件。'
    count=sum(e['execution_status']=='succeeded' and e['validity']=='current' for e in snapshot['experiments'].values())
    return f'已保留 {count} 项实验成果和全部执行记录。' if snapshot['executions'] else '原题和当前研究记录已保存，尚未开始实验。'


def planning_failure_message(error, snapshot):
    from .runtime import ModelServiceError,ResearchBudgetExhausted,RepeatedOperationBlocked
    from ai_system.config.generation import ModelGenerationLimit
    from ai_system.sandbox.lifetime import readable_sandbox_error
    from starlette.exceptions import HTTPException
    sandbox_text=readable_sandbox_error(error)
    if sandbox_text:
        detail=sandbox_text
    elif isinstance(error,HTTPException) and 400<=error.status_code<500:
        from .review_cache import error_detail
        detail=error_detail(error)
    else:
        detail=str(error) if isinstance(error,(ModelServiceError,ResearchBudgetExhausted,RepeatedOperationBlocked,NoResearchProgress,ModelGenerationLimit)) else '研究控制器未完成下一行动，具体诊断已保存，可继续处理。'
    return detail+' '+preserved_progress(snapshot)


def decision_block_message(store, snapshot):
    action=snapshot.get('action') or {}
    return action.get('message') or action.get('reason') or '研究操作未完成，已有产物已保存。'


from ai_system.agent.loop import NoResearchProgress


def working_context(ctx):
    """Durable state at a conversation boundary; tool receipts carry later changes."""
    state=ctx.research.snapshot()
    from .views import questions,question_context
    from .runs import run_context
    from .files import known_files
    rows=question_context([q for q in questions(state) if not q.get('retired')])
    runs=run_context(ctx.research)[-6:]
    for r in runs:
        if r.get('error'):r['error']=r['error'][:500]
        r['files']=r['files'][:8]
        if r.get('stdout_path') and ctx.fs.exists(r['stdout_path']):
            r['output_tail']=ctx.fs.read_text(r['stdout_path'])[-600:]
    main=ctx.workspace/('paper.tex' if ctx.output_mode=='latex' else 'paper.md')
    import hashlib
    document={}
    if main.is_file():
        names={main.name}|{name for name,row in ctx.fs._manifest['files'].items()
                               if row.get('kind')=='document' and name.endswith(('.md','.tex','.bib'))}
        sources=[]
        for name in sorted(names):
            path=ctx.fs.abs(name)
            if path.is_file():
                raw=path.read_bytes()
                sources.append({'path':name,'version':hashlib.sha256(raw).hexdigest(),'size':len(raw)})
        document={'path':main.name,'version':hashlib.sha256(main.read_bytes()).hexdigest(),
                  'nonempty':bool(main.stat().st_size),
                  'source_files':sources}
    return {'source':{'requirement':state['spec']['requirement'],'amendments':state['spec']['amendments'],
                      'output_mode':state['spec'].get('output_mode')},
            'progress_plan':state.get('progress_plan'),
            'questions':rows,'runs':runs,'files':known_files(ctx)[:30],
            'file_tools':'research_list_files / research_search_files / research_read_artifact；Python沙箱不保证有正文副本',
            'document':document,
            'last_action':{k:(state.get('action') or {}).get(k) for k in ('capability','status','target','message','error_type')},
            'document_diagnostics':(state.get('action') or {}).get('result',{}).get('issues',[])}


class Planner:
    def __init__(self,llm):self.llm=llm

    async def run(self,ctx,context,observation_id):
        from langchain_core.messages import HumanMessage,ToolMessage
        from langchain_core.tools import StructuredTool
        from ai_system.agent.loop import AgentLoop,run_session
        from ai_system.agent.runtime import invoke as invoke_tool,recover_tool
        from ai_system.agent.prompt import ContextManager,state_message,state_update
        from ai_system.graph.llm_utils import message_text
        from ai_system.config.generation import request_model
        from ai_system.modules.registry import capabilities
        from .runtime import execution_scope
        from .review_cache import error_detail
        from .plan import plan_reminder
        registry=capabilities();tools=[];mapping={}
        for capability in sorted(registry.model_capabilities(),key=lambda c:c.name):
            name=capability.name.replace('.','_');mapping[name]=capability.name
            async def call(_name=capability.name,**kwargs):return await registry.execute(_name,kwargs,ctx)
            tools.append(StructuredTool(name=name,description=capability.purpose,args_schema=capability.inputs,
                                        coroutine=call,metadata={'access':capability.access}))
        user_turn=context.get('user_message')
        manuscript_edited=bool(user_turn and user_turn.get('manuscript_edited'))
        def user_content():
            text=user_turn['message']
            if user_turn.get('question_id'):text+='\n当前选中问题：'+user_turn['question_id']
            if user_turn.get('history'):text+='\n近期聊天：'+json.dumps(user_turn['history'],ensure_ascii=False)
            if user_turn.get('paper_context'):
                from services.chat_services.paper_references import context_text
                text+='\n本轮论文引用：'+context_text(user_turn['paper_context'])
                text+='\n引用不是改稿授权。用户要求改稿时，用writing_propose_edits提出修改集，等待接受。'
            if user_turn.get('previous_turn'):text+='\n尚未完成的请求：'+user_turn['previous_turn']['message']
            return text
        failed_export=None
        async def execute(call,*,recover_only=False,queued_at=None):
            nonlocal manuscript_edited,failed_export
            name=call['name'];args=call['args']
            key='research-tool:'+ctx.research.research_id+':'+str(call.get('_message_id','finish'))+':'+call['id']
            if name in {'writing_edit','workspace_edit'}:
                from ai_system.modules.file_edits import observed_version
                args={**args,'expected_version':observed_version(ctx.agent_messages,args.get('path','paper.md'))}
            native={**call,'name':mapping.get(name,name),'id':key,'args':args}
            if recover_only:
                result=await recover_tool(ctx,native)
                if result is None:return ToolMessage(content='新用户消息已到达，此调用尚未提交，未执行。',tool_call_id=call['id'],name=name,status='error')
                return result.model_copy(update={'tool_call_id':call['id'],'name':name})
            exporting=name in {'research_deliver','writing_export'}
            if exporting:
                from services.chat_services.paper_project import snapshot as source_snapshot
                source=source_snapshot(ctx.workspace)['version']
                if source==failed_export:raise NoResearchProgress('正文与诊断没有变化，停止重复交付；草稿和日志已保存。')
            try:
                with execution_scope(action_id=key,observation_id=observation_id):
                    outcome=await invoke_tool(ctx,registry,native['name'],args,call_id=key,
                                              capture_errors=True,queued_at=queued_at)
                result=outcome.value
                if exporting and outcome.kind is not None and (outcome.status!='completed' or any(result.get(key) for key in ('issues','research_blocked','artifact_conflict'))):failed_export=source
                if name=='writing_edit' and outcome.kind is not None:manuscript_edited=True
                control=result.get('control') if isinstance(result,dict) else None
                if name=='research_deliver' and outcome.status=='completed' and not any(result.get(key) for key in ('issues','research_blocked','artifact_conflict')):
                    control='complete'
                content=result if isinstance(result,str) else json.dumps(result,ensure_ascii=False,default=str)
                return ToolMessage(content=content,tool_call_id=call['id'],name=name,
                    status='success' if outcome.status=='completed' else 'error',
                    additional_kwargs={'agent_stop':control} if control else {})
            except (ValueError,KeyError,FileNotFoundError) as exc:
                return ToolMessage(content=error_detail(exc),tool_call_id=call['id'],name=name,status='error')
            except Exception as exc:
                from ai_system.sandbox.lifetime import readable_sandbox_error
                detail=readable_sandbox_error(exc)
                if detail is None:raise
                return ToolMessage(content=detail,tool_call_id=call['id'],name=name,status='error')
        known_context=None;resumed=False
        async def prepare(messages):
            nonlocal known_context,resumed
            from ai_system.modules.experiments.execution import recover_outputs
            from .control import refresh_registry
            await recover_outputs(ctx)
            ctx.research.refresh_inputs();refresh_registry(ctx)
            facts=working_context(ctx);notes=[]
            if resumed:
                notes.append(HumanMessage(content='继续原会话；先核实已有工具回执与后台作业，不重放已提交计算。Python进程不共享变量，读取已保存代码与结果继续。'))
                resumed=False
            update=state_message(facts) if known_context is None else state_update(known_context,facts)
            if update is not None:notes.append(update)
            known_context=facts
            return notes
        async def finish(reply,messages):
            from ai_system.agent.commentary import public_progress
            text='\n\n'.join(public_progress(reply.content)).strip()
            if not text:return '尚无最终回复，请继续处理或说明需要的用户信息。'
            start=next((index+1 for index,message in enumerate(messages) if user_turn and message.id==user_turn['id']),0)
            requested_export=any(isinstance(message,ToolMessage) and message.name in {'writing_export','research_deliver'} and message.status=='success' for message in messages[start:])
            needs_delivery=not user_turn or manuscript_edited or requested_export
            if needs_delivery:
                from services.chat_services.paper_project import snapshot as source_snapshot
                import hashlib
                state=ctx.research.snapshot();main=ctx.workspace/('paper.tex' if ctx.output_mode=='latex' else 'paper.md')
                if not main.is_file() or not main.read_text().strip():return '请先用writing_edit保存正文，再交付。'
                version=source_snapshot(ctx.workspace)['version'] if ctx.output_mode=='latex' else hashlib.sha256(main.read_bytes()).hexdigest()
                required=['paper.tex','paper.pdf'] if ctx.output_mode=='latex' else ['paper.md','paper.docx'] if ctx.output_mode=='word' else ['paper.md']
                if state.get('export_source_version')!=version or any(not (ctx.workspace/name).is_file() for name in required):
                    result=await execute({'name':'research_deliver','args':{'title':state['spec']['requirement'].splitlines()[0][:80],'reason':text[:2000]},'id':'finish:'+reply.id})
                    value=json.loads(result.content) if result.status=='success' else {}
                    if result.status=='error' or value.get('issues') or value.get('research_blocked') or value.get('artifact_conflict'):
                        return '交付尚未完成，请根据诊断修正后继续：'+result.content
            if needs_delivery:text+='\n\n交付文件：'+'、'.join(required)+'。'
            with ctx.research._transaction() as db:
                state=ctx.research._read(db)
                state['outcome']='completed' if needs_delivery else user_turn['outcome']
                state['last_reply']=text;state.pop('conversation_input',None);state['action']=None;state['publication']=None
                ctx.research._save(db,state,'request_completed',{'reply':text})
                if user_turn:db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',(ctx.research.research_id,'conversation:'+user_turn['id'],digest(user_turn['message']),json.dumps({'message':user_turn['message'],'reply':text},ensure_ascii=False)))
            return None
        common={'original_request':context['source']['requirement'],'available_method_keys':context['available_method_keys'],
                'skill_policy':context['skill_policy'],'inputs':context['inputs'],'materials':context['materials'],
                'template_requirements':ctx.template_requirements,'compact_paper':ctx.compact_paper}
        messages=[HumanMessage(content=json.dumps(common,ensure_ascii=False),id='initial-request:'+ctx.research.research_id)]
        if user_turn:messages.append(HumanMessage(content=user_content(),id=user_turn['id']))
        def on_resume(pending):
            nonlocal resumed
            resumed=True
        recorder=ContextManager(ctx,self.llm,ctx.research.research_id+'-lead',tail=lambda messages:plan_reminder(ctx,messages))
        agent=AgentLoop(request_model(self.llm),tools,PROMPT+PROGRESS_PROMPT,ctx=ctx,context=recorder,
                        prepare=prepare,execute=execute,finish=finish)
        result=None
        try:
            result=await run_session(agent,ctx.research.research_id+'-lead',messages,ctx=ctx,on_resume=on_resume,
                                     new_user_turn_id=user_turn['id'] if user_turn else None)
            return result
        finally:
            await ctx.kernels.disconnect()
            from ai_system.agent.store import AgentStore
            history=AgentStore(ctx.research.root).messages(ctx.research.research_id+'-lead')
            if history:
                from ai_system.modules.experiments.execution import save_transcript
                save_transcript(ctx,observation_id.replace(':','-'),history)

    async def amendment(self, snapshot, change, scope):
        from ..graph.llm_utils import call_structured, as_messages
        from ..config.generation import request_model
        from .validation import validate_spec
        if not change.strip():raise ValueError('修改内容不能为空')
        selected = scope if scope is not None else [key for key, t in snapshot["experiments"].items() if t["kind"] != "verify" and not t['retired']]
        payload = {"change": change, "scope": selected,'experiments':[{k:t[k] for k in ('task_id','objective')} for t in snapshot['experiments'].values() if t['task_id'] in selected],
                   "used_requirement_ids": [r["id"] for r in snapshot["spec"]["requirements"]]}
        source='\n'.join([snapshot['spec']['requirement'],*snapshot['spec']['amendments'],change])
        def validate(contract):
            affected={key for r in contract.requirements for key in r.task_ids}
            if not affected or not affected<=set(selected):raise ValueError('修订只能关联实际受影响的已知任务')
            validate_spec(source,{'subproblems':[{'id':key} for key in affected],
                'requirements':[r.model_dump() for r in contract.requirements],'assumptions':[]})
        response = await call_structured(request_model(self.llm,'auxiliary'), AmendmentSpec,as_messages(
            "把用户补充要求转成结构化要求，已有要求复用RQ编号形成新版本，新要求才用新编号，source_quote保留原题或补充要求的实际摘录，task_ids只取scope中的任务。"
            "只关联change实际影响的问题，不把缺省scope当作修改所有问题；继续/恢复不是新要求。"
            "合并相同任务的要求，简短输出。方法选择或实现纠错作为goal，不虚构0/1自报检查。"
            "硬约束保留原文中的数值、单位和范围，必要校验在计算代码中执行，不创建登记指标或验收检查。"
            "保留原始要求及旧执行契约快照；新条件只属于新问题范围，不能声称旧实验已经验证。", json.dumps(payload, ensure_ascii=False)),validate=validate)
        contract = response if isinstance(response, AmendmentSpec) else AmendmentSpec.model_validate(response)
        if not contract.requirements or scope is not None and {key for r in contract.requirements for key in r.task_ids} != set(scope):
            raise ValueError("补充要求未覆盖修改范围")
        return contract.requirements


class AmendmentSpec(Contract):
    requirements: list[Requirement]


def propose_revision(snapshot, command_id, base_revision, change, scope, requirements):
    selected = sorted({key for r in requirements for key in r.task_ids}) if scope is None else scope
    selected = [key for key in selected if snapshot["experiments"][key]["kind"] != "verify"]
    if not change.strip() or not selected or len(set(selected)) != len(selected):
        raise ValueError("修改内容和范围必须有效")
    return ResearchRevision(command_id=command_id,base_revision=base_revision,change=change,
                            experiment_ids=selected, requirements=requirements)
