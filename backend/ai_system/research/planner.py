"""Research tool Agent and scoped evidence context; no structured master decision."""
import asyncio
import json
from .models import Contract, Requirement, ResearchRevision
from .domain import findings, experiment_scope
from .store import digest
from ai_system.graph.commentary import PROGRESS_PROMPT, ProgressMiddleware

PROMPT = """你是研究层Agent，沿研究→用户问题→实验→行动组织探索。
使用普通工具工作，不输出总计划JSON。先用research_update_question建立用户要回答的问题，取得真实question_id，再安排实验。问题不是清洗或检索手段；覆盖原始请求的不同实质目标，可逐步细化、拆合并复用已有实验。
同一实验执行层会连续完成必要行动；新增方法或条件比较才安排新实验，固定方法补充计算或修复执行故障用research_continue_experiment。已有结果直接读取和复用，保留其来源、条件和版本。修过度解释用research_revise_interpretation，不重算正确统计。
每次新增实验明确它将减少哪项影响答案的不确定性；已满足请求时及时交付。预算耗尽或同类故障重复时保留已有成果，更新为部分回答并说明缺口后交付，不能换实验编号绕过限制。文件保存、报告排版失败不需要重算实验。
每个工具独立提交。建立问题后尚未安排实验是正常状态。用工具实际返回的ID，禁止猜编号、沿用失败请求的假编号或混用其它工具字段。参数错误会返回具体观察，直接在同一上下文纠正；不能把格式错误当研究阻碍。版本冲突时重新读取当前记录再操作。
同名或同值不代表同一对象或范围。结果的meaning、实验conditions、输入版本与行关系一并核对；未知口径如实说明。新实验需要沿用或比较已有结果时，用artifact_refs建立实际依赖，勿先独立重算再猜旧结果为何不同。
按需调用语义核查，意见只供核实、采纳、驳回或保留局限。程序故障不证明方法无效。旧观点、负结果可以讨论，但不可混为当前事实。
结果与解释更新后，用research_update_answer修订问题答案、实际依据、适用范围、未知与下一步。继续获取信息应有明确需求，不重复无变化的读取或判断。
原要求的重要问题分别记录已答、部分或未答及停止原因后，research_deliver交付。写作是研究级操作，读取当前答案、实验记录与必要历史，不创建写作实验。不要求所有软意见归零或所有问题有正结果。
用户要求研究报告或PDF时，实验负责计算、CSV、图表和执行代码，正式报告由research_deliver后的写作、引用解析和编译流程生成；不要把完整报告或PDF生成安排进计算实验。实验临时报告有引用占位符、排版或字体问题时，把具体问题交给正式写作，不为修报告重算统计，也不以临时PDF未成稿为由反复核对同一实验。
只有确需用户提供必要信息或决定才research_ask_user；无可行继续路径可research_pause。安排实验或交付后，系统会转入已有执行图，等待真实执行结果再判断。
"""

def ready_tasks(snapshot):
    experiments = snapshot['experiments']
    return [key for key, item in experiments.items() if not item['retired']
        and item['execution_status'] == 'pending' and item['validity']=='current'
        and sum(a['task_id']==key and a['revision']==item['revision'] for a in snapshot['executions'].values())<snapshot['max_attempts']
        and len(snapshot['executions'])<snapshot['max_total_attempts']
        and all(experiments[dep]['acceptance']=='accepted' and experiments[dep]['validity']=='current'
                for dep in item['dependencies'])
        and all(snapshot['assumptions'][aid]['status']!='refuted' for aid in item['assumption_ids'])]


