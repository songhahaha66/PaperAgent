"""Offline architecture contracts; no external models or historical user data."""
from helpers.legacy_registry import value as legacy_value, figure as legacy_figure, claims as legacy_claims

from ai_system.sandbox.evidence import seal_run
import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from ai_system.graph.context import GraphContext
from ai_system.graph.registry import ResultRegistry
from ai_system.research.domain import experiment_report
from ai_system.research.feedback import ReportEdit, interpretation_version, revise_interpretation
from research_tools_helpers import add_experiment
from ai_system.research.models import WorkerResult, IssueReport, ResearchConflict
from ai_system.research.store import ResearchStore
from ai_system.research.views import questions
from services.file_services.workspace_fs import WorkspaceFS


pytestmark = pytest.mark.usefixtures("offline_kernel")

@pytest.fixture
def study(tmp_path):
    store=ResearchStore(tmp_path,'architecture');store.initialize('Compare aggregate records and selected subjects.')
    eid=add_experiment(store,'What does the supplied material establish?',method='count source rows',conditions='entire source',evaluation='retain original row identity')
    order=store.claim(eid)
    fs=WorkspaceFS(str(tmp_path));run=fs.create_run();fs.save_run_input(run,'print(12)');fs.save_run_stdout(run,'12\n');fs.finish_run(run,'success'); seal_run(fs, run)
    registry=ResultRegistry(fs)
    value=legacy_value(registry,eid,'source record count','12',run,meaning={
        'object':'source observations','scope':'entire source','statistic':'number of source records','lineage':[]})
    aux=legacy_value(registry,eid,'auxiliary count','12',run)
    claim=legacy_claims(registry,eid,[{'statement':'Unjustified explanation {{R:'+value.id+'}}','strength':'倾向'}])[0]
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
    assert study.store.delivery_ready(after), 'answer freshness stays visible without blocking file export'


def test_interpretation_version_conflict_preserves_parallel_update(study):
    before=study.store.snapshot();version=interpretation_version(before,before['experiments'][study.eid])
    edit=ReportEdit(summary='Revised',key_findings=[])
    revise_interpretation(study.store,study.eid,version,edit)
    changed=study.store.snapshot()
    with pytest.raises(ResearchConflict):revise_interpretation(study.store,study.eid,version,edit)
    assert study.store.snapshot()==changed


def test_continuation_reads_saved_input_and_runs_only_new_calculation(tmp_path,monkeypatch):
    """Actual subprocess kernels, controlled worker report, no external models."""
    from ai_system.research.control import execute, experiment_dispatch
    from ai_system.modules.experiments.planning import experiment_design
    async def run():
        ctx=GraphContext.create(str(tmp_path),planner_llm=None)
        ctx.research=ResearchStore(tmp_path,'kernel-continuation')
        ctx.research.initialize('Count the supplied observations and derive a scaled count.')
        eid=add_experiment(ctx.research,'What can be concluded?',method='count and scale',conditions='fixed observations',evaluation='exact arithmetic')
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
            report={'summary':'Observed '+value,'approach':'count and scale','status':'completed','key_findings':[]}
            return {'report':report,'design':experiment_design(branch['experiment'])}
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
            assert not ctx.research.registry_payload()['results']
            outputs=[ctx.fs.read_text(r['stdout']) for r in ctx.fs._manifest['runs'].values()]
            assert any('12' in t for t in outputs) and any('24' in t for t in outputs)
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
