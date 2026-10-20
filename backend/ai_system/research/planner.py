"""Choose one research action; IDs, execution context and ownership belong to the store."""
import json
from typing import Literal
from pydantic import Field
from .models import Contract, ExperimentPlan, Requirement, ResearchRevision
from .domain import findings, experiment_report
from .store import digest


class Judgment(Contract):
    question_id: str
    statement: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_step: str = ''


class SectionBasis(Contract):
    ref: str = Field(min_length=1, description='已有产物或文献的实际引用编号')
    quote: str = ''


class EvidenceSection(Contract):
    key: str
    title: str
    purpose: str
    task_ids: list[str] = Field(default_factory=list)
    basis: list[SectionBasis] = Field(default_factory=list)


class NextAction(Contract):
    action: Literal['experiment', 'inspect', 'literature', 'tool', 'ask', 'write', 'pause']
    reason: str = Field(min_length=1)
    question_id: str | None = None
    question: str = Field(default='', description='仅新增研究问题时提供；已有问题使用question_id')
    title: str = Field(default='',max_length=120)
    experiment_id: str | None = Field(default=None, description='仅执行已排队实验或重试执行故障时引用；更换方法必须新增实验')
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


PROMPT = """根据原题、研究问题、实验及真实产物决定下一步，只返回一个平铺的NextAction对象。
持续提出方法、获取证据、修正判断。方法明确就直接experiment；不确定或证据不足时逐步安排候选实验。
问题是研究目标，实验是回答问题的一种具体方法。同一问题的多个方法都使用同一question_id，不把方法当成新问题。
新增问题提供question/title，系统分配编号；新增实验提供method、conditions、evaluation，不提供内部branch或任务修改补丁。
普通代码、推导、仿真使用experiment；CSV双模型比较仅在适用时提供experiment协议，不能决定其他研究的主流程。
已有产物通过artifact_refs引用；补充实验关联issue_ids。执行失败不是方法被证伪，可重试故障实验或新增方法，不能重跑已完成实验冒充新证据。
模型估计、审查意见与实测产物必须区分。只有同条件结果才可比较；不强行选优，无赢家时保留多个候选或补实验。
judgments记录基于实际产物的判断与局限。正文常规意见自动修；只有缺必要信息或真实用户取舍才ask。
已有材料未查看且需要读取时inspect。文献不足时literature。工具入口只使用给定能力及其参数，不传复杂内部状态。
当实际成果足以回答原题时write，可提供简洁大纲；系统允许先展示草稿，科学验收和发布由现有写作检查负责。
不要复制完整原要求、分配编号、构造执行上下文或要求用户修模型格式。完整原题始终保留，方法必须落实其要求。
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


def decision_context(store, snapshot):
    from .literature import LiteratureStore
    from ai_system.modules.registry import capabilities
    from ai_system.modules.methods import allowed_method_keys
    registry = store.registry_payload(snapshot)
    sources = LiteratureStore(store.root).retrieve(snapshot['spec']['requirement'], limit=12)
    files = store.file_inputs()
    context = {'source': snapshot['spec'], 'questions': list(snapshot['questions'].values()),
        'experiments': [{**{k:e.get(k) for k in ('task_id','question_id','revision','objective','method_instruction','experiment','dependencies','execution_status','acceptance','validity','retired','investigates')},
                         'report':experiment_report(snapshot,e)} for e in snapshot['experiments'].values()],
        'artifacts': registry['results'], 'claims': registry['claims'], 'literature': sources,
        'issues': [i for i in findings(snapshot) if i['status'] not in {'resolved','dismissed','superseded'}],
        'inputs': files, 'materials': snapshot['materials']['profile'] if snapshot['materials']['inputs']==files else '',
        'recent_actions': snapshot.get('decision_history', [])[-12:], 'clarifications':snapshot.get('clarifications',[]),
        'capabilities': capabilities().research_tools(), 'skill_policy': store.skill_preferences()['policy']}
    context['available_method_keys'] = sorted(allowed_method_keys(context['skill_policy']))
    from ai_system.modules.experiments.store import ExperimentStore
    context['comparisons']=ExperimentStore(store).accepted_summaries(snapshot)
    context['literature_mode'] = LiteratureStore(store.root).policy()
    if any(name.lower().endswith('.csv') for name in files):
        from ai_system.modules.experiments.store import SupervisedSpec
        context['csv_comparison_protocol'] = SupervisedSpec.model_json_schema()
    return 'action:' + digest(context), context


def validate_action(action, snapshot, context):
    """Check actual references and execution meaning once, without quote-copy protocols."""
    if action.question_id is not None and action.question_id not in snapshot['questions']:
        raise ValueError('question_id应引用已有研究问题；新增问题请提供question')
    known = {item['id'] for item in context['artifacts']}
    if not set(action.artifact_refs) <= known:
        raise ValueError('artifact_refs中有不存在或已失效的产物，请引用当前artifacts')
    issues = {i['id'] for i in context['issues']}
    if not set(action.issue_ids) <= issues:
        raise ValueError('issue_ids中有不存在或已关闭的意见')
    for judgment in action.judgments:
        if judgment.question_id not in snapshot['questions'] or not set(judgment.artifact_refs)<=known:
            raise ValueError('问题判断必须关联已有问题和实际产物')
        if any(snapshot['artifacts'][ref]['experiment_id'] not in {
                e['task_id'] for e in snapshot['experiments'].values() if e['question_id']==judgment.question_id}
               for ref in judgment.artifact_refs):
            raise ValueError('问题判断引用了其他问题的产物，请说明实际依赖后安排相应实验')
    if action.action=='experiment':
        if action.experiment_id:
            experiment=snapshot['experiments'].get(action.experiment_id)
            if not experiment or experiment['execution_status'] not in {'pending','failed','cancelled'}:
                raise ValueError('只能执行排队实验或重试执行故障；新方法请新增实验')
            if action.method or action.experiment:
                raise ValueError('已有实验的方法已冻结；换方法请在同一问题下新增实验')
        elif not (action.question_id or action.question.strip()) or not (
                action.experiment or action.method.strip() and action.conditions.strip() and action.evaluation.strip()):
            raise ValueError('新实验需要研究问题、具体方法、数据条件和评价标准')
    elif action.action=='tool':
        from ai_system.modules.registry import capabilities
        capabilities().validate_research_tool(action.capability, action.inputs)
    elif action.action=='ask' and not action.message.strip():
        raise ValueError('请说明需要用户补充什么信息或作出什么决定')
    elif action.action=='literature':
        if context['literature_mode']=='off' or not action.query.strip():
            raise ValueError('文献检索需要有效查询且项目允许外部检索')
    elif action.action=='write' and not context['artifacts']:
        raise ValueError('尚无真实研究产物，先执行实验再撰写研究结果')


def preserved_progress(snapshot):
    count=sum(e['acceptance']=='accepted' and e['validity']=='current' for e in snapshot['experiments'].values())
    return f'已保留 {count} 项实验成果和全部执行记录。' if snapshot['executions'] else '原题和当前研究记录已保存，尚未开始实验。'


def planning_failure_message(error, snapshot):
    from .runtime import decision_error_summary
    return '下一步操作未能准备完成：'+decision_error_summary(error)+'。'+preserved_progress(snapshot)


def decision_block_message(store, snapshot):
    action=snapshot.get('action') or {}
    return action.get('message') or action.get('error') or action.get('reason') or '研究操作未完成，已有产物已保存。'


class Planner:
    def __init__(self, llm):
        self.llm=llm

    async def decide(self, snapshot, context):
        from ..graph.llm_utils import call_structured, as_messages, cap_output
        model_name=str(getattr(self.llm,'model_name',None) or getattr(self.llm,'model',''))
        return await call_structured(cap_output(self.llm, 4800), NextAction,
            as_messages(PROMPT, json.dumps(context, ensure_ascii=False)),
            validate=lambda action: validate_action(action, snapshot, context),
            retries=1, json_first='qwen' in model_name.lower())

    async def amendment(self, snapshot, change, scope):
        from ..graph.llm_utils import call_structured, as_messages, cap_output
        from .validation import validate_spec,source_excerpt
        from .models import is_continuation
        if is_continuation(change):raise ValueError('继续/恢复不是实质修改')
        selected = scope if scope is not None else [key for key, t in snapshot["experiments"].items() if t["kind"] != "verify" and not t['retired']]
        payload = {"change": change, "scope": selected,'experiments':[{k:t[k] for k in ('task_id','objective')} for t in snapshot['experiments'].values() if t['task_id'] in selected],
                   "used_requirement_ids": [r["id"] for r in snapshot["spec"]["requirements"]]}
        def validate(contract):
            for requirement in contract.requirements:
                requirement.source_quote=source_excerpt(change,requirement.source_quote) or requirement.source_quote
            affected={key for r in contract.requirements for key in r.task_ids}
            if not affected or not affected<=set(selected):raise ValueError('修订只能关联实际受影响的已知任务')
            validate_spec(change,{'subproblems':[{'id':key} for key in affected],
                'requirements':[r.model_dump() for r in contract.requirements],'assumptions':[]})
            if any(r.id in payload['used_requirement_ids'] for r in contract.requirements):
                raise ValueError('补充要求编号与原要求重复')
        response = await call_structured(cap_output(self.llm,4800), AmendmentSpec,as_messages(
            "把用户补充要求转成结构化要求，使用新的RQ编号，source_quote逐字摘录change，task_ids只取scope中的任务。"
            "只关联change实际影响的问题，不把缺省scope当作修改所有问题；继续/恢复不是新要求。"
            "合并相同任务的要求，简短输出。方法选择或实现纠错作为goal，不虚构0/1自报检查。"
            "硬约束必须有可执行检查，数值阈值必须出现在本项逐字摘录内；检查编号全局唯一。"
            "原题约束保持不变；不要悄悄删除或放宽。", json.dumps(payload, ensure_ascii=False)),validate=validate)
        contract = response if isinstance(response, AmendmentSpec) else AmendmentSpec.model_validate(response)
        if not contract.requirements or scope is not None and {key for r in contract.requirements for key in r.task_ids} != set(scope):
            raise ValueError("补充要求未覆盖修改范围")
        return contract.requirements


class AmendmentSpec(Contract):
    requirements: list[Requirement]


def propose_revision(snapshot, command_id, base_revision, change, scope, requirements):
    from .models import is_continuation
    if is_continuation(change):raise ValueError('继续/恢复不是实质修订，不得生成要求或作废成果')
    selected = sorted({key for r in requirements for key in r.task_ids}) if scope is None else scope
    selected = [key for key in selected if snapshot["experiments"][key]["kind"] != "verify"]
    if not change.strip() or not selected or len(set(selected)) != len(selected):
        raise ValueError("修改内容和范围必须有效")
    return ResearchRevision(command_id=command_id,base_revision=base_revision,change=change,
                            experiment_ids=selected, requirements=requirements)
