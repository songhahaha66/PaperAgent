"""Offline architecture contracts; no external models or historical user data."""
import asyncio
import copy
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.graph.registry import ResultRegistry
from ai_system.research.domain import current_result_refs, experiment_report
from ai_system.research.feedback import ReportEdit, FeedbackDecision, interpretation_version, revise_interpretation
from ai_system.research.loop import create_experiment, prepare_action
from ai_system.research.models import WorkerResult, IssueReport, ResearchConflict
from ai_system.research.planner import NextAction, QuestionUpdate, Judgment, decision_context, validate_action
from ai_system.research.questions import apply_questions
from ai_system.research.store import ResearchStore
from ai_system.research.views import questions
from ai_system.research.experiment_views import experiments_view
from services.file_services.workspace_fs import WorkspaceFS


@pytest.fixture
def study(tmp_path):
    store=ResearchStore(tmp_path,'architecture');store.initialize('Compare aggregate records and selected subjects.')
    eid=create_experiment(store,NextAction(action='experiment',reason='Measure source rows',information_need='Need record count',
        question_updates=[{'question':'What does the supplied material establish?'}],method='count source rows',conditions='entire source',evaluation='retain original row identity'))
    order=store.claim(eid)
    fs=WorkspaceFS(str(tmp_path));run=fs.create_run();fs.save_run_input(run,'print(12)');fs.save_run_stdout(run,'12\n');fs.finish_run(run,'success')
    registry=ResultRegistry(fs,allocate_id=store.allocate_ref)
    value=registry.record_value(eid,'source record count','12',run,meaning={
        'object':'source observations','scope':'entire source','statistic':'number of source records','lineage':[]})
    aux=registry.record_value(eid,'auxiliary count','12',run)
    claim=registry.set_claims(eid,[{'statement':'Unjustified explanation {{R:'+value.id+'}}','strength':'倾向'}])[0]
    report={'summary':'Observed {{R:'+value.id+'}}','approach':'row count',
            'key_findings':[{'statement':claim.statement,'strength':claim.strength}],
            'requirement_evidence':[{'requirement_id':'RQ0','evidence_refs':[value.id]}]}
    store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',report=report,
                             results=[asdict(value),asdict(aux)],claims=[asdict(claim)]))
    qid=store.snapshot()['experiments'][eid]['question_id']
    with store._transaction() as db:
        state=store._read(db);state['questions'][qid].update(judgment='Current answer',artifact_refs=[value.id],answer_status='current',coverage='answered',stop_reason='All supplied source rows are accounted for')
        store._save(db,state,'test_answer',{})
    return SimpleNamespace(store=store,eid=eid,qid=qid,value=value,aux=aux,registry=registry,run=run,order=order)


def test_edit_changes_current_claims_and_history_without_a_run(study):
    before=study.store.snapshot();task=before['experiments'][study.eid]
    revise_interpretation(study.store,study.eid,interpretation_version(before,task),
        ReportEdit(summary='Observed {{R:'+study.value.id+'}}',key_findings=[{'statement':'Narrower supported interpretation {{R:'+study.value.id+'}}'}]))
    after=study.store.snapshot()
    assert after['executions']==before['executions']
    assert after['artifacts'][study.value.id]==before['artifacts'][study.value.id]
    assert after['experiments'][study.eid]['execution_id']==task['execution_id']
    assert len(study.store.registry_payload()['claims'])==1
    assert 'Narrower' in study.store.registry_payload()['claims'][0]['statement']
    assert any(not c['active'] for c in after['claims'].values())
    assert any(a['type']=='report' and a['report']==experiment_report(before,task) for a in after['artifacts'].values())
    assert questions(after)[0]['answer_status']=='needs_update'
    assert not study.store.delivery_ready(after)


def test_false_semantic_suspicion_can_be_dismissed_against_retained_fact(study):
    with study.store._transaction() as db:
        state=study.store._read(db)
        issue=study.store._record_issue(state,IssueReport(task_id=study.eid,category='data',message='Wrong unit assumption',evidence_refs=[study.value.id]),'suspected',source='semantic_check')
        study.store._save(db,state,'test_doubt',{})
    before=study.store.snapshot();task=before['experiments'][study.eid]
    revise_interpretation(study.store,study.eid,interpretation_version(before,task),decisions=[FeedbackDecision(
        issue_id=issue['id'],disposition='dismiss',reason='Original record scope contradicts this suspicion',evidence_refs=[study.value.id])])
    after=study.store.snapshot()
    assert after['feedback'][0]['status']=='dismissed'
    assert after['executions']==before['executions']
    assert after['experiments']==before['experiments']


