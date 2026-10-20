"""Bounded planning over immutable requirements and evidence-backed issues."""

from ai_system.research.domain import findings as research_findings
import json
import re
from pydantic import ConfigDict, Field, field_validator
from langsmith import traceable
from typing import Literal

from .models import Contract, Requirement, PlanPatch, TaskChange, PlanProposal


class DecisionBasis(Contract):
    ref: str
    quote: str = Field(default='', max_length=2000, description='可省略，由后端绑定该引用的实际原文；提供时必须逐字摘录')


class RequirementCoverage(Contract):
    requirement_id: str
    requirement: str = Field(min_length=1, description='逐字复制该要求的完整statement，不省略其中的并列目的或输出')
    supported: str = Field(max_length=3000, description='实际产物具体回答了哪些部分，给出对应答案及其边界；执行通过、任务数和文件存在不是答案。没有实质答案时为空')
    basis: list[DecisionBasis] = Field(default_factory=list, max_length=12, description='仅引用支撑本项具体答案的实际产物或原文')
    remaining: str = Field(max_length=2000, description='原要求中尚无相应产物回答的部分；只在研究目的已完整回答时为空。交付性要求说明待写作/导出实施的部分')


class DecisionTaskChange(TaskChange):
    task_id: str = Field(default='', description='add时仅为本次决策的临时标识，可用任意唯一名称或省略；后端分配正式编号。revise/retire时必须使用已有任务编号。')


class ResearchCommand(PlanProposal):
    model_config = ConfigDict(json_schema_extra={'anyOf': [
        {'properties': {'action': {'enum': ['deliver']}, 'coverage': {'minItems': 1},
                        'outline': {'minItems': 1}, 'task_ids': {'maxItems': 0}},
         'required': ['coverage', 'outline']},
        {'properties': {'action': {'enum': ['execute']}}, 'anyOf': [
            {'properties': {'task_ids': {'minItems': 1}}, 'required': ['task_ids']},
            {'properties': {'changes': {'minItems': 1}}, 'required': ['changes']}]},
        {'properties': {'action': {'enum': ['inspect', 'capability', 'research', 'literature', 'ask', 'pause']},
                        'task_ids': {'maxItems': 0}}},
    ]})
    action: Literal['execute','inspect','capability','research','literature','ask','pause','deliver']
    changes: list[DecisionTaskChange] = Field(default_factory=list)
    requirement_ids: list[str] = Field(min_length=1)
    basis: list[DecisionBasis] = Field(default_factory=list, max_length=12)
    coverage: list[RequirementCoverage] = Field(default_factory=list, description='仅deliver时逐项核对全部原要求及实际依据，每项恰好一次')
    gap: str = Field(default='', max_length=2000, description='尚待解决的研究缺口；research省略时复用reason，deliver必须为空')
    query: str = Field(default='', max_length=300)
    task_ids: list[str] = Field(default_factory=list,max_length=3)
    question: str = Field(default='',max_length=2000)
    outline: list['EvidenceSection'] = Field(default_factory=list,max_length=16)
    requirements: list[Requirement] = Field(default_factory=list, description='按需摘录原题要求或补齐尚未执行任务的检查，不能覆盖原要求；原题全文始终保留于RQ0')
    title: str = Field(default='',max_length=160,description='仅收敛时根据实际成果拟定论文标题')
    capability: str = ''
    inputs: dict = Field(default_factory=dict)

    @field_validator('changes', mode='before')
    @classmethod
    def change_aliases(cls, changes):
        return [c.model_dump() if isinstance(c,TaskChange) else c for c in changes]


class EvidenceSection(Contract):
    key: str
    title: str = Field(min_length=1,max_length=120)
    purpose: str = Field(min_length=1,max_length=1500)
    task_ids: list[str] = Field(default_factory=list)
    basis: list[DecisionBasis] = Field(default_factory=list,max_length=12)


ResearchCommand.model_rebuild()
# Read historical command receipts; this contract is never sent to the planner.
EvidenceDecision = ResearchCommand


