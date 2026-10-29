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


def add_experiment(store,question=None,*,question_id=None,method='fixed arithmetic',conditions='fixed source',evaluation='exact result',title=''):
    qid=question_id or add_question(store,question)
    result=operations.schedule_experiment(SimpleNamespace(research=store),operations.ExperimentInput(
        question_id=qid,title=title,plan={'conditions':conditions,'evaluation':evaluation,'candidates':[{'name':title or method,'instruction':method}]}).model_dump())
    # These worker fixtures claim/commit manually; integration tests use Agent
    # dispatch, act and solve_join to verify the actual handoff instead.
    _manual_worker_handoff(store)
    return result['experiment_id']


def _manual_worker_handoff(store):
    with store._transaction() as db:
        state=store._read(db);state['action']['status']='done';store._save(db,state,'test_manual_worker_handoff',{})


def continue_experiment(store,experiment_id,information_need='修复原实验执行，复用已有上游结果'):
    result=operations.continue_experiment(SimpleNamespace(research=store),operations.ContinueInput(
        experiment_id=experiment_id,information_need=information_need).model_dump())
    _manual_worker_handoff(store)
    return result


def answer(store,**values):
    return operations.update_answer(SimpleNamespace(research=store),operations.AnswerUpdate(**values).model_dump())


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


def research_step(messages,plan):
    """Drive the saved fixture goals using only context and actual tool receipts.

    No store access, generated IDs, acceptance overrides or completed-state
    writes are available to this controlled model.
    """
    from copy import deepcopy
    from langchain_core.messages import HumanMessage,ToolMessage
    context=None
    for message in messages:
        if not isinstance(message,HumanMessage):continue
        text=message.content
        if isinstance(text,str) and text.startswith('当前研究记录已更新：'):text=text.split('：',1)[1]
        try:value=json.loads(text)
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and {'questions','experiments','source'}<=value.keys():context=value
    if context is None:raise AssertionError('Research tools need the actual scoped context')
    questions={q['id']:deepcopy(q) for q in context['questions']}
    records={e['task_id']:deepcopy(e) for e in context['experiments']}
    for message in messages:
        if not isinstance(message,ToolMessage):continue
        if message.status=='error':
            raise AssertionError('Unexpected research fixture tool error: '+str(message.content))
        value=json.loads(message.content)
        if isinstance(value.get('question'),dict):questions[value['question_id']]=value['question']
        for record in value.get('experiments',[]):records[record['task_id']]=record
        for judgment in value.get('judgments',[]):
            q=questions[judgment['question_id']]
            q.update(judgment,judgment=judgment['statement'],answer_status='current')
    def call(name,args):return tool(name,args,'fixture-research-'+str(len(messages)))
    known={}
    for target in plan.subproblems:
        matches=[q for q in questions.values() if not q.get('retired') and
                 (q['title']==target.title or q['question']==target.question or
                  any(e['question_id']==q['id'] and e['objective']==target.question for e in records.values()))]
        assert len(matches)<=1, 'Fixture targets must not guess between multiple questions'
        if not matches:return call('research_update_question',{'question':target.question,'title':target.title})
        question=matches[0];known[target.id]=question
        own=set(question.get('experiment_ids',[]))|set(question.get('related_experiment_ids',[]))
        own.update(eid for eid,e in records.items() if e['question_id']==question['id'])
        if own-set(records):return call('research_read_question',{'question_id':question['id']})
        experiments=[e for eid,e in records.items() if eid in own]
        if not experiments:
            dependencies=[known[dep] for dep in target.depends_on]
            assert all(q.get('coverage') in {'answered','partial'} for q in dependencies)
            return call('research_schedule_experiment',{'question_id':question['id'],'title':target.title,
                'plan':{'conditions':'当前提供的输入与上游实际结果','evaluation':'核对实际执行、登记结果和冻结要求',
                    'candidates':[{'name':target.title,'instruction':'落实当前问题的固定计算示例，保留代码和真实输出'}]},
                'dependency_ids':list(dict.fromkeys(eid for q in dependencies for eid in q['experiment_ids']))})
        pending=next((e for e in experiments if e['execution_status'] in {'pending','failed','cancelled'}),None)
        if pending:return call('research_continue_experiment',{'experiment_id':pending['task_id'],'information_need':'完成尚未成功的原实验，复用已有结果'})
        if any(e['acceptance']!='accepted' or e['validity']!='current' for e in experiments):
            return call('research_pause',{'message':'当前实验尚无可用于回答的有效结果，保留执行诊断供核实。'})
        if not question.get('judgment') or question.get('answer_status')=='needs_update' or not question.get('coverage'):
            evidence=[r for e in experiments for r in e['results']]
            refs=list(dict.fromkeys(r['id'] for r in evidence))
            text='本次条件下的实际结果：'+'；'.join(e['record'].get('summary','') for e in experiments)
            partial=question.get('answer_status')=='needs_update'
            return call('research_update_answer',{'question_id':question['id'],'statement':text,'artifact_refs':refs,
                'coverage':'partial' if partial else 'answered','stop_reason':'已汇总当前有效实验结果；未执行条件仍未知。',
                'limitations':['仅覆盖已执行条件，新增条件与外推范围需要另行验证。']})
    return call('research_deliver',{'title':plan.title,'reason':'各问题已有带实际来源的答案与局限，开始研究级交付。'})