def test_split_reuses_experiment_sources_without_recomputation(study):
    before=study.store.snapshot()
    with study.store._transaction() as db:
        state=study.store._read(db)
        apply_questions(state,[QuestionUpdate(question='What is known about the selected subjects?',reason='Refine original target',
            source_question_ids=[study.qid],experiment_ids=[study.eid])])
        study.store._save(db,state,'test_split',{})
    after=study.store.snapshot();rows=questions(after)
    assert len(rows)==2 and study.eid in rows[1]['experiment_ids']
    assert study.value.id in rows[1]['available_artifact_refs']
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']


def test_interpretation_version_conflict_preserves_parallel_update(study):
    before=study.store.snapshot();version=interpretation_version(before,before['experiments'][study.eid])
    edit=ReportEdit(summary='Revised',key_findings=[])
    revise_interpretation(study.store,study.eid,version,edit)
    changed=study.store.snapshot()
    with pytest.raises(ResearchConflict):revise_interpretation(study.store,study.eid,version,edit)
    assert study.store.snapshot()==changed


def test_view_defaults_to_references_actually_used_by_interpretation(study):
    view=experiments_view(study.store.snapshot(),lambda path,prefix='':path)[0]
    assert view['primary_result_refs']==[study.value.id]
    assert len(view['results'])==2
    assert view['record']['approach']=='row count'
    assert view['interpretation_version']


def test_same_numeric_value_never_guesses_a_reference(study):
    assert study.registry.ground_statement(study.eid,'The effect is 12.0')=='The effect is 12.0'


def test_semantic_doubts_do_not_automatically_schedule_another_review(study,monkeypatch):
    with study.store._transaction() as db:
        state=study.store._read(db)
        study.store._record_issue(state,IssueReport(task_id=study.eid,category='writing',message='Check interpretation'),'suspected',source='local_review')
        study.store._save(db,state,'test_doubt',{})
    async def decide(self,snapshot,context):return NextAction(action='pause',reason='Owner decides next step')
    async def event(*args):pass
    monkeypatch.setattr('ai_system.research.planner.Planner.decide',decide)
    ctx=SimpleNamespace(research=study.store,planner_llm=None,max_planning_calls=None,events=SimpleNamespace(stage=event,emit=event))
    assert asyncio.run(prepare_action(ctx))
    assert study.store.snapshot()['action']['capability']=='pause'


def test_same_experiment_continuation_inherits_evidence_without_relabeling_source(study,monkeypatch):
    async def decide(self,snapshot,context):
        action=NextAction(action='continue',reason='Inspect additional condition within fixed method',
                          information_need='Need one additional descriptive measurement',experiment_id=study.eid)
        validate_action(action,snapshot,context);return action
    async def event(*args):pass
    monkeypatch.setattr('ai_system.research.planner.Planner.decide',decide)
    ctx=SimpleNamespace(research=study.store,planner_llm=None,max_planning_calls=None,events=SimpleNamespace(stage=event,emit=event))
    asyncio.run(prepare_action(ctx));order=study.store.claim(study.eid)
    before=study.store.snapshot();artifact=copy.deepcopy(before['artifacts'][study.value.id])
    report=experiment_report(before,before['experiments'][study.eid])
    study.store.commit(WorkerResult(order=order,execution_status='succeeded',acceptance='accepted',report=report,
        results=[asdict(study.value),asdict(study.aux)]))
    after=study.store.snapshot()
    assert len(after['experiments'])==1 and len(after['executions'])==2
    assert after['executions'][order.attempt_id]['parent_attempt']==study.order.attempt_id
    assert after['artifacts'][study.value.id]==artifact
    assert study.value.id in current_result_refs(after,{study.eid})


def test_schema_four_cutover_preserves_original_and_removes_stale_current_claim(study):
    state=study.store.snapshot();state['schema_version']=4
    task=state['experiments'][study.eid];state['artifacts'][task['report_ref']]['report']['key_findings']=[]
    raw=json.dumps(state)
    with study.store._transaction() as db:
        db.execute('UPDATE research SET payload=? WHERE id=?',(raw,'architecture'))
        db.execute('DELETE FROM domain_migrations WHERE version=7')
    migrated=ResearchStore(study.store.root,'architecture')
    assert migrated.snapshot()['schema_version']==5 and not migrated.registry_payload()['claims']
    assert migrated.snapshot()['executions']==state['executions']
    with migrated._connect() as db:
        assert db.execute("SELECT raw FROM legacy_records WHERE source='research/architecture/v4'").fetchone()[0].decode()==raw
    assert ResearchStore(study.store.root,'architecture').snapshot()==migrated.snapshot()