def assign_task_ids(proposal, snapshot):
    """Resolve decision-local aliases before validation; allocation has no side effects.

    The saved, validated proposal carries the IDs through patch receipts and
    dispatch. Re-validating against the same snapshot produces identical IDs.
    Existing task references must never be silently redirected to new work.
    """
    additions=[change for change in proposal.changes if change.action=='add']
    if not additions:return
    aliases=[change.task_id for change in additions if change.task_id]
    if len(set(aliases))!=len(aliases):
        raise ValueError('新增任务的临时标识重复，无法区分任务引用')
    if set(aliases)&snapshot['tasks'].keys():
        raise ValueError('新增任务的临时标识与已有任务冲突，无法区分新增研究和已有成果')
    numbers=[int(match[1]) for key in snapshot['tasks'] if (match:=re.fullmatch(r'Q([0-9]+)[a-z]?',key))]
    first=max(numbers,default=0)+1
    assigned=[f'Q{first+index}' for index in range(len(additions))]
    mapping={change.task_id:key for change,key in zip(additions,assigned) if change.task_id}
    for change,key in zip(additions,assigned):change.task_id=key
    for change in proposal.changes:
        if change.question_key:change.question_key=mapping.get(change.question_key,change.question_key)
        if change.dependencies is not None:
            change.dependencies=[mapping.get(key,key) for key in change.dependencies]
    proposal.task_ids=[mapping.get(key,key) for key in proposal.task_ids]
    if proposal.action=='execute' and not proposal.task_ids:
        proposal.task_ids=assigned
    for owner in [*proposal.requirements,*proposal.assumptions]:
        owner.task_ids=[mapping.get(key,key) for key in owner.task_ids]
        for check in owner.checks:
            check.left.task_id=mapping.get(check.left.task_id,check.left.task_id)
            if check.right:check.right.task_id=mapping.get(check.right.task_id,check.right.task_id)
    for section in proposal.outline:
        section.task_ids=[mapping.get(key,key) for key in section.task_ids]
        if section.key.startswith('solve:'):
            key=section.key.removeprefix('solve:')
            section.key='solve:'+mapping.get(key,key)


def prepare_decision(proposal, snapshot, context):
    if proposal.action=='research' and not proposal.gap.strip():
        proposal.gap=proposal.reason[:2000]
    assign_task_ids(proposal,snapshot)
    evidence={row['id']:row for row in context['evidence']}
    for basis in [*proposal.basis,*(b for c in proposal.coverage for b in c.basis),*(b for s in proposal.outline for b in s.basis)]:
        if not basis.quote and basis.ref in evidence:basis.quote=evidence[basis.ref]['text'][:2000]
    for change in proposal.changes:
        if change.experiment and context.get('skill_policy',{}).get('mode','auto')=='auto':
            from ai_system.modules.methods import METHOD_INDEX
            # Unknown resource tags are not an algorithm or an executable skill.
            # Keep the candidate instruction intact and never claim to load a
            # nonexistent resource. Explicitly disabled, known keys still fail.
            for candidate in change.experiment.candidates:
                candidate.method_keys=[key for key in candidate.method_keys if key in METHOD_INDEX]
        if change.experiment and not change.method_instruction:
            change.method_instruction='；'.join(c.instruction for c in change.experiment.candidates)[:3000]
    # Filling an omitted execution list must still honor schema limits.
    EvidenceDecision.model_validate(proposal.model_dump())
    validate_decision(proposal,snapshot,context)


def preserved_progress(snapshot):
    accepted=sum(t['acceptance']=='accepted' and t['validity']=='current' for t in snapshot['tasks'].values())
    if accepted:return f'已保留 {accepted} 项已验收任务的成果与执行记录。'
    if snapshot['attempts']:return f"已保留 {len(snapshot['attempts'])} 次执行记录，尚无通过验收的研究结果。"
    return '尚未开始实验计算，目前没有已验收的研究结果；原题和当前记录已保存。'


def planning_failure_message(error, snapshot):
    reason=str(error).strip() or type(error).__name__
    return f'系统未能生成可执行的下一步操作，已保留执行位置和技术诊断。{preserved_progress(snapshot)}恢复后会重新准备操作，无需手工修改审查意见。'


def decision_block_message(store, snapshot):
    key,_=decision_context(store,snapshot)
    record=store.planning_decision(key) or {}
    if record.get('apply_error'):
        return f"后续研究方案提交失败：{record['apply_error'][:300]}。{preserved_progress(snapshot)}可继续重试。"
    if record.get('status')=='planning_failed':
        return record.get('message') or planning_failure_message(record.get('error') or '上次决策未通过校验',snapshot)
    status=record.get('status')
    reason={'limited':'已达到研究规划次数上限，尚未形成可交付方案。',
            'collecting':'资料查证的完成情况尚不确定，需核对已保存的检索记录。',
            'collection_failed':'资料查证未取得所需证据，研究缺口尚未解决。',
            'operation_uncertain':'上次研究操作的完成情况尚不确定，需要核对执行记录。'}.get(status)
    if not reason and record.get('proposal',{}).get('action')=='pause':
        reason=record['proposal'].get('gap') or record['proposal'].get('reason')
    return (reason or '下一步研究决策尚未完成。')+' '+preserved_progress(snapshot)