def report_context(report, *, full=False):
    if full:return report
    return {k:report[k] for k in ('summary','key_findings','limitations','status','error') if k in report}


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
    registry=store.registry_payload(snapshot)
    sources=LiteratureStore(store.root).retrieve(selected['question'] if selected else snapshot['spec']['requirement'],limit=12)
    # Current question summaries stay global for goal coverage. Full records and
    # actual values are restricted to the selected question/dependency scope.
    context={'source':snapshot['spec'],'questions':question_context(rows),'current_question_id':selected['id'] if selected else None,
        'experiments':[{**row,'record':report_context(row['record'])} for row in records],
        'artifact_index':[{**{k:r.get(k) for k in ('id','subproblem','name','kind','unit','meaning')},
            'conditions':(snapshot['experiments'][r['subproblem']].get('experiment') or {}).get('conditions','')} for r in registry['results']],
        'literature':sources,'issues':[i for i in findings(snapshot) if i['task_id'] in scope and i['status'] not in {'resolved','dismissed','superseded'}],
        'inputs':files,'materials':snapshot['materials']['profile'] if snapshot['materials']['inputs']==files else '',
        'last_action':{k:last.get(k) for k in ('id','capability','status','question_id','target','reason','message','error_type')},'clarifications':snapshot.get('clarifications',[]),
        'skill_policy':store.skill_preferences()['policy']}
    context['execution_budget'] = {'remaining': max(0, snapshot['max_total_attempts'] - len(snapshot['executions'])),
        'experiments': {key: max(0, snapshot['max_attempts'] - sum(a['task_id']==key and a['revision']==e['revision'] for a in snapshot['executions'].values())) for key,e in snapshot['experiments'].items()}}
    context['available_method_keys'] = sorted(allowed_method_keys(context['skill_policy']))
    from ai_system.modules.experiments.store import ExperimentStore
    context['comparisons']=[row for row in ExperimentStore(store).accepted_summaries(snapshot) if row.get('task_id') in scope]
    context['recent_operations']=[{k:a.get(k) for k in ('capability','question_id','experiment_id','status','message','error_type')} for a in snapshot.get('activity',[]) if a.get('capability')!='research.observe' and (a.get('question_id') in {None,selected['id'] if selected else None} or a.get('experiment_id') in scope)][-12:]
    context['literature_mode'] = LiteratureStore(store.root).policy()
    if any((e.get('experiment') or {}).get('csv') for e in snapshot['experiments'].values()):
        from ai_system.modules.experiments.store import SupervisedSpec
        context['csv_comparison_protocol'] = SupervisedSpec.model_json_schema()
    return 'action:' + digest(context), context

def preserved_progress(snapshot):
    count=sum(e['acceptance']=='accepted' and e['validity']=='current' for e in snapshot['experiments'].values())
    return f'已保留 {count} 项实验成果和全部执行记录。' if snapshot['executions'] else '原题和当前研究记录已保存，尚未开始实验。'


def planning_failure_message(error, snapshot):
    from .runtime import ModelServiceError,ResearchBudgetExhausted,RepeatedOperationBlocked
    from langgraph.errors import GraphRecursionError
    if isinstance(error,GraphRecursionError):
        detail='研究工具循环达到图步骤上限，尚未完成下一行动；这不是费用额度耗尽。'
        last=next((a.get('message') for a in reversed(snapshot.get('activity',[]))
                   if a.get('status')=='failed' and a.get('capability')!='research.observe' and a.get('message')),None)
        if last:detail+=' 最近具体反馈：'+last
    else:
        detail=str(error) if isinstance(error,(ModelServiceError,ResearchBudgetExhausted,RepeatedOperationBlocked,NoResearchProgress)) else '研究控制器未完成下一行动，具体诊断已保存，可继续处理。'
    return detail+' '+preserved_progress(snapshot)


def decision_block_message(store, snapshot):
    action=snapshot.get('action') or {}
    return action.get('message') or action.get('reason') or '研究操作未完成，已有产物已保存。'


class NoResearchProgress(ValueError):pass


