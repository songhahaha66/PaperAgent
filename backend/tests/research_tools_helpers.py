"""Fixtures for the normal tool protocol; never used by production code."""
import json
from typing import Callable
from types import SimpleNamespace
from pydantic import PrivateAttr
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from ai_system.research import operations
from ai_system.research.questions import QuestionUpdate


def add_question(store,question,**values):
    return operations.update_question(SimpleNamespace(research=store),QuestionUpdate(question=question,**values).model_dump())['question_id']


def add_experiment(store,question=None,*,question_id=None,method='fixed arithmetic',conditions='fixed source',evaluation='exact result',title='',dependency_ids=(),plan=None):
    """Seed an imported experiment for historical reader/worker unit tests only."""
    qid=question_id or add_question(store,question)
    design=plan or {'conditions':conditions,'evaluation':evaluation,
        'candidates':[{'name':title or method,'instruction':method}]}
    with store._transaction() as db:
        state=store._read(db);q=state['questions'][qid]
        key='E'+str(max((int(k[1:]) for k in state['experiments'] if k.startswith('E') and k[1:].isdigit()),default=0)+1)
        requirement_ids=[r['id'] for r in state['spec']['requirements']]
        task={'task_id':key,'question_id':qid,'revision':1,'objective':q['question'],
            'source':state['spec']['requirement'],'dependencies':list(dependency_ids),'requirement_ids':requirement_ids,
            'assumption_ids':[],'kind':'compute','retired':False,'execution_status':'pending',
            'acceptance':'pending','validity':'current','execution_id':None,'report_ref':None,
            'method_instruction':method,'experiment':design,'investigates':[],'reason':'historical test fixture',
            'subproblem':{'id':key,'title':title or method,'question':q['question'],'kind':'compute',
                          'depends_on':list(dependency_ids),'deliverables':[]}}
        state['experiments'][key]=task
        for requirement in state['spec']['requirements']:requirement['task_ids'].append(key)
        store._bind_assumption_inputs(state)
        store._save(db,state,'fixture_imported_experiment',{'experiment_id':key})
    return key


def _manual_worker_handoff(store):
    with store._transaction() as db:
        state=store._read(db)
        if state.get('action'):
            state['action']['status']='done';store._save(db,state,'test_manual_worker_handoff',{})


def continue_experiment(store,experiment_id,information_need='修复原实验执行，复用已有上游结果'):
    """Seed a historical continuation; live Agents use the generic Python tool."""
    with store._transaction() as db:
        state=store._read(db);task=state['experiments'].get(experiment_id)
        if not task or task['retired'] or task['validity']!='current':raise ValueError('实验不存在或已失效')
        if task.get('execution_id'):
            task['continuation']={'parent_attempt':task['execution_id'],'information_need':information_need}
        task['execution_status']='pending'
        store._save(db,state,'fixture_continuation',{'experiment_id':experiment_id})
    _manual_worker_handoff(store)
    return {'experiment_id':experiment_id,'question_id':task['question_id'],'action_id':None}


def record_writing_inputs(ctx,source,subproblems,*,reports=None,designs=None):
    """Persist synthetic worker receipts for writing tests, using the real store."""
    from dataclasses import asdict
    from ai_system.research.store import ResearchStore
    from ai_system.research.models import WorkerResult
    from ai_system.research.control import graph_view,refresh_registry
    store=ResearchStore(ctx.workspace,'writing-fixture')
    store.initialize(source,{'subproblems':subproblems,'assumptions':[],
        'requirements':[{'id':'RQ0','statement':source,'source_quote':source,'kind':'goal',
                         'task_ids':[sp['id'] for sp in subproblems],'checks':[]}]},output_mode=ctx.output_mode)
    for sp in subproblems:
        results=[asdict(r) for r in ctx.registry.for_subproblem(sp['id'])]
        store.commit(WorkerResult(order=store.claim(sp['id']),execution_status='succeeded',acceptance='accepted',
            report=(reports or {}).get(sp['id'],{'status':'completed','summary':'Recorded writing inputs'}),
            design=(designs or {}).get(sp['id']),results=results))
    ctx.research=store
    refresh_registry(ctx)
    return graph_view(store.snapshot())