def decision_context(store, snapshot):
    """One evidence frontier, independent of commits, writing and UI refreshes."""
    from .literature import LiteratureStore
    from .store import digest
    from ai_system.modules.experiments.store import ExperimentStore
    results=store.registry_payload(snapshot)['results']
    literature=LiteratureStore(store.root)
    sources=literature.retrieve(snapshot['spec']['requirement'],limit=12)
    evidence=[{'id':r['id'],'task_id':r['subproblem'],'name':r['name'],
               'text':str(r['value'] if r.get('value') is not None else r['name']),'unit':r.get('unit',''),'run_id':r['run_id']} for r in results]
    evidence += [{'id':s['id'],'name':s['source'].get('title') or '文献摘录','text':s['text'],
                  'locator':s['locator'],'boundary':s['boundary'],'source_url':s['source_url'],
                  'content_hash':s['content_hash']} for s in sources]
    evidence += [{'id':summary['id'],'task_id':summary['task_id'],'kind':'comparison',
                  'name':' / '.join(row['name'] for row in summary['branches'])+' 共同协议实测比较',
                  'text':json.dumps(summary,ensure_ascii=False,sort_keys=True)}
                 for summary in ExperimentStore(store).accepted_summaries(snapshot)]
    research_tasks=list(snapshot['tasks'].values())
    # Status/failure records steer actions; they are never scientific evidence.
    tasks=[{**{k:t.get(k) for k in ('task_id','objective','research_question','dependencies','requirement_ids','kind','revision',
            'execution_status','acceptance','validity','retired','method_instruction','experiment')},
            'limitations':(t.get('report') or {}).get('limitations',[])} for t in research_tasks]
    assumptions={key:{**{k:a[k] for k in ('statement','task_ids','checks')},'refuted':a['status']=='refuted'}
                 for key,a in snapshot['assumptions'].items() if a['status']!='retired'}
    actions=[{'action':r['proposal']['action'],'reason':r['proposal'].get('reason',''),
              'status':r['status'],'result':r.get('result',{}),'response':r.get('response','')}
             for _,r in decision_records(store) if r.get('proposal') and r['proposal'].get('action') not in {'deliver','pause'} and
             r.get('status') in {'done','collected','collection_failed','replied','operation_uncertain'}][:12]
    files=store.file_inputs();materials=snapshot.get('materials') or {}
    attempts=[{**{k:a.get(k) for k in ('attempt_id','task_id','revision','execution_status','acceptance')},
               'error':str((a.get('result',{}).get('report') or {}).get('error',''))[:1500],
               'published':a.get('commit',{}).get('published',False)} for a in snapshot['attempts'].values()]
    context={'controller_version':5,'spec':snapshot['spec'],'tasks':tasks,'assumptions':assumptions,
             'evidence':evidence,'inputs':files,'literature_mode':literature.policy(),
             'materials':materials.get('profile','') if materials.get('inputs')==files else '材料已变更或尚未查看，可选择inspect；不把文件存在当成分析结果。',
             'judgments':snapshot.get('judgments',[])[-20:],'ready_tasks':ready_tasks(snapshot),'attempts':attempts,'recent_actions':actions,
             'issues':[i for i in research_findings(snapshot) if i['category'] not in {'writing','service'} and i['status'] not in {'resolved','dismissed','superseded'}]}
    context['experiments']=[]
    for task in research_tasks:
        if not task.get('experiment'):continue
        attempt=snapshot['attempts'].get(task.get('active_attempt'),{})
        inputs=attempt.get('input_refs',{})
        plan=task['experiment']
        comparison=ExperimentStore(store).read(task['task_id'],task['revision']) if plan.get('csv') else None
        context['experiments'].append({'task_id':task['task_id'],'revision':task['revision'],
            'comparison_scope':digest([task['objective'],plan['conditions'],plan['evaluation'],plan.get('csv'),inputs]),
            'conditions':plan['conditions'],'evaluation':plan['evaluation'],'candidates':plan['candidates'],
            'measured':task['acceptance']=='accepted' and task['validity']=='current',
            'result_refs':[e['id'] for e in evidence if e.get('task_id')==task['task_id']],
            'execution_status':task['execution_status'],
            'observations':{'status':comparison['status'],'reason':comparison.get('reason'),
                'branches':[{k:r.get(k) for k in ('name','status','error')} for r in comparison['branches']]} if comparison else None,
            'limitations':(task.get('report') or {}).get('limitations',[]),
            'decision_reason':task.get('reason','')})
    if any(name.lower().endswith('.csv') for name in files):
        from ai_system.modules.experiments.store import SupervisedSpec
        context['csv_comparison_protocol']=SupervisedSpec.model_json_schema()
    from ai_system.modules.registry import capabilities
    context['capabilities']=capabilities().research_tools()
    context['skill_policy']=store.skill_preferences()['policy']
    from ai_system.modules.methods import allowed_method_keys
    context['available_method_keys']=sorted(allowed_method_keys(context['skill_policy']))
    from ai_system.graph.context import TEMPLATE_CONTRACT,TEMPLATE_REQUIREMENTS_CHARS
    template=store.root/TEMPLATE_CONTRACT
    context['template_requirements']=template.read_text(encoding='utf-8')[:TEMPLATE_REQUIREMENTS_CHARS] if template.is_file() else ''
    # Keep version/attempt identity for deduplication without offering completion
    # labels to the model as an answer to the research question.
    versions=[{k:t.get(k) for k in ('task_id','revision','active_attempt','acceptance','validity','retired')}
              for t in research_tasks]
    return 'research-next:'+digest({'context':{k:v for k,v in context.items() if k!='judgments'},'task_versions':versions,
        'assumption_versions':[(key,a['revision']) for key,a in snapshot['assumptions'].items()]}),context