class Planner:
    def __init__(self,llm):self.llm=llm

    async def run(self,ctx,context,observation_id):
        from langchain.agents import create_agent
        from langchain.agents.middleware import before_model,after_model,wrap_tool_call
        from langchain_core.messages import HumanMessage,ToolMessage
        from langchain_core.tools import StructuredTool
        from ai_system.graph.compaction import HistoryRecorder
        from ai_system.graph.llm_utils import cap_output
        from ai_system.modules.registry import capabilities,invoke
        from .runtime import execution_scope
        from .views import activity
        from .review_cache import error_detail
        registry=capabilities();tools=[];mapping={}
        for capability in registry.model_capabilities():
            name=capability.name.replace('.','_');mapping[name]=capability.name
            async def call(_name=capability.name,**kwargs):return await invoke(ctx,_name,**kwargs)
            tools.append(StructuredTool(name=name,description=capability.purpose,args_schema=capability.inputs,coroutine=call))
        lock=asyncio.Lock()
        known_revision=ctx.research.snapshot()["revision"]
        model_calls=0;last_tool_feedback=''
        @wrap_tool_call
        async def feedback(request,handler):
            nonlocal known_revision,last_tool_feedback
            call=request.tool_call;name=call['name'];key='research-tool:'+observation_id+':'+call['id']
            async with lock:
                with execution_scope(action_id=key,observation_id=observation_id,tool_call_id=key,expected_revision=known_revision):
                    try:
                        if name not in mapping:raise ValueError('未知工具 '+name+'；请使用已提供的工具名称')
                        # ModuleRegistry validates raw args inside its durable activity
                        # boundary, including extra fields and failed references.
                        result=await invoke(ctx,mapping[name],**call['args'])
                        if isinstance(result,dict) and 'revision' in result:known_revision=result['revision']
                        last_tool_feedback='；'.join(result.get('answer_gaps',[])) if isinstance(result,dict) else ''
                        return ToolMessage(content=json.dumps(result,ensure_ascii=False,default=str),tool_call_id=call['id'],name=name)
                    except (ValueError,KeyError,FileNotFoundError) as exc:
                        detail=error_detail(exc);last_tool_feedback=detail
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
        @before_model(can_jump_to=['end'])
        async def dispatched(state,runtime):
            nonlocal known_revision,pending_diagnostic,model_calls
            observations=[]
            if pending_diagnostic:
                observations.append(HumanMessage(content=pending_diagnostic));pending_diagnostic=''
            snapshot=ctx.research.snapshot();action=snapshot.get('action') or {}
            if action.get('status')=='planned':return {'messages':observations,'jump_to':'end'}
            if model_calls>=ctx.max_solver_steps:
                raise NoResearchProgress(f'本轮研究已达到 {model_calls} 次模型决策上限，尚未安排下一行动；已有结果保留。'+(' 最近具体反馈：'+last_tool_feedback if last_tool_feedback else ''))
            model_calls+=1
            if snapshot['revision']!=known_revision:
                known_revision=snapshot['revision']
                _,current=decision_context(ctx.research,snapshot)
                current={**current,'source':{k:v for k,v in current['source'].items() if k!='requirement'}}
                observations.append(HumanMessage(content='当前研究记录已更新：'+json.dumps(current,ensure_ascii=False)))
            if observations:return {'messages':observations}
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
            if not getattr(reply,'tool_calls',[]) and not prompted_for_action:
                prompted_for_action=True
                return {'messages':[HumanMessage(content='尚未调用工具或安排可执行行动。请现在调用与当前信息需求对应的工具；先建立问题须使用返回的真实ID。若确需用户补充必要信息可提问，无可行路径则明确暂停。保留已有成果，不要重复计算。')],'jump_to':'model'}
        recorder=HistoryRecorder()
        agent=create_agent(model=cap_output(self.llm,4800),tools=tools,system_prompt=PROMPT + PROGRESS_PROMPT,
                           middleware=[recorder,ProgressMiddleware(ctx.events, scope=observation_id),feedback,dispatched,malformed])
        common={'original_request':context['source']['requirement'],'available_method_keys':context['available_method_keys'],
                'skill_policy':context['skill_policy'],'inputs':context['inputs'],'materials':context['materials']}
        dynamic={k:v for k,v in context.items() if k not in {'available_method_keys','skill_policy','inputs','materials'}}
        dynamic['source']={k:v for k,v in context['source'].items() if k!='requirement'}
        messages=[HumanMessage(content=json.dumps(common,ensure_ascii=False)),HumanMessage(content=json.dumps(dynamic,ensure_ascii=False))]
        result=None
        try:
            # before/after hooks are graph nodes too. Bound actual model calls above;
            # allocate graph transitions from this agent's real topology.
            nodes=len(set(agent.get_graph().nodes)-{'__start__','__end__'})
            result=await agent.ainvoke({'messages':messages},config={'recursion_limit':ctx.max_solver_steps*nodes+2})
            return result
        finally:
            history=result['messages'] if result else recorder.messages
            if history:
                from ai_system.modules.experiments.execution import save_transcript
                save_transcript(ctx,observation_id.replace(':','-'),history)

    async def amendment(self, snapshot, change, scope):
        from ..graph.llm_utils import call_structured, as_messages, cap_output
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
        response = await call_structured(cap_output(self.llm,4800), AmendmentSpec,as_messages(
            "把用户补充要求转成结构化要求，已有要求复用RQ编号形成新版本，新要求才用新编号，source_quote保留原题或补充要求的实际摘录，task_ids只取scope中的任务。"
            "只关联change实际影响的问题，不把缺省scope当作修改所有问题；继续/恢复不是新要求。"
            "合并相同任务的要求，简短输出。方法选择或实现纠错作为goal，不虚构0/1自报检查。"
            "硬约束必须有可执行检查，数值阈值必须出现在本项逐字摘录内；检查编号全局唯一。"
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
