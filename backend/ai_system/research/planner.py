"""One sustained research Agent with scoped records and direct execution."""
import asyncio
import json
from .models import Contract, Requirement, ResearchRevision
from .domain import findings, experiment_scope
from .store import digest
from ai_system.graph.commentary import PROGRESS_PROMPT, ProgressMiddleware

PROMPT = """你是持续工作的研究Agent，自己读取材料、运行代码、观察结果、修订方法并回答用户。
工作区文件用research_list_files定位、research_search_files搜索、research_read_artifact分页读取。正文和日志在工作区工具中可读，Python仅负责计算，不能通过搜索沙箱根目录判断正文是否存在。
先直接用 research_read_artifact 读取原始材料，不需要实验编号。用 research_create_question 列出原要求中的全部实质问题（可同轮分别调用），修改才用research_edit_question。用research_run_python在同一持久Python内核计算；公共数据分析可以省略question_id，不把代码挂到不相关问题下。
research_run_python 返回真实输出、错误和文件路径。复用变量与已保存文件，必要时在代码中核对单位、数据范围、约束和预测时可用信息。实际数值直接写入 research_update_answer 的答案，不登记指标或R/F引用。
用普通工具完成读取、计算和解释。只有需要明确比较不同方法/条件时才 research_schedule_experiment；普通方案记录后仍由你直接运行代码，CSV监督学习比较才进入独立分支。
普通计算仅保存运行回执，不做实验提交或验收。需要继续就读取已有代码和文件，不为读取、同步或排版重算。
程序故障不证明方法无效，语义核查只供核实。无变化的重复读取、反复重算和没有新信息的核查应停止。
文件同步由系统重试；执行结果未知时不得自动重放代码。恢复后先读取已有日志和结果，不假定旧Python内存还存在。
所有模型请求共用项目运行预算。预算不足时保留已完成成果和未完成部分，不通过换编号或重新开始绕过限制。
用实际工具返回的ID；参数错误在当前上下文纠正。每个原始问题保留已答、部分或未答、适用范围和停止原因。
研究、正文均由你负责：用writing_edit直接保存Markdown或原生TeX，长稿按文件分步编辑；已有文件先读取，系统自动绑定读取回执的版本保护人工修改，不要复制哈希。不要用Python生成论文。引用图表前可用writing_use_asset绑定实际文件及版本。原生TeX定稿前用writing_preview查看真正的PDF与警告，分批查看关键图表页；按需research_delegate purpose=document_review委派只读复核固定source_version和pdf_sha256，复核与主会话共用预算，正文仍由你改。模型无图像能力时明确仅检查PDF文本。编译成功不代表图表或结论正确。正文与答案完成后给出最终回复或research_deliver，进入相同交付流程；明确局限后可以交付部分结果。
需要独立调查或复核时research_delegate：isolated提供明确任务与材料；fork继承已有上下文接手工作。不要将连续纠错拆给多个Agent，子任务结果仍由你整合。
确需用户提供必要信息才 research_ask_user；无可行路径时 research_pause。交付、提问或启动独立比较后等待真实结果。
"""