def test_continuation_reads_saved_input_and_runs_only_new_calculation(tmp_path,monkeypatch):
    """Actual subprocess kernels, controlled worker report, no external models."""
    from ai_system.research.control import execute, experiment_dispatch
    from ai_system.modules.experiments.planning import experiment_design
    async def run():
        ctx=GraphContext.create(str(tmp_path),planner_llm=None,max_review_rounds=0,check_claims=False)
        ctx.research=ResearchStore(tmp_path,'kernel-continuation')
        ctx.research.initialize('Count the supplied observations and derive a scaled count.')
        eid=create_experiment(ctx.research,NextAction(action='experiment',reason='Count',question_updates=[{'question':'What can be concluded?'}],
            method='count and scale',conditions='fixed observations',evaluation='exact arithmetic'))
        async def solve(branch,runtime):
            local=runtime.context
            if branch.get('continuation'):
                code="from pathlib import Path\nv=int(Path('outputs/intermediate/base.txt').read_text())\nprint(v*2)"
                name,value='scaled count','24'
            else:
                code="from pathlib import Path\nPath('outputs/intermediate').mkdir(parents=True,exist_ok=True)\nPath('outputs/intermediate/base.txt').write_text('12')\nprint(12)"
                name,value='base count','12'
            result=await local.kernels.get(eid).execute(code)
            assert result.ok
            item=local.registry.record_value(eid,name,value,result.run_id)
            report={'summary':'Observed {{R:'+item.id+'}}','approach':'count and scale','status':'completed','key_findings':[],
                'requirement_evidence':[{'requirement_id':'RQ0','evidence_refs':[item.id]}]}
            return {'solutions':{eid:report},'designs':{eid:experiment_design(branch['experiment'])}}
        monkeypatch.setattr('ai_system.modules.experiments.execution.solve',solve)
        try:
            await execute(experiment_dispatch(ctx,eid),SimpleNamespace(context=ctx))
            first=ctx.research.snapshot();assert first['experiments'][eid]['acceptance']=='accepted'
            with ctx.research._transaction() as db:
                state=ctx.research._read(db);task=state['experiments'][eid]
                task.update(continuation={'parent_attempt':task['execution_id'],'information_need':'Need scaled count'},execution_status='pending')
                ctx.research._save(db,state,'continue_requested',{})
            await execute(experiment_dispatch(ctx,eid),SimpleNamespace(context=ctx))
            final=ctx.research.snapshot()
            assert final['experiments'][eid]['acceptance']=='accepted'
            assert len(final['experiments'])==1 and len(final['executions'])==2
            assert {r['value'] for r in ctx.research.registry_payload()['results']}=={'12','24'}
            assert len(ctx.fs._manifest['runs'])==2
        finally:await ctx.kernels.close()
    asyncio.run(run())


def test_failed_additional_action_preserves_prior_current_evidence(study):
    before=study.store.snapshot();task=before['experiments'][study.eid]
    with study.store._transaction() as db:
        state=study.store._read(db)
        state['experiments'][study.eid].update(execution_status='pending',continuation={
            'parent_attempt':task['execution_id'],'information_need':'Need one further measurement'})
        study.store._save(db,state,'test_continue',{})
    order=study.store.claim(study.eid)
    study.store.commit(WorkerResult(order=order,execution_status='failed',acceptance='inconclusive',
        report={'status':'failed','error':'local execution fault'},
        issues=[IssueReport(task_id=study.eid,category='execution',message='local execution fault')]))
    after=study.store.snapshot()
    assert after['experiments'][study.eid]['execution_id']==task['execution_id']
    assert after['experiments'][study.eid]['report_ref']==task['report_ref']
    assert study.store.registry_payload()==study.store.registry_payload(before)
    assert after['executions'][order.attempt_id]['execution_status']=='failed'
    assert any(a['type']=='report' and a['report'].get('error')=='local execution fault' for a in after['artifacts'].values())