def validate_decision(proposal, snapshot, context):
    from .store import ResearchStore
    from .validation import bind_requirements
    if proposal.requirements and proposal.action not in {'research','execute'}:
        raise ValueError('仅在创建研究或执行前绑定原题要求与检查')
    if len({r.id for r in proposal.requirements})!=len(proposal.requirements):
        raise ValueError('要求绑定不能重复')
    requirements={r['id']:r for r in bind_requirements(snapshot,proposal.requirements)}
    source=snapshot['spec']['requirement']+'\n'+'\n'.join(snapshot['spec']['amendments'])
    selected=set(proposal.requirement_ids)
    if not selected or not selected<=requirements.keys():
        raise ValueError('下一步决策必须关联原要求')
    known={e['id']:e for e in context['evidence']}
    def check_basis(items, requirement_ids):
        targets={key for rid in requirement_ids for key in requirements[rid]['task_ids']}
        for basis in items:
            row=known.get(basis.ref)
            if not row or not basis.quote.strip() or basis.quote not in row['text']:
                raise ValueError('决策依据须引用evidence中的实际结果或文献；审查意见不是实测证据，科研疑点应通过issue_ids关联，不能据此重跑已验收任务')
            if row.get('task_id') and not any(key in snapshot['tasks'] and snapshot['tasks'][key]['kind']!='verify' and
                    ResearchStore._reaches(snapshot,row['task_id'],key) for key in targets):
                raise ValueError('决策结果与所选原要求无关')
    # Optional evidence is still a provenance claim whenever it is supplied.
    check_basis(proposal.basis,selected)
    for item in proposal.coverage:
        if item.requirement_id not in requirements:raise ValueError('覆盖引用必须关联原要求')
        check_basis(item.basis,[item.requirement_id])
    if proposal.action=='deliver':
        covered=[c.requirement_id for c in proposal.coverage]
        if len(set(covered))!=len(covered) or set(covered)!=requirements.keys():
            raise ValueError('收敛时必须逐项核对全部原要求，不能遗漏或重复')
        for item in proposal.coverage:
            requirement=requirements[item.requirement_id]
            if item.requirement!=requirement['statement']:
                raise ValueError('覆盖说明必须保留完整原要求，不能省略部分研究目的')
            if requirement['kind']!='deliverable':
                if item.remaining.strip():raise ValueError('原要求仍有未回答部分，不能直接收敛写作')
                if not item.supported.strip() or not item.basis:
                    raise ValueError('声称研究要求已回答时必须给出实质答案及本项实际依据')
    if not proposal.reason.strip():raise ValueError('必须说明本次行动的目的')
    if proposal.action!='research' and (proposal.assumptions or proposal.issue_ids):
        raise ValueError('只有定向研究修订可以进入既有反证修复协议')
    if proposal.action!='execute' and proposal.task_ids:raise ValueError('只有执行行动能派出任务')
    if proposal.action!='deliver' and proposal.title:raise ValueError('论文标题在研究收敛时确定')
    if proposal.action!='deliver' and proposal.outline:raise ValueError('仅在实际证据已能回答原题时组织交付大纲')
    if proposal.action!='ask' and proposal.question:raise ValueError('只有缺少必要信息时询问用户')
    if proposal.action!='capability' and (proposal.capability or proposal.inputs):raise ValueError('能力参数只能用于通用能力行动')
    if proposal.action=='capability':
        from ai_system.modules.registry import capabilities
        capabilities().validate_research_tool(proposal.capability,proposal.inputs)
        if proposal.changes or proposal.query:raise ValueError('工具调用不能同时修改原问题或方法')
    elif proposal.action=='execute':
        if any(c.action!='add' for c in proposal.changes):raise ValueError('执行中只可创建本次需要的新研究；方法修订仍由research处理')
        if proposal.query or not proposal.task_ids or len(set(proposal.task_ids))!=len(proposal.task_ids):
            raise ValueError('执行须选择当前就绪任务，不同时改方法或检索')
        additions={c.task_id:c for c in proposal.changes}
        if not set(additions)<=set(proposal.task_ids):raise ValueError('只创建本次实际执行的研究')
        if not set(proposal.task_ids)<=set(context['ready_tasks']) | additions.keys():raise ValueError('不能执行非就绪或已经完成的任务')
        def related_requirements(key):
            if key in additions:return set(additions[key].requirement_ids or [])
            task=snapshot['tasks'][key]
            targets={key,*task['dependencies']}
            if task.get('verifies_issue'):
                targets.update(i['task_id'] for i in research_findings(snapshot) if i['id']==task['verifies_issue'])
            return {rid for rid,r in requirements.items() if targets & set(r['task_ids'])}
        if not all(related_requirements(key) & selected for key in proposal.task_ids):
            raise ValueError('执行任务必须关联本次未解原要求')
    if proposal.action in {'research','execute'}:
        if proposal.action=='research':
            if not proposal.gap.strip():raise ValueError('补充研究缺少研究理由或实际缺口说明')
            if not (proposal.changes or proposal.assumptions or proposal.requirements):
                raise ValueError('补充研究未提供任务、假设修订或要求绑定，无法执行')
            if proposal.query:raise ValueError('研究修订不能同时发起文献检索，请选择literature行动')
        if len({c.task_id for c in proposal.changes})!=len(proposal.changes):
            raise ValueError('后续研究任务不能重复')
        if sum(c.action!='retire' for c in proposal.changes)>snapshot['max_total_attempts']-len(snapshot['attempts']):
            raise ValueError('现有执行次数不足以完成所提后续研究')
        for change in proposal.changes:
            if change.experiment:
                from ai_system.modules.methods import allowed_method_keys
                # The same policy is checked again in the worker against its live configuration.
                policy=context.get('skill_policy')
                if policy and any(not set(c.method_keys)<=allowed_method_keys(policy) for c in change.experiment.candidates):
                    raise ValueError('候选方法使用了当前不允许的技能')
                if change.kind!='compute':raise ValueError('候选实验必须是计算任务，独立核查仍沿用问题协议')
            if change.action!='add':
                task=snapshot['tasks'].get(change.task_id)
                if not task or task['kind']=='verify' or task['retired'] or task['acceptance']=='accepted':
                    raise ValueError('方法调整只针对未验收分支，不能重跑或停止已验收成果')
                if change.objective!=task['objective'] or change.dependencies is not None or change.requirement_ids is not None:
                    raise ValueError('调整方法或停止尝试不能改写研究问题、依赖和原要求')
                if change.action=='revise' and not change.method_instruction.strip():raise ValueError('须明确新的尝试策略')
                if change.action=='revise' and task.get('experiment') and change.experiment is None:
                    raise ValueError('修订候选实验须记录新版候选方法与评价条件，不能静默替换原方法')
                if not set(task['requirement_ids']) & selected:raise ValueError('方法调整必须对应当前研究缺口')
                continue
            if change.kind=='verify':
                raise ValueError('独立核查任务由问题协议调度，不能作为普通新增研究创建')
            if change.task_id in snapshot['tasks']:
                raise ValueError('新增研究编号与已有任务冲突，不能覆盖已有研究')
            if not re.fullmatch(r'Q[0-9]+[a-z]?',change.task_id):
                raise ValueError('新增研究尚未分配后端任务编号')
            if 'RQ0' in requirements and requirements['RQ0']['statement']==snapshot['spec']['requirement'] and 'RQ0' not in (change.requirement_ids or []):
                raise ValueError('新增研究必须关联完整原题RQ0，不能用局部摘录代替原目标')
            if not change.requirement_ids or not set(change.requirement_ids)<=selected:
                raise ValueError('后续任务必须绑定本次决策的原要求')
            if not set(change.dependencies or [])<=snapshot['tasks'].keys():
                raise ValueError('后续研究依赖未知任务')
            if not all(snapshot['tasks'][key]['acceptance']=='accepted' and snapshot['tasks'][key]['validity']=='current'
                       for key in change.dependencies or []):
                raise ValueError('后续研究依赖尚未验收')
            if change.dependencies and not any(set(snapshot['tasks'][key]['requirement_ids']) & set(change.requirement_ids) for key in change.dependencies):
                raise ValueError('后续研究依赖与原要求无关')
        from .validation import validate_spec
        from .store import validate_dependencies
        tasks={key:{**task['subproblem'],'depends_on':task['dependencies']} for key,task in snapshot['tasks'].items() if task['kind']!='verify'}
        for change in proposal.changes:
            if change.action=='add':
                tasks[change.task_id]={'id':change.task_id,'depends_on':change.dependencies or []}
                for rid in change.requirement_ids:
                    requirements[rid]={**requirements[rid],'task_ids':list(dict.fromkeys([*requirements[rid]['task_ids'],change.task_id]))}
        validate_spec(source,{'subproblems':list(tasks.values()),'requirements':[{k:v for k,v in r.items() if k!='revision'} for r in requirements.values()],
                             'assumptions':[{k:v for k,v in a.items() if k in {'id','statement','task_ids','checks'}} for a in snapshot['assumptions'].values()]})
        dependencies={'tasks':{key:{'dependencies':sp['depends_on']} for key,sp in tasks.items()}}
        validate_dependencies(dependencies['tasks'])
        for requirement in requirements.values():
            for check in requirement['checks']:
                if check.get('right') and not ResearchStore._reaches(dependencies,check['right']['task_id'],check['left']['task_id']):
                    raise ValueError('跨任务验收操作数必须声明真实任务依赖')
        if proposal.action=='execute':
            for rid,requirement in requirements.items():
                for task_id in set(proposal.task_ids)&set(requirement['task_ids']):
                    if requirement['kind']=='constraint' and not any(c['left']['task_id']==task_id for c in requirement['checks']):
                        raise ValueError('相关原题约束必须在执行前绑定本实验检查')
        # The store retains the existing counterevidence/assumption contracts.
        known_issues={i['id']:i for i in research_findings(snapshot)}
        if any(i not in known_issues or known_issues[i]['status']!='confirmed' or known_issues[i]['action']!='recompute' for i in proposal.issue_ids):
            raise ValueError('反证修复只能关联当前已确认问题')
        if proposal.assumptions and not proposal.issue_ids:raise ValueError('不能无反证修改冻结假设')
    if proposal.action=='literature':
        if context['literature_mode']=='off':
            raise ValueError('项目已关闭外部文献检索，不能自动查找')
        if not proposal.gap.strip() or len(proposal.query.strip())<2 or proposal.changes:
            raise ValueError('文献补证必须说明缺口和检索问题，不能同时改研究')
    elif proposal.action in {'inspect','ask','pause'}:
        if proposal.changes or proposal.query:raise ValueError('查看材料、询问和暂停不能偷偷修改研究')
        if proposal.action=='ask' and not proposal.question.strip():raise ValueError('须说明缺少哪些必要信息')
    elif proposal.action=='deliver':
        if proposal.changes or proposal.query or proposal.gap.strip() or selected!=requirements.keys():
            raise ValueError('仍有未解决缺口或未覆盖原要求时不能直接收敛写作')
        delivery={**snapshot,'feedback':[i for i in snapshot['feedback'] if not (i['category']=='writing' and i['source']=='sections')],
                  'faults':[i for i in snapshot['faults'] if i['category']!='service']}
        if not ResearchStore.delivery_ready(delivery):raise ValueError('仍有未停止的尝试或未解决的实质问题，不能交付')
        if not proposal.outline:raise ValueError('收敛决策必须按实际结果组织论文大纲')
        keys=[s.key for s in proposal.outline]
        if len(set(keys))!=len(keys):raise ValueError('大纲章节不能重复')
        known_keys={'abstract','restatement','analysis','assumptions','symbols','sensitivity','evaluation','references','solve_heading'}
        for section in proposal.outline:
            if section.key not in known_keys and not re.fullmatch(r'(?:solve:Q[0-9]+[a-z]?|custom:[a-z0-9_-]+)',section.key):
                raise ValueError('大纲需使用现有写作章节入口')
            if not set(section.task_ids)<=snapshot['tasks'].keys():raise ValueError('大纲关联未知研究')
            if section.key.startswith('solve:'):
                task=snapshot['tasks'].get(section.key.split(':',1)[1])
                if not task or task['retired'] or task['acceptance']!='accepted' or task['validity']!='current':
                    raise ValueError('求解章节只能表达当前已验收成果')
            check_basis(section.basis,requirements.keys())
        cited={b.ref for s in proposal.outline for b in s.basis}
        if not {b.ref for c in proposal.coverage if requirements[c.requirement_id]['kind']!='deliverable' for b in c.basis}<=cited:
            raise ValueError('结果驱动大纲必须承接逐项覆盖所依据的实际证据')


