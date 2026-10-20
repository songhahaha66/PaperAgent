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
from test_paper_graph import decision_outline
from ai_system.research.models import IssueReport
from ai_system.research.literature import LiteratureStore
from test_native_latex import accepted_store
from test_research_p2 import problem,TASK,submit
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
    snapshot=env.store.snapshot()
    for task in snapshot['experiments'].values():
        task['report'].update(summary='all requirements are covered by accepted tasks',
                              key_findings=['unsubstantiated completion claim'],limitations=['Only the stated conditions were examined.'])
    key,context=planner.decision_context(env.store,snapshot)
    assert all('report' not in task and 'execution_id' not in task for task in context['experiments'])
    assert all('execution_status' in task and 'acceptance' in task for task in context['experiments'])
    assert 'all requirements are covered by accepted tasks' not in json.dumps(context)
    assert 'unsubstantiated completion claim' not in json.dumps(context)
    assert all(task['limitations']==['Only the stated conditions were examined.'] for task in context['experiments'])
    assert context['spec']['requirements']==snapshot['spec']['requirements']
    assert next(t['objective'] for t in context['experiments'] if t['task_id']=='Q1')==snapshot['experiments']['Q1']['objective']
    assert {'demand','allocation','residual','independent'}<={e['name'] for e in context['evidence']}
    revised=copy.deepcopy(snapshot);revised['experiments']['Q1']['revision']+=1
    new_key,new_context=planner.decision_context(env.store,revised)
    assert new_context!=context and new_key!=key, 'A changed attempt frontier requires a new action decision, not a completion inference'
































def add_passage(store):
    literature=LiteratureStore(store.root)
    source=literature.upsert({'title':'Estimate demand: representative sampling','url':'https://example.org/research',
                              'authors':['Known Author'],'provider':'controlled'})
    literature.add_passages(source,[{'text':'Estimate demand requires checking representative sampling and explicitly retaining uncertainty in the inference.',
                                    'locator':'原文第 1 段'}],'webpage',source['url'],'source-content')
    return source,literature.passages()[-1]
