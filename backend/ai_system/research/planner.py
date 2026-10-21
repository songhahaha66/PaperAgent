"""Choose one research action; IDs, execution context and ownership belong to the store."""
import json
from typing import Literal
from pydantic import Field, model_validator
from .models import Contract, ExperimentPlan, Requirement, ResearchRevision
from .domain import findings, experiment_report, experiment_scope, current_result_refs
from .store import digest


class QuestionUpdate(Contract):
    question_id: str | None = None
    base_version: int = Field(default=1, ge=1)
    question: str = Field(min_length=1, description='用户要回答的问题，不是清洗、检索等手段')
    title: str = ''
    reason: str = ''
    source_question_ids: list[str] = Field(default_factory=list, description='拆分或合并的来源问题；历史和成果保留')
    experiment_ids: list[str] = Field(default_factory=list, description='关联可复用的已有实验，不修改其来源或计算')
    retire_source_ids: list[str] = Field(default_factory=list)


class Judgment(Contract):
    question_id: str
    statement: str = ''
    artifact_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_step: str = ''
    coverage: Literal['answered','partial','unanswered'] | None = None
    stop_reason: str = ''


class SectionBasis(Contract):
    ref: str = Field(min_length=1, description='已有产物或文献的实际引用编号')
    quote: str = ''


class EvidenceSection(Contract):
    key: str
    title: str
    purpose: str
    task_ids: list[str] = Field(default_factory=list)
    basis: list[SectionBasis] = Field(default_factory=list)


EXPERIMENT_FIELDS = ('method', 'conditions', 'evaluation')


class NextAction(Contract):
    action: Literal['experiment', 'continue', 'observe', 'inspect', 'literature', 'tool', 'ask', 'write', 'pause']
    reason: str = Field(min_length=1)
    information_need: str = Field(default='', description='继续实验、查看或核查须说明缺少什么信息及会影响什么判断')
    question_updates: list[QuestionUpdate] = Field(default_factory=list)
    question_id: str | None = None
    question_index: int | None = Field(default=None, ge=0, description='本次question_updates中行动所属问题的零基索引；仅有一个更新时可省略。已有问题使用question_id，系统分配实际编号')
    title: str = Field(default='',max_length=120)
    experiment_id: str | None = Field(default=None, description='继续/重试原实验，或标明读取和检索行动的所属实验；更换方法须新增实验')
    method: str = Field(default='', max_length=3000, description='本次实际执行的方法、参数及关键假设，执行者不得静默换方法')
    conditions: str = Field(default='', max_length=2000, description='实际数据范围、输入版本与适用条件')
    evaluation: str = Field(default='', max_length=2000, description='怎样检验方法，比较时须使用共同条件；无赢家允许保留多个候选')
    experiment: ExperimentPlan | None = Field(default=None, description='仅CSV比较等需要完整执行协议时提供；普通代码实验提供method/conditions/evaluation即可')
    artifact_refs: list[str] = Field(default_factory=list, description='实际依赖的已有产物；系统构造执行依赖，不把审查意见当成实测结果')
    issue_ids: list[str] = Field(default_factory=list, description='本次调查或修复的意见，与产物引用分开')
    judgments: list[Judgment] = Field(default_factory=list, description='根据已有产物更新问题判断，允许无结论或多个候选')
    query: str = ''
    message: str = Field(default='', description='ask时说明必须由用户补充的信息或作出的决定')
    capability: str = ''
    inputs: dict = Field(default_factory=dict)
    outline: list[EvidenceSection] = Field(default_factory=list)

    @model_validator(mode='after')
    def experiment_fields(self):
        if self.action != 'experiment' or self.experiment_id:
            return self
        if self.experiment is None:
            missing = [name for name in EXPERIMENT_FIELDS if not getattr(self,name).strip()]
            if missing:raise ValueError('新实验缺少方法、条件或评价：'+', '.join(missing))
        resolve_question(self, [q.question_id or f'new:{i}' for i,q in enumerate(self.question_updates)])
        return self


def resolve_question(action, updated_ids):
    if action.question_id is not None and action.question_index is not None:
        raise ValueError('question_id与question_index选择一种明确的关联')
    if action.question_id is not None:return action.question_id
    if action.question_index is not None:
        if action.question_index>=len(updated_ids):raise ValueError('question_index超出本次问题更新范围')
        return updated_ids[action.question_index]
    if len(updated_ids)==1:return updated_ids[0]
    if action.action=='experiment' and not action.experiment_id:
        raise ValueError('新实验须关联已有question_id或本次question_updates；多个问题用question_index明确选择')
    return None