def ready_tasks(snapshot):
    experiments = snapshot['experiments']
    return [key for key, item in experiments.items() if not item['retired']
        and item['execution_status'] == 'pending' and item['validity']=='current'
        and all(experiments[dep]['acceptance']=='accepted' and experiments[dep]['validity']=='current'
                for dep in item['dependencies'])
        and all(snapshot['assumptions'][aid]['status']!='refuted' for aid in item['assumption_ids'])]


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
    if selected is None:selected=next((q for q in rows if q.get('answer_gaps')),None)
    own=set(selected['experiment_ids']) if selected else {eid for q in rows for eid in q['experiment_ids']}
    scope=experiment_scope(snapshot['experiments'],own)
    records=experiment_context(snapshot,scope)
    sources=LiteratureStore(store.root).retrieve(selected['question'] if selected else snapshot['spec']['requirement'],limit=12)
    # Current question summaries stay global for goal coverage. Full records and
    # actual values are restricted to the selected question/dependency scope.
    context={'source':snapshot['spec'],'questions':question_context(rows),'current_question_id':selected['id'] if selected else None,
        'experiments':[{**row,'record':report_context(row['record'])} for row in records],
        'literature':sources,'issues':[i for i in findings(snapshot) if i['task_id'] in scope and i['status'] not in {'resolved','dismissed','superseded'}],
        'inputs':files,'materials':snapshot['materials']['profile'] if snapshot['materials']['inputs']==files else '',
        'last_action':{k:last.get(k) for k in ('id','capability','status','question_id','target','reason','message','error_type')},'clarifications':snapshot.get('clarifications',[]),
        'skill_policy':store.skill_preferences()['policy']}
    context['available_method_keys'] = sorted(allowed_method_keys(context['skill_policy']))
    from ai_system.modules.experiments.store import ExperimentStore
    context['comparisons']=[row for row in ExperimentStore(store).accepted_summaries(snapshot) if row.get('task_id') in scope]
    context['recent_operations']=[{k:a.get(k) for k in ('capability','question_id','experiment_id','status','message','error_type')} for a in snapshot.get('activity',[]) if a.get('capability')!='research.observe' and (a.get('question_id') in {None,selected['id'] if selected else None} or a.get('experiment_id') in scope)][-12:]
    if last.get('capability','').startswith('writing.'):
        context['document_diagnostics']=(last.get('result') or {}).get('issues',[])
    from .runs import run_context
    context['runs'] = run_context(store)[-12:]
    context['literature_mode'] = LiteratureStore(store.root).policy()
    main=store.root/('paper.tex' if snapshot['spec'].get('output_mode')=='latex' else 'paper.md')
    if main.is_file():
        import hashlib
        context['document']={'path':main.name,'version':hashlib.sha256(main.read_bytes()).hexdigest()}
    if any((e.get('experiment') or {}).get('csv') for e in snapshot['experiments'].values()):
        from ai_system.modules.experiments.store import SupervisedSpec
        context['csv_comparison_protocol'] = SupervisedSpec.model_json_schema()
    return 'action:' + digest(context), context

def preserved_progress(snapshot):
    runs={a['run_id'] for a in snapshot.get('activity', []) if a.get('run_id')}
    if runs:return f'已保留 {len(runs)} 次计算的代码、日志和实际结果文件。'
    count=sum(e['acceptance']=='accepted' and e['validity']=='current' for e in snapshot['experiments'].values())
    return f'已保留 {count} 项实验成果和全部执行记录。' if snapshot['executions'] else '原题和当前研究记录已保存，尚未开始实验。'


def planning_failure_message(error, snapshot):
    from .runtime import ModelServiceError,ResearchBudgetExhausted,RepeatedOperationBlocked
    from ai_system.config.generation import ModelGenerationLimit
    from langgraph.errors import GraphRecursionError
    from starlette.exceptions import HTTPException
    if isinstance(error,GraphRecursionError):
        detail='研究工具循环达到图步骤上限，尚未完成下一行动；这不是费用额度耗尽。'
        last=next((a.get('message') for a in reversed(snapshot.get('activity',[]))
                   if a.get('status')=='failed' and a.get('capability')!='research.observe' and a.get('message')),None)
        if last:detail+=' 最近具体反馈：'+last
    elif isinstance(error,HTTPException) and 400<=error.status_code<500:
        from .review_cache import error_detail
        detail=error_detail(error)
    else:
        detail=str(error) if isinstance(error,(ModelServiceError,ResearchBudgetExhausted,RepeatedOperationBlocked,NoResearchProgress,ModelGenerationLimit)) else '研究控制器未完成下一行动，具体诊断已保存，可继续处理。'
    return detail+' '+preserved_progress(snapshot)


def decision_block_message(store, snapshot):
    action=snapshot.get('action') or {}
    return action.get('message') or action.get('reason') or '研究操作未完成，已有产物已保存。'


class NoResearchProgress(ValueError):pass


def working_context(ctx):
    """Durable state at a conversation boundary; tool receipts carry later changes."""
    state=ctx.research.snapshot()
    from .views import questions,question_context
    from .runs import run_context
    from .files import known_files
    rows=question_context([q for q in questions(state) if not q.get('retired')])
    for q in rows:
        if len(q.get('judgment',''))>800:q['judgment']=q['judgment'][:800]+'…（research_read_question读取完整答案）'
    runs=run_context(ctx.research)[-6:]
    for r in runs:
        if r.get('error'):r['error']=r['error'][:500]
        r['files']=r['files'][:8]
        if r.get('stdout_path') and ctx.fs.exists(r['stdout_path']):
            r['output_tail']=ctx.fs.read_text(r['stdout_path'])[-600:]
    main=ctx.workspace/('paper.tex' if ctx.output_mode=='latex' else 'paper.md')
    import hashlib
    return {'source':{'requirement':state['spec']['requirement'],'amendments':state['spec']['amendments'],
                      'output_mode':state['spec'].get('output_mode')},
            'questions':rows,'runs':runs,'files':known_files(ctx)[:30],
            'file_tools':'research_list_files / research_search_files / research_read_artifact；Python沙箱不保证有正文副本',
            'document':{'path':main.name,'version':hashlib.sha256(main.read_bytes()).hexdigest(),'nonempty':bool(main.stat().st_size)} if main.is_file() else {},
            'document_diagnostics':(state.get('action') or {}).get('result',{}).get('issues',[])}


