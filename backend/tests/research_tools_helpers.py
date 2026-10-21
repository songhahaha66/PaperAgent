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
    with store._transaction() as db:
        state=store._read(db);state['action']['status']='done';store._save(db,state,'test_manual_worker_handoff',{})
    return result['experiment_id']


def answer(store,**values):
    return operations.update_answer(SimpleNamespace(research=store),operations.AnswerUpdate(**values).model_dump())


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