PROMPT = """研究层根据完整原要求、问题当前答案、实验发现及必要历史，决定一次NextAction。
主线：研究→用户要回答的问题→获取/验证证据的实验→连续的读取、检索、计算、绘图、核查和解释修订行动。
问题不是方法或清洗任务。首轮识别原要求的实质目标，用question_updates逐步建立问题，可细化、改写、拆合；已有实验通过experiment_ids复用，保留来源和历史。不要只建立一个前处理问题而遗漏用户目标。
问题只通过question_updates建立或修改。新增实验可在同一决策中关联本次唯一更新；多个更新时用question_index明确选择，已有问题使用question_id。实验填写method/conditions/evaluation（或完整experiment），不再重复顶层question。
每次继续获取信息都写明information_need：缺什么证据、为何影响问题答案。换方法或比较条件才新增experiment。
已完成实验需新增计算但方法和条件不变时continue并引用experiment_id；故障可experiment重试原experiment_id。读取已有文件用research.read_artifact，不重算已有输出。已有实验后的inspect/literature行动须用experiment_id关联所属实验。
文字或过度解释错误调用research.revise_interpretation，传interpretation_version、完整summary/key_findings及所需意见处置；不新增实验、不重算正确统计。
可按需research.check_interpretation返回具体疑点。核查意见是建议而非事实，须核对原资料、对象、粒度、单位、分母、范围后采纳、带证据驳回或保留局限。程序失败不证明方法无效。
judgments更新当前答案、依据、未知及下一步原因；结果或解释改变后重新判断适用范围、方法选择和问题答案。question_updates可与experiment一次提交，系统分配问题编号并原子关联。observe只用于实际更新问题或判断，不得空转。
多个问题可引用同一实验结果，artifact_refs必须真实有效；不要复制来源或按同值猜引用。新问题可通过关联已有experiment_ids复用证据。
write时逐一在judgments明确每个用户问题的coverage（answered/partial/unanswered）与stop_reason。允许部分或未答后交付，说明停止原因，不强求正结果，也不能把一个答案当全题完成。跨问题写作是研究级交付，不创建写作实验。
写作自主组织原要求、当前答案、相关结果和必要历史/负结果；未支持或撤回观点可讨论但不能当当前事实。缺实质证据时回研究层，排版、措辞、编译留写作处理。
只返回平铺NextAction；真实缺用户必要信息才ask，无可行路径才pause。工具只使用提供的能力和真实参数。
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


def research_history_context(snapshot, *, full=False):
    from .domain import experiment_report
    return [{'experiment_id':key, 'execution_status':e['execution_status'], 'acceptance':e['acceptance'],
             'validity':e['validity'], 'report':report_context(experiment_report(snapshot,e),full=full), 'report_ref':e['report_ref'],
             'prior_interpretations':[{'report':report_context(a['report'],full=full), 'report_ref':a['payload']['id'], 'status':'failed' if a['report'].get('status')=='failed' else 'superseded'}
                  for a in snapshot['artifacts'].values() if a['type']=='report' and a['experiment_id']==key
                  and a['payload']['id']!=e['report_ref']]}
            for key,e in snapshot['experiments'].items()
            if e['acceptance']!='accepted' or e['validity']!='current' or any(
                 a['type']=='report' and a['experiment_id']==key and a['payload']['id']!=e['report_ref']
                 for a in snapshot['artifacts'].values())]


def decision_context(store, snapshot):
    from .literature import LiteratureStore
    from ai_system.modules.registry import capabilities
    from ai_system.modules.methods import allowed_method_keys
    from .views import questions,question_context,experiment_context
    files=store.file_inputs()
    rows=[q for q in questions(snapshot) if not q.get('retired')]
    last=snapshot.get('action') or {}
    focused=last.get('question_id')
    selected=next((q for q in rows if q['id']==focused),None)
    if selected is None:selected=next((q for q in rows if q.get('answer_status')=='needs_update' or not q.get('coverage')),None)
    own=set(selected['experiment_ids']) if selected else {eid for q in rows for eid in q['experiment_ids']}
    scope=experiment_scope(snapshot['experiments'],own)
    records=experiment_context(snapshot,scope)
    registry=store.registry_payload(snapshot)
    sources=LiteratureStore(store.root).retrieve(selected['question'] if selected else snapshot['spec']['requirement'],limit=12)
    from ai_system.graph.registry import find_refs
    used={ref for row in records for ref in find_refs(json.dumps(report_context(row['record']),ensure_ascii=False))}
    used.update(ref for q in rows for ref in q.get('artifact_refs',[]))
    # Current question summaries stay global for goal coverage. Full records and
    # actual values are restricted to the selected question/dependency scope.
    context={'source':snapshot['spec'],'questions':question_context(rows),'current_question_id':selected['id'] if selected else None,
        'experiments':[{**row,'record':report_context(row['record']),'results':[r for r in row['results'] if r['id'] in used]} for row in records],
        'artifacts':[r for r in registry['results'] if r['subproblem'] in scope and r['id'] in used],
        'artifact_index':[{k:r[k] for k in ('id','subproblem','name','kind')} for r in registry['results']],
        'literature':sources,'issues':[i for i in findings(snapshot) if i['task_id'] in scope and i['status'] not in {'resolved','dismissed','superseded'}],
        'inputs':files,'materials':snapshot['materials']['profile'] if snapshot['materials']['inputs']==files else '',
        'last_action':last,'clarifications':snapshot.get('clarifications',[]),
        'capabilities':capabilities().research_tools(),'skill_policy':store.skill_preferences()['policy']}
    context['available_method_keys'] = sorted(allowed_method_keys(context['skill_policy']))
    from ai_system.modules.experiments.store import ExperimentStore
    context['comparisons']=[row for row in ExperimentStore(store).accepted_summaries(snapshot) if row.get('task_id') in scope]
    context['literature_mode'] = LiteratureStore(store.root).policy()
    if any(name.lower().endswith('.csv') for name in files):
        from ai_system.modules.experiments.store import SupervisedSpec
        context['csv_comparison_protocol'] = SupervisedSpec.model_json_schema()
    return 'action:' + digest(context), context


def validate_action(action, snapshot, context):
    """Check actual references and execution meaning once, without quote-copy protocols."""
    if action.question_id is not None and action.question_id not in snapshot['questions']:
        raise ValueError('question_id应引用已有研究问题；新增问题请提供question_updates')
    if action.experiment_id is not None and action.experiment_id not in snapshot['experiments']:
        raise ValueError('experiment_id必须引用已有实验')
    if action.action in {'inspect','literature'} and snapshot['experiments'] and not action.experiment_id:
        raise ValueError('读取或检索行动须通过experiment_id关联实验；新方法先建立实验')
    known = {item['id'] for item in [*context['artifacts'],*context.get('artifact_index',[])]}
    if not set(action.artifact_refs) <= known:
        raise ValueError('artifact_refs中有不存在或已失效的产物，请引用当前artifacts')
    issues = {i['id'] for i in context['issues']}
    if not set(action.issue_ids) <= issues:
        raise ValueError('issue_ids中有不存在或已关闭的意见')
    from .questions import apply_questions
    from copy import deepcopy
    preview=deepcopy(snapshot)
    updated=apply_questions(preview,action.question_updates)
    target=resolve_question(action,updated)
    projected=preview['questions']
    from .views import questions
    outdated={q['id'] for q in questions(snapshot) if q.get('answer_status')=='needs_update'}
    if target is not None and target not in projected:raise ValueError('行动关联了未知问题')
    for judgment in action.judgments:
        if judgment.question_id not in projected or not set(judgment.artifact_refs) <= known:
            raise ValueError('问题答案必须关联已有问题与实际产物；新问题建立后再判断')
        question = projected[judgment.question_id]
        own = {key for key, e in snapshot['experiments'].items()
               if e['question_id'] == judgment.question_id and not e['retired']} | set(question.get('related_experiment_ids', []))
        if not set(judgment.artifact_refs) <= current_result_refs(snapshot, experiment_scope(snapshot['experiments'], own)):
            raise ValueError('答案只能引用关联实验与其依赖的当前有效结果')
    if action.action=='observe' and preview['questions']==snapshot['questions'] and not any(
        j.question_id in outdated or any(projected[j.question_id].get(k)!=v for k,v in {'judgment':j.statement,'artifact_refs':j.artifact_refs,
            'limitations':j.limitations,'next_step':j.next_step,'coverage':j.coverage,'stop_reason':j.stop_reason}.items())
        for j in action.judgments):
        raise ValueError('observe没有实际问题或答案变化；请选择有信息需求的行动，或明确交付/暂停')
    if action.action=='continue':
        task=snapshot['experiments'].get(action.experiment_id)
        if not task or task['execution_status'] != 'succeeded' or task['validity'] != 'current':
            raise ValueError('continue只补充同方法同条件的已完成实验；故障使用experiment重试')
        if action.method or action.experiment or action.conditions:
            raise ValueError('更改方法或条件须新增实验，不能作为继续行动')
    if action.action=='experiment':
        if action.experiment_id:
            experiment=snapshot['experiments'].get(action.experiment_id)
            if not experiment or experiment['execution_status'] not in {'pending','failed','cancelled'}:
                raise ValueError('只能执行排队实验或重试执行故障；新方法请新增实验')
            if action.method or action.experiment:
                raise ValueError('已有实验的方法已冻结；换方法请在同一问题下新增实验')
    elif action.action=='tool':
        from ai_system.modules.registry import capabilities
        capabilities().validate_research_tool(action.capability, action.inputs)
    elif action.action=='ask' and not action.message.strip():
        raise ValueError('请说明需要用户补充什么信息或作出什么决定')
    elif action.action=='literature':
        if context['literature_mode']=='off' or not action.query.strip():
            raise ValueError('文献检索需要有效查询且项目允许外部检索')
    elif action.action=='write':
        from .store import ResearchStore
        for judgment in action.judgments:
            q=projected[judgment.question_id]
            q.update(judgment=judgment.statement,artifact_refs=judgment.artifact_refs,
                     answer_status='current',coverage=judgment.coverage,stop_reason=judgment.stop_reason,basis_versions={ref:snapshot['artifacts'][ref]['content_hash'] for ref in judgment.artifact_refs},
                     interpretation_versions={snapshot['artifacts'][ref]['experiment_id']:snapshot['experiments'][snapshot['artifacts'][ref]['experiment_id']]['report_ref'] for ref in judgment.artifact_refs})
        if not ResearchStore.delivery_ready({**snapshot,'questions':projected}):
            raise ValueError('write前逐一明确每个用户问题的coverage与stop_reason；已答项须有有效依据，允许说明部分/未答及停止原因')


def preserved_progress(snapshot):
    count=sum(e['acceptance']=='accepted' and e['validity']=='current' for e in snapshot['experiments'].values())
    return f'已保留 {count} 项实验成果和全部执行记录。' if snapshot['executions'] else '原题和当前研究记录已保存，尚未开始实验。'


def planning_failure_message(error, snapshot):
    from .runtime import decision_error_summary
    return '下一步操作未能准备完成：'+decision_error_summary(error)+'。'+preserved_progress(snapshot)


def decision_block_message(store, snapshot):
    action=snapshot.get('action') or {}
    if action.get('status')=='failed' and action.get('capability','').startswith('writing.'):
        from .runtime import action_failure_message
        step=action['capability'].split('.')[1]
        stage=snapshot.get('delivery_stages',{}).get(step,{})
        return action.get('message') or stage.get('note') or action_failure_message(action)
    return action.get('message') or action.get('error') or action.get('reason') or '研究操作未完成，已有产物已保存。'


class Planner:
    def __init__(self, llm):
        self.llm=llm

    async def decide(self, snapshot, context):
        from ..graph.llm_utils import call_structured, as_messages, cap_output
        model_name=str(getattr(self.llm,'model_name',None) or getattr(self.llm,'model',''))
        from langchain_core.messages import SystemMessage, HumanMessage
        # Keep stable instructions, tool schemas and common materials before the
        # changing research view. No extra summary model or cache service.
        common={'capabilities':context['capabilities'],'available_method_keys':context['available_method_keys'],
                'skill_policy':context['skill_policy'],'original_request':context['source']['requirement'],
                'inputs':context['inputs'],'materials':context['materials']}
        dynamic={k:v for k,v in context.items() if k not in {'capabilities','available_method_keys','skill_policy','inputs','materials'}}
        return await call_structured(cap_output(self.llm, 4800), NextAction,
            [SystemMessage(content=PROMPT),HumanMessage(content=json.dumps(common,ensure_ascii=False)),
             HumanMessage(content=json.dumps(dynamic,ensure_ascii=False))],
            validate=lambda action: validate_action(action, snapshot, context),
            retries=1, json_first='qwen' in model_name.lower())

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