def test_current_requirement_binding_reuses_evidence_without_backdating_checks(study):
    from ai_system.research.models import ResearchRevision
    before=study.store.snapshot()
    patch=ResearchRevision(command_id='new-scope',base_revision=before['revision'],change='Inspect only the selected subset',
        experiment_ids=[study.eid],requirements=[{'id':'RQ0','statement':'Inspect only the selected subset',
            'source_quote':'Inspect only the selected subset','kind':'goal','task_ids':[study.eid],'checks':[]}])
    study.store.apply_revision_command('new-scope',before['revision'],patch.change,[study.eid],patch)
    after=study.store.snapshot()
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']
    assert after['experiments']==before['experiments']
    assert after['spec']['requirements'][0]['revision']==2
    assert after['questions'][study.qid]['answer_status']=='needs_update'
    assert current_result_refs(after,{study.eid})==current_result_refs(before,{study.eid})
    assert not study.store.delivery_ready(after)


def test_evaluate_reuses_ancestor_output_once_and_newer_same_name_wins(study):
    from ai_system.research.validation import evaluate
    state=study.store.snapshot();task=state['experiments'][study.eid]
    parent=task['execution_id'];new_id='additional-action'
    rules=[{'owner_kind':'requirement','owner_id':'RQ0','owner_revision':1,'check':{
        'id':'count','description':'Exact record count','left':{'task_id':study.eid,'name':study.value.name},
        'operator':'eq','expected':12,'tolerance':0}}]
    state['executions'][new_id]={'parent_attempt':parent,'experience_basis':{'protocol':rules}}
    task.update(execution_id=new_id,execution_status='running')
    inherited=evaluate(state,study.eid,[asdict(study.value)])
    assert inherited.outcome=='accepted' and inherited.checks[0].evidence_refs==[study.value.id]
    replacement={**asdict(study.value),'id':'R99','value':'15'}
    newer=evaluate(state,study.eid,[asdict(study.value),replacement])
    assert newer.outcome=='rejected' and newer.checks[0].evidence_refs==['R99']


def test_logical_feedback_dismissal_needs_no_invented_result_reference(study):
    with study.store._transaction() as db:
        state=study.store._read(db)
        issue=study.store._record_issue(state,IssueReport(task_id=study.eid,category='writing',message='Wording allegedly implies causality'),'suspected',source='semantic_check')
        study.store._save(db,state,'test_advisory',{})
    snapshot=study.store.snapshot();task=snapshot['experiments'][study.eid]
    revise_interpretation(study.store,study.eid,interpretation_version(snapshot,task),decisions=[FeedbackDecision(
        issue_id=issue['id'],disposition='dismiss',reason='The sentence explicitly limits itself to a descriptive association')])
    current=study.store.snapshot()
    assert current['feedback'][0]['status']=='dismissed'
    with study.store._transaction() as db:
        state=study.store._read(db)
        repeated=study.store._record_issue(state,IssueReport(task_id=study.eid,category='writing',message=issue['message']),'suspected',source='semantic_check')
        assert not repeated['blocking']



@pytest.mark.parametrize('outcome',['completed','needs_attention'])
def test_new_revision_reopens_finished_study_and_idempotent_retry_does_not(study,monkeypatch,outcome):
    from ai_system.research.models import ResearchRevision
    study.store.set_outcome(outcome)
    before=study.store.snapshot();change='Reassess the selected scope'
    patch=ResearchRevision(command_id='reopen',base_revision=before['revision'],change=change,
        experiment_ids=[study.eid],requirements=[{'id':'RQ0','statement':change,'source_quote':change,
            'kind':'goal','task_ids':[study.eid],'checks':[]}])
    receipt=study.store.apply_revision_command('reopen',before['revision'],change,[study.eid],patch)
    revised=study.store.snapshot()
    assert revised['outcome']=='running'
    assert revised['executions']==before['executions'] and revised['artifacts']==before['artifacts']
    observed=[]
    async def decide(self,snapshot,context):
        observed.append(snapshot['revision'])
        return NextAction(action='pause',reason='New revision reached the research decision loop')
    async def event(*args):pass
    monkeypatch.setattr('ai_system.research.planner.Planner.decide',decide)
    ctx=SimpleNamespace(research=study.store,planner_llm=None,max_planning_calls=None,
        events=SimpleNamespace(stage=event,emit=event))
    assert asyncio.run(prepare_action(ctx))
    assert observed==[revised['revision']]
    study.store.set_outcome('completed')
    finished=study.store.snapshot()
    assert study.store.apply_revision_command('reopen',before['revision'],change,[study.eid],patch)==receipt
    assert study.store.snapshot()==finished