class AmendmentSpec(Contract):
    requirements: list[Requirement]


def propose_revision(snapshot, command_id, base_revision, change, scope, requirements):
    from .models import is_continuation
    if is_continuation(change):raise ValueError('继续/恢复不是实质修订，不得生成要求或作废成果')
    selected = sorted({key for r in requirements for key in r.task_ids}) if scope is None else scope
    selected = [key for key in selected if snapshot["tasks"][key]["kind"] != "verify"]
    if not change.strip() or not selected or len(set(selected)) != len(selected):
        raise ValueError("修改内容和范围必须有效")
    return PlanPatch(command_id=command_id, base_revision=base_revision, reason=change,
        changes=[TaskChange(task_id=key, objective=snapshot["tasks"][key]["objective"] + "\n\n补充要求：" + change,
            requirement_ids=snapshot["tasks"][key]["requirement_ids"] + [r.id for r in requirements if key in r.task_ids]) for key in selected],
        requirements=requirements, amendment=change)


def ready_tasks(snapshot):
    tasks = snapshot["tasks"]
    if len(snapshot["attempts"]) >= snapshot["max_total_attempts"]:
        return []
    blocked = {key for i in research_findings(snapshot) if i["status"] == "confirmed" and i["action"] == "recompute"
               for key in i["repair_targets"]}
    return [key for key, task in tasks.items() if not task["retired"] and key not in blocked
            and task["execution_status"] == "pending"
            and sum(a["task_id"] == key and a["revision"] == task["revision"] for a in snapshot["attempts"].values()) < snapshot["max_attempts"]
            and all(snapshot["assumptions"][aid]["status"] != "refuted" for aid in task["assumption_ids"])
            and all(tasks[dep]["acceptance"] == "accepted" and tasks[dep]["validity"] == "current" for dep in task["dependencies"])]


