"""Evidence-frontier decisions drive the actual loop; all model/network replies are controlled."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.research import control,loop,planner
from helpers.graph import decision_outline
from ai_system.research.models import IssueReport
from ai_system.research.literature import LiteratureStore
from helpers.paper import accepted_store
from helpers.contracts import problem,TASK,submit
from test_review_cache import no_network


def run(value):return asyncio.run(value)






@pytest.fixture
def env(tmp_path):
    store=accepted_store(tmp_path)
    with store._transaction() as db:
        saved=store._read(db);saved['materials']={'inputs':store.file_inputs(),'profile':''};store._save(db,saved,'materials_profiled',{})
    events=[]
    class Stream:
        async def send_json_block(self,kind,content):events.append((kind,content))
    def context(model):
        ctx=GraphContext.create(str(tmp_path),planner_llm=model,stream_manager=Stream())
        ctx.research=store
        return ctx
    return SimpleNamespace(store=store,context=context,events=events,
        state={'task':TASK,'analysis':problem().model_dump(),'sections':{}})


def observe(env,ctx):return run(control.observe(env.state,SimpleNamespace(context=ctx)))








def test_decision_context_uses_products_and_purposes_without_completion_shortcuts(env):
    from ai_system.research.domain import experiment_report
    snapshot=env.store.snapshot()
    qid=snapshot['experiments']['Q1']['question_id']
    snapshot['questions'][qid].update(judgment='',artifact_refs=[],coverage=None,stop_reason='',answer_status='unanswered')
    for key,task in snapshot['experiments'].items():
        refs=[r['id'] for r in env.store.registry_payload(snapshot)['results'] if r['subproblem']==key]
        experiment_report(snapshot,task).update(summary='all requirements are covered by accepted tasks {{R:'+refs[0]+'}}',
            key_findings=[{'statement':'local result {{R:'+refs[0]+'}}','strength':'倾向'}],
            limitations=['Only the stated conditions were examined.'])
    key,context=planner.decision_context(env.store,snapshot)
    assert all('report' not in task and 'execution_id' not in task for task in context['experiments'])
    assert all('execution_status' in task and 'acceptance' in task for task in context['experiments'])
    [record]=context['experiments']
    assert record['record']=={k:v for k,v in experiment_report(snapshot,snapshot['experiments']['Q1']).items()
                             if k in ('summary','key_findings','limitations','status','error')}
    assert 'all requirements are covered by accepted tasks' in record['record']['summary']
    assert record['record']['limitations']==['Only the stated conditions were examined.']
    question=next(q for q in context['questions'] if q['id']==qid)
    assert question['answer_status']=='unanswered' and not question['coverage'] and not question['judgment']
    assert not env.store.delivery_ready(snapshot), 'a report cannot declare all user questions answered'
    assert context['source']['requirements']==snapshot['spec']['requirements']
    assert record['objective']==snapshot['experiments']['Q1']['objective']
    assert {r['name'] for e in context['experiments'] for r in e['results']}=={'demand'}
    assert 'artifacts' not in context
    assert {'demand','allocation','residual','independent'}<={e['name'] for e in context['artifact_index']}
    revised=copy.deepcopy(snapshot);revised['experiments']['Q1']['revision']+=1
    new_key,new_context=planner.decision_context(env.store,revised)
    assert new_context!=context and new_key!=key, 'an interpretation frontier change requires a fresh research judgment'

































def add_passage(store):
    literature=LiteratureStore(store.root)
    source=literature.upsert({'title':'Estimate demand: representative sampling','url':'https://example.org/research',
                              'authors':['Known Author'],'provider':'controlled'})
    literature.add_passages(source,[{'text':'Estimate demand requires checking representative sampling and explicitly retaining uncertainty in the inference.',
                                    'locator':'原文第 1 段'}],'webpage',source['url'],'source-content')
    return source,literature.passages()[-1]