class Planner:
    def __init__(self,llm):self.llm=llm

    async def run(self,ctx,context,observation_id):
        from langchain.agents import create_agent
        from langchain.agents.middleware import before_model,after_model,wrap_tool_call
        from langchain_core.messages import HumanMessage,ToolMessage
        from langchain_core.tools import StructuredTool
        from ai_system.graph.compaction import ContextMiddleware,state_message
        from ai_system.config.generation import request_model
        from ai_system.modules.registry import capabilities,invoke
        from .runtime import execution_scope
        from .views import activity
        from .review_cache import error_detail
        registry=capabilities();tools=[];mapping={};read_tools=set()
        for capability in sorted(registry.model_capabilities(),key=lambda c:c.name):
            name=capability.name.replace('.','_');mapping[name]=capability.name
            if capability.access=='read':read_tools.add(name)
            async def call(_name=capability.name,**kwargs):return await invoke(ctx,_name,**kwargs)
            tools.append(StructuredTool(name=name,description=capability.purpose,args_schema=capability.inputs,coroutine=call))
        lock=asyncio.Lock()
        read_slots=asyncio.Semaphore(4)
        known_revision=ctx.research.snapshot()["revision"]
        def input_version():
            main=ctx.workspace/('paper.tex' if ctx.output_mode=='latex' else 'paper.md')
            import hashlib
            return {'inputs':ctx.research.file_inputs(),
                    'document':hashlib.sha256(main.read_bytes()).hexdigest() if main.is_file() else None}
        known_inputs=input_version()
        last_tool_feedback=''
        @wrap_tool_call
        async def feedback(request,handler):
            nonlocal known_revision,known_inputs,last_tool_feedback
            call=request.tool_call;name=call['name'];key='research-tool:'+ctx.research.research_id+':'+call['id']
            async with (ctx.delegation_slots if name=='research_delegate' else read_slots if name in read_tools else lock):
                from .runtime import ResearchRuntime
                meter=ResearchRuntime(ctx.research.root,ctx.research.research_id)
                snapshot=ctx.research.snapshot()
                args=call['args']
                if name=='writing_edit':
                    from ai_system.modules.writing.draft import observed_version
                    args={**args,'expected_version':observed_version(ctx.agent_messages,args.get('path','paper.md'))}
                operation='tool:'+digest([name,args,ctx.research.file_inputs(),snapshot['spec'],snapshot.get('document')])
                meter.check_guard(operation)
                with execution_scope(action_id=key,observation_id=observation_id,tool_call_id=key,expected_revision=known_revision):
                    try:
                        if name not in mapping:raise ValueError('未知工具 '+name+'；请使用已提供的工具名称')
                        # ModuleRegistry validates raw args inside its durable activity
                        # boundary, including extra fields and failed references.
                        from .session import invoke_once
                        result=await invoke_once(ctx,mapping[name],args,key)
                        if isinstance(result,dict) and result.get('execution_status') in {'failed','cancelled','interrupted'}:
                            meter.failed(operation,digest(result.get('observation','')),'execution','相同代码和输入重复失败；已有日志保留，请修改实现后继续。')
                        else:meter.succeeded(operation)
                        if isinstance(result,dict) and 'revision' in result:known_revision=result['revision']
                        if name=='writing_edit' and result.get('path')==('paper.tex' if ctx.output_mode=='latex' else 'paper.md'):
                            known_inputs={**known_inputs,'document':result['version']}
                        last_tool_feedback='；'.join(result.get('answer_gaps',[])) if isinstance(result,dict) else ''
                        from ai_system.modules.writing.document import tool_content
                        failed=isinstance(result,dict) and result.get('execution_status') in {'failed','cancelled','interrupted'}
                        return ToolMessage(content=tool_content(ctx,result,ctx.coder_llm),tool_call_id=call['id'],name=name,status='error' if failed else 'success')
                    except (ValueError,KeyError,FileNotFoundError) as exc:
                        detail=error_detail(exc);last_tool_feedback=detail
                        meter.failed(operation,digest(detail),'tool_input',detail)
                        # Registered calls already have a failed activity. Unknown
                        # tools have no registry boundary, so retain that call here.
                        if name not in mapping:
                            try:
                                with activity(ctx,'research.tool_error',action_id=key,observation_id=observation_id) as item:
                                    item['inputs']=call
                                    raise ValueError(detail)
                            except ValueError:pass
                        return ToolMessage(content=detail,tool_call_id=call['id'],name=name,status='error')
        pending_diagnostic=''
        resume_note=False
        initial_snapshot=True
        @before_model(can_jump_to=['end'])
        async def dispatched(state,runtime):
            nonlocal known_revision,known_inputs,pending_diagnostic,resume_note,initial_snapshot
            from ai_system.modules.experiments.execution import recover_outputs
            await recover_outputs(ctx)
            observations=[]
            resumed_now=resume_note
            if resume_note:
                observations.append(HumanMessage(content='继续原会话；工具会优先重连原Python上下文，并报告context_restored和runtime_epoch；只有确认重连成功才依赖原变量。旧运行以回执为准，不重放已完成计算。'))
                resume_note=False
            if pending_diagnostic:
                observations.append(HumanMessage(content=pending_diagnostic));pending_diagnostic=''
            snapshot=ctx.research.snapshot();action=snapshot.get('action') or {}
            if action.get('status')=='planned':return {'messages':observations,'jump_to':'end'}
            inputs_now=input_version()
            if initial_snapshot or resumed_now or snapshot['revision']!=known_revision or inputs_now!=known_inputs:
                observations.append(state_message(working_context(ctx)))
                initial_snapshot=False
                known_revision=snapshot['revision'];known_inputs=inputs_now
            if observations:
                return {'messages':observations}
        prompted_for_action=False
        @after_model(can_jump_to=['model'])
        async def malformed(state,runtime):
            nonlocal prompted_for_action,pending_diagnostic
            reply=state['messages'][-1]
            invalid=getattr(reply,'invalid_tool_calls',[])
            if invalid:
                with activity(ctx,'research.tool_error',observation_id=observation_id) as item:
                    item.update(status='failed',inputs={'calls':invalid},message='工具参数不是有效JSON，请按工具schema纠正')
                pending_diagnostic='工具参数解析失败：'+json.dumps(invalid,ensure_ascii=False)+'。请纠正本次调用，不要重做已有计算。'
                # Preserve raw failures in activity, but send only valid calls
                # on the wire. Their observations must precede the next model
                # request; jumping now would leave those calls unanswered.
                clean=reply.model_copy(update={'invalid_tool_calls':[],
                    'additional_kwargs':{k:v for k,v in reply.additional_kwargs.items() if k!='tool_calls'}})
                patch={'messages':[clean]}
                if not reply.tool_calls:patch['jump_to']='model'
                return patch
            if not getattr(reply,'tool_calls',[]):
                from ai_system.graph.llm_utils import message_text
                try:
                    if not message_text(reply).strip():raise ValueError('没有最终回复或工具操作，请继续未完成工作或明确暂停原因。')
                    await invoke(ctx,'research.deliver',title=ctx.research.snapshot()['spec']['requirement'].splitlines()[0][:80],
                                 reason=message_text(reply)[:2000])
                except ValueError as exc:
                    if prompted_for_action:raise NoResearchProgress(str(exc)) from exc
                    prompted_for_action=True
                    return {'messages':[HumanMessage(content='最终回复尚不能交付：'+str(exc))],'jump_to':'model'}
        recorder=ContextMiddleware(ctx,self.llm,ctx.research.research_id+'-lead')
        common={'original_request':context['source']['requirement'],'available_method_keys':context['available_method_keys'],
                'skill_policy':context['skill_policy'],'inputs':context['inputs'],'materials':context['materials'],
                'template_requirements':ctx.template_requirements,'compact_paper':ctx.compact_paper}
        messages=[HumanMessage(content=json.dumps(common,ensure_ascii=False),id='initial-request:'+ctx.research.research_id)]
        result=None
        try:
            from .session import session_saver,run_session
            def resumed(pending):
                nonlocal resume_note
                resume_note=True
            async with session_saver(ctx) as saver:
                agent=create_agent(model=request_model(self.llm),tools=tools,system_prompt=PROMPT + PROGRESS_PROMPT,
                    checkpointer=saver,middleware=[recorder,ProgressMiddleware(ctx.events,scope=observation_id),feedback,dispatched,malformed])
                result=await run_session(agent,ctx.research.research_id+'-lead',messages,on_resume=resumed)
            return result
        finally:
            await ctx.kernels.disconnect()
            history=result['messages'] if result else recorder.messages
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