def decision_records(store):
    with store._connect() as db:
        return [(r[0],json.loads(r[1])) for r in db.execute(
            "SELECT id,response FROM commands WHERE research_id=? AND id LIKE 'research-next:%' ORDER BY rowid DESC",
            (store.research_id,))]


def literature_action(store):
    current=current_decision(store)
    if current and current[1]['proposal']['action']=='literature' and current[1]['status']=='planned':
        key,record=current;proposal=record['proposal']
        return {'capability':'literature.collect','inputs':{'query':proposal['query'],'limit':3,'fetch_open_access':True},'decision_key':key}
    return None


def current_decision(store):
    key,_=decision_context(store,store.snapshot());record=store.planning_decision(key)
    return (key,record) if record and record.get('status') in {'planned','done'} else None


def begin_collection(store, key):
    from .literature import LiteratureStore
    record=store.planning_decision(key)
    if not record or record['status']!='planned' or decision_context(store,store.snapshot())[0]!=key:
        raise ValueError('检索所依据的研究状态已改变，请重新观察')
    literature=LiteratureStore(store.root)
    with store._connect() as db:
        after=db.execute('SELECT COALESCE(MAX(id),0) FROM literature_operations').fetchone()[0]
    record.update(status='collecting',operation_after=after,passages_before=[p['id'] for p in literature.passages()])
    store.save_planning_decision(key,record)