class ResearchToolsModel(BaseChatModel):
    respond: Callable
    _calls: int = PrivateAttr(default=0)
    @property
    def calls(self):return self._calls
    @property
    def _llm_type(self):return 'controlled-research-tools'
    def bind_tools(self,tools,**kwargs):return self
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self._calls+=1
        return ChatResult(generations=[ChatGeneration(message=self.respond(messages))])


def tool(name,args,call_id='call'):
    return AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':call_id}])


def research_step(messages,plan,transform=lambda text:text):
    """Drive the saved fixture goals using only context and actual tool receipts.

    No store access, generated IDs, acceptance overrides or completed-state
    writes are available to this controlled model.
    """
    from copy import deepcopy
    from langchain_core.messages import HumanMessage,ToolMessage
    context=None;context_index=0
    for index,message in enumerate(messages):
        if not isinstance(message,HumanMessage):continue
        text=message.content
        if isinstance(text,str) and text.startswith('当前研究记录已更新：'):text=text.split('：',1)[1]
        try:value=json.loads(text)
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and {'questions','source'}<=value.keys():context=value;context_index=index
    if context is None:raise AssertionError('Research tools need the actual scoped context')
    questions={q['id']:deepcopy(q) for q in context['questions']}
    runs={r['run_id']:deepcopy(r) for r in context.get('runs',[])}
    for message in messages[context_index+1:]:
        if not isinstance(message,ToolMessage):continue
        try:value=json.loads(message.content)
        except (ValueError,TypeError):
            raise AssertionError('Unexpected research fixture tool error: '+str(message.content))
        if message.status=='error' and not (value.get('run_id') and value.get('execution_status') in {'failed','cancelled','interrupted'}):
            raise AssertionError('Unexpected research fixture tool error: '+str(message.content))
        if isinstance(value.get('question'),dict):questions[value['question_id']]=value['question']
        for run in value.get('runs',[]):runs[run['run_id']]=run
        if value.get('run_id'):runs[value['run_id']]=value
    def call(name,args):
        from uuid import uuid4
        return tool(name,args,'fixture-research-'+uuid4().hex)
    for target in plan.subproblems:
        matches=[q for q in questions.values() if not q.get('retired') and
                 (q['title']==target.title or q['question']==target.question)]
        assert len(matches)<=1
        if not matches:return call('research_create_question',{'question':target.question,'title':target.title})
        question=matches[0]
        own=[r for r in runs.values() if r.get('question_id')==question['id']
             and (r.get('execution_status') or r.get('status')) in {'success','succeeded'}]
        if len(own)<2:
            code=("forecast = 1234.5678\nprint('forecast', forecast)\nprint('saving',0.085)" if not own else
                  "import matplotlib.pyplot as plt\nplt.plot([1,2,3])")
            return call('research_run_python',{'question_id':question['id'],'code':code,'title':target.title})
    def question_text(q):
        own=[r for r in runs.values() if r.get('question_id')==q['id']
             and (r.get('execution_status') or r.get('status')) in {'success','succeeded'}]
        return '本次条件下的实际结果：'+'；'.join(r.get('observation',r.get('output_tail','')) for r in own)+'。仅限已执行条件，未执行条件仍未知。'
    body='# '+plan.title+'\n\n'+'\n\n'.join('## '+q['title']+'\n'+question_text(q) for q in questions.values())
    common=next((json.loads(m.content) for m in messages if isinstance(m,HumanMessage) and str(m.content).startswith('{') and 'template_requirements' in json.loads(m.content)),{})
    if common.get('template_requirements'):
        from helpers.outline import template_outline
        body='# '+plan.title+'\n\n'+'\n\n'.join('## '+item['title']+'\n'+('\n'.join(q['title']+'：'+question_text(q) for q in questions.values())) for item in template_outline(common['template_requirements']))
    if context['source'].get('output_mode')=='latex':
        from helpers.native_document import native_document,escape_plain
        body=native_document(plan.title,escape_plain(body),'')
    body=transform(body)
    import hashlib
    edits=[m for m in messages[context_index+1:] if getattr(m,'name',None)=='writing_edit']
    if context.get('document',{}).get('version')!=hashlib.sha256(body.encode()).hexdigest() and not edits:
        return call('writing_edit',{'path':'paper.tex' if context['source'].get('output_mode')=='latex' else 'paper.md',
            'expected_version':context.get('document',{}).get('version'),'content':body})
    return call('research_deliver',{'title':plan.title,'reason':'实际输出与局限已写入正文，交付当前文件。'})