def test_empty_observe_rejected_but_current_answer_change_is_allowed(study):
    state=study.store.snapshot();_,context=decision_context(study.store,state)
    with pytest.raises(ValueError,match='没有实际'):
        validate_action(NextAction(action='observe',reason='Think again'),state,context)
    changed=NextAction(action='observe',reason='Narrow the explanation',judgments=[
        Judgment(question_id=study.qid,statement='Limited to the observed source rows',artifact_refs=[study.value.id])])
    validate_action(changed,state,context)
    unchanged=NextAction(action='observe',reason='No new information',judgments=[Judgment(
        question_id=study.qid,statement='Current answer',artifact_refs=[study.value.id],
        coverage='answered',stop_reason='All supplied source rows are accounted for')])
    with pytest.raises(ValueError,match='没有实际'):validate_action(unchanged,state,context)
    state['questions'][study.qid]['answer_status']='needs_update'
    validate_action(unchanged,state,context)  # Reassessment may retain the same answer.


def test_delivery_closes_each_problem_without_requiring_positive_answers(study):
    state=study.store.snapshot()
    assert study.store.delivery_ready(state)
    apply_questions(state,[QuestionUpdate(question='Does this generalize beyond the supplied source?')])
    assert not study.store.delivery_ready(state)
    other=next(q for key,q in state['questions'].items() if key!=study.qid)
    other.update(coverage='unanswered',stop_reason='No observations beyond this source were provided')
    assert study.store.delivery_ready(state)
    other.update(coverage='partial',judgment='Only the supplied range is described')
    assert study.store.delivery_ready(state)
    other.update(coverage='answered')
    assert not study.store.delivery_ready(state)  # A positive answer still needs real evidence.


def test_question_scope_filters_other_runtime_judgments(study):
    from services.chat_services.research_session import research_snapshot,scoped_snapshot
    with study.store._transaction() as db:
        state=study.store._read(db)
        ids=apply_questions(state,[QuestionUpdate(question='Other question marker')])
        study.store._save(db,state,'test_other_question',{})
    other=ids[0]
    from ai_system.research import ENGINE_VERSION
    (study.store.root/'.system/graph_thread.json').write_text(json.dumps({'engine_version':ENGINE_VERSION,'thread_id':study.store.research_id}))
    snapshot=research_snapshot(study.store.root,'work','paused')
    snapshot.runtime={'actions':[
        {'id':'a','question_id':study.qid,'status':'done','inputs':{'secret':'debug'}},
        {'id':'b','question_id':other,'status':'done','purpose':'OTHER_ONLY'}],
        'history':[{'id':'decision','question_id':None,'reason':'OTHER_ONLY','judgments':[
            {'question_id':study.qid,'statement':'Selected answer'},
            {'question_id':other,'statement':'OTHER_ONLY'}]}],
        'models':[{'id':'m','question_id':other,'status':'done','purpose':'OTHER_ONLY'}]}
    scoped=scoped_snapshot(snapshot,study.qid)
    serialized=scoped.model_dump_json()
    assert 'OTHER_ONLY' not in serialized and 'Other question marker' not in serialized
    assert scoped.runtime['history'][0]['judgments']==[{'question_id':study.qid,'statement':'Selected answer'}]
    assert len(scoped.runtime['actions'])==1 and 'inputs' not in scoped.runtime['actions'][0]
    assert scoped.delivery is None and not scoped.artifacts


def test_decision_context_limits_full_records_to_focused_question(study):
    other=create_experiment(study.store,NextAction(action='experiment',reason='Another target',
        question_updates=[{'question':'Other goal'}],method='OTHER_METHOD',conditions='other source',evaluation='other exact check'))
    with study.store._transaction() as db:
        state=study.store._read(db)
        state['action']={'id':'focus','question_id':study.qid,'status':'done','capability':'research.observe'}
        study.store._save(db,state,'test_focus',{})
    _,context=decision_context(study.store,study.store.snapshot())
    assert {e['task_id'] for e in context['experiments']}=={study.eid}
    assert len(context['questions'])==2  # Goal coverage remains visible to the research layer.
    assert 'OTHER_METHOD' not in json.dumps(context,ensure_ascii=False)
    assert 'history' not in context and 'recent_actions' not in context