async def finish_collection(ctx, key, result):
    from .literature import LiteratureStore
    record=ctx.research.planning_decision(key)
    search=result.get('search') or {}
    passages=[p['id'] for p in LiteratureStore(ctx.workspace).passages()
              if p['source_id'] in search.get('source_ids',[]) and p['id'] not in record.get('passages_before',[])]
    acquired=search.get('status') in {'completed','partial'} and bool(passages)
    record.update(status='collected' if acquired else 'collection_failed',
                  result={'status':search.get('status','failed'),'passage_ids':passages,
                          'source_ids':search.get('source_ids',[]),'error_count':len(search.get('errors',[]))})
    ctx.research.save_planning_decision(key,record)
    await ctx.events.stage('已保留新取得的文献摘录，将依据原要求决定是否还需研究；取得资料不表示结论已证实。' if acquired else
                           '文献查证未取得新的可定位摘录；检索记录已保留，研究缺口尚未解决，暂停交付。')


async def recover_collection(ctx, key, record):
    """Reconcile a saved operation; never issue a second search after interruption."""
    with ctx.research._connect() as db:
        operations=[json.loads(r[0]) for r in db.execute('SELECT payload FROM literature_operations WHERE id>?',
                                                       (record['operation_after'],))]
    matches=[r for r in operations if r.get('kind')=='search' and r.get('query')==record['proposal']['query']]
    if len(matches)==1 and matches[0].get('status') in {'completed','partial','failed'}:
        await finish_collection(ctx,key,{'search':matches[0]})
        return True
    await ctx.events.stage('上次文献查证的完成情况尚不确定，已保留记录并暂停；不会自动重复检索。')
    return False


async def advance_research(ctx):
    """One decision at each changed material/action/attempt/evidence state."""
    store=ctx.research
    for previous_key,previous in decision_records(store):
        if previous.get('status')=='collecting' and not await recover_collection(ctx,previous_key,previous):
            return True
    snapshot=store.snapshot()
    key,context=decision_context(store,snapshot)
    for previous_key,previous in decision_records(store):
        if previous.get('status')=='planned' and previous_key!=key:
            # A patch receipt proves a completed application after a crash. Other
            # old candidates may not be executed against a different frontier.
            previous['status']='done' if store.planning_decision(previous_key+':apply') else 'superseded'
            store.save_planning_decision(previous_key,previous)
    snapshot=store.snapshot()
    key,context=decision_context(store,snapshot)
    record=store.planning_decision(key)
    if record and record['status']=='limited':
        # Old limit receipts could incorrectly permit writing. A limit is never
        # a convergence verdict, including after upgrading an existing project.
        if record.get('blocked') is not True:
            store.save_planning_decision(key,{**record,'blocked':True})
        return True
    if record and record['status']=='done':
        try:
            completed=EvidenceDecision.model_validate(record.get('proposal'))
            validate_decision(completed,snapshot,context)
        except ValueError as exc:
            store.save_planning_decision(key,{**record,'status':'planning_failed','error_type':type(exc).__name__,
                'error':str(exc)[:2000],'message':planning_failure_message(exc,snapshot)})
            return True
        return completed.action!='deliver'
    if record and record['status'] not in {'planned','retry'}:
        # A previous failed/uncertain call is durable. Only explicit resume (for
        # planning) or changed evidence may request another bounded decision.
        return True
    if not record or record['status']=='retry':
        if not store.reserve_planning_call(key,limit=ctx.max_planning_calls):
            store.save_planning_decision(key,{'status':'limited','blocked':True})
            await ctx.events.stage('已达到规划或执行资源上限，当前证据尚无明确的收敛决策；已保留原要求和全部结果并暂停交付。')
            return True
        try:
            from .views import activity
            from ai_system.graph.tracing import labelled
            with activity(ctx,'research.observe'), labelled('observe'):
                planner=Planner(ctx.planner_llm)
                proposal=await planner.decide(snapshot,context)
            proposal=EvidenceDecision.model_validate(proposal)
            prepare_decision(proposal,snapshot,context)
            if decision_context(store,store.snapshot())[0]!=key:
                raise ValueError('决策期间研究证据已改变')
            record={'status':'planned','base_revision':snapshot['revision'],'proposal':proposal.model_dump(),'intent':planner.last_intent}
            store.save_planning_decision(key,record)
        except BaseException as exc:
            message=planning_failure_message(exc,snapshot)
            store.save_planning_decision(key,{'status':'planning_failed','error_type':type(exc).__name__,'error':str(exc)[:2000],'message':message})
            if not isinstance(exc,Exception):
                raise
            await ctx.events.stage(message)
            return True
        names={r['id']:r['statement'] for r in snapshot['spec']['requirements']}
        names.update({t['task_id']:t['subproblem'].get('title','相关研究') for t in snapshot['tasks'].values()})
        names.update({e['id']:e['name'] for e in context['evidence']})
        human=lambda text:re.sub(r'\b(?:RQ\d+|Q\d+[a-z]?|[RFC]\d+|P[a-f0-9]{24})\b',lambda m:names.get(m[0],'相关证据'),text)
        action={'deliver':'按结果组织论文','research':'调整研究尝试','literature':'查找相关文献原文',
                'execute':'执行下一项研究','inspect':'查看已有材料','capability':'调用已注册研究能力','ask':'询问必要信息','pause':'暂停并保留研究'}[proposal.action]
        await ctx.events.stage(f"发现：{human(proposal.gap or proposal.reason)}。接下来：{action}。依据：{human(proposal.reason)}")
    proposal=EvidenceDecision.model_validate(record['proposal'])
    if proposal.action in {'execute','inspect','capability','ask','literature'}:
        return False
    try:
        if proposal.action=='research':
            store.apply_patch(PlanPatch(command_id=key+':apply',base_revision=record['base_revision'],
                reason=proposal.reason,changes=proposal.changes,assumptions=proposal.assumptions,issue_ids=proposal.issue_ids, requirements=proposal.requirements))
        record['status']='done'
        record.pop('apply_error',None)
        store.save_planning_decision(key,record)
        return proposal.action=='pause'
    except Exception as exc:
        # Keep the saved proposal for idempotent recovery; don't spend another
        # model call or let an unapplied research decision fall into writing.
        store.save_planning_decision(key,{**record,'apply_error':str(exc)[:2000]})
        await ctx.events.stage(decision_block_message(store,store.snapshot()))
        return True


class Planner:
    def __init__(self, llm):
        self.llm = llm

    @traceable(name='research.decide', process_inputs=lambda inputs: {'context': inputs['context']})
    async def decide(self, snapshot, context):
        from ..graph.llm_utils import call_structured, as_messages, cap_output
        from .intents import ResearchIntent, compile_intent, PROMPT
        model_name=str(getattr(self.llm,'model_name',None) or getattr(self.llm,'model',''))
        intent=await call_structured(cap_output(self.llm,4800),ResearchIntent,
            as_messages(PROMPT,json.dumps(context,ensure_ascii=False)),
            validate=lambda value:compile_intent(value,snapshot,context),
            retries=1,json_first='qwen' in model_name.lower())
        self.last_intent=intent.model_dump()
        return compile_intent(intent,snapshot,context)

    async def amendment(self, snapshot, change, scope):
        from ..graph.llm_utils import call_structured, as_messages, cap_output
        from .validation import validate_spec,source_excerpt
        from .models import is_continuation
        if is_continuation(change):raise ValueError('继续/恢复不是实质修改')
        selected = scope if scope is not None else [key for key, t in snapshot["tasks"].items() if t["kind"] != "verify" and not t['retired']]
        payload = {"change": change, "scope": selected,'tasks':[{k:t[k] for k in ('task_id','objective')} for t in snapshot['tasks'].values() if t['task_id'] in selected],
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
