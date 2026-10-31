"""Pure mocks: no Python interpreter, compiler, model or remote server is run."""
import asyncio
import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.graph.context import GraphContext, KernelPool
from ai_system.modules.experiments import supervised
from ai_system.modules.experiments.handoff import publish_comparison
from ai_system.modules.experiments.store import SupervisedSpec, CandidateCode
from ai_system.sandbox.contracts import ExecutionOutcome
from ai_system.sandbox.remote_kernel import RemoteKernel
from ai_system.sandbox.settings import SandboxSettings
from ai_system.sandbox.file_limits import FileLimitExceeded
from ai_system.sandbox.artifacts import output_hashes, capture_local_outputs
from services.file_services.workspace_fs import WorkspaceFS
from services.file_services.workspace_lock import edit_lock
from services.chat_services import paper_project


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv('PA_DATA_PATH', str(tmp_path/'private'))
    monkeypatch.delenv('PAPERAGENT_SANDBOX_CONFIG', raising=False)


from helpers.remote_files import RemoteFiles


class MemorySession(RemoteFiles):
    root='/workspace'
    context_id='mock-context'
    def __init__(self, root, key, state):
        super().__init__(root)
        self.path=self.remote;self.state=state
        self.calls=[];self.fits=0;self.closed=False;self.prediction=None
    async def start(self):await super().start()
    async def execute(self, code, run_id, output):
        assert self.state['active']==0, 'pool must serialize actual computation'
        self.state['active']+=1
        try:
            # Computation owns a runtime lock, not the manuscript lock.
            with edit_lock(self.backend_root):pass
            await asyncio.sleep(0)
            self.calls.append(code)
            directory=self.path/'outputs/intermediate/comparisons/mock-comparison'
            if 'PROJECT_PREDICTIONS_SAVED' in code:
                assert all(row.get('code_hash') for row in self.state['record']['branches']), 'freeze both implementations before running either'
                index=0 if 'inputs-0.json' in code else 1
                payload=json.loads((directory/f'inputs-{index}.json').read_text())
                assert 'y_validation' not in payload
                self.fits+=1
                self.prediction=[3.,4.] if index==0 else [1.,2.]
                (directory/f'predictions-{index}.json').write_text(json.dumps({'prediction':self.prediction,'negative_prediction':[0.,0.],'estimator_audit':{'checked':True}}))
                await output('stdout','PROJECT_PREDICTIONS_SAVED\n')
            elif 'formal_metrics.json' in code:
                assert self.fits==1 and self.prediction is not None
                assert '.fit(' not in code, 'handoff must retain the fitted winner'
                values={'metric':0.,'baseline':1.5,'train_count':3,'validation_count':2,'total_count':5}
                candidates={'0':{'metric':2.,'baseline':1.5},'1':{'metric':0.,'baseline':1.5}}
                (directory/'formal_metrics.json').write_text(json.dumps({'values':values,'predictions':self.prediction,'candidates':candidates}))
                await output('stdout','\n'.join(str(v) for v in [*values.values(),2.,1.5,0.])+'\n')
            else:
                await output('stdout','2.0\n0.0\n1.5\n')
            return ExecutionOutcome()
        finally:self.state['active']-=1
    async def finalize(self, run_id):pass
    async def close(self):self.closed=True
    async def interrupt(self):pass


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    root=tmp_path/'private/project-files/project';root.mkdir(parents=True)
    state={'active':0};sessions={};creation=[]
    def factory(workspace, *, timeout, fs, experiment_id, execution_scope, **kwargs):
        session=MemorySession(Path(workspace),experiment_id,state);sessions[experiment_id]=session;creation.append(experiment_id)
        return RemoteKernel(workspace,session,SandboxSettings(provider='mock'),timeout=timeout,fs=fs,execution_scope=execution_scope)
    monkeypatch.setattr('ai_system.graph.context.create_kernel',factory)
    ctx=GraphContext.create(root,planner_llm=None)
    data_hash=hashlib.sha256(b'fixed data').hexdigest()
    store=SimpleNamespace(root=root,snapshot=lambda:None,file_inputs=lambda:{'data/input.csv':data_hash})
    ctx.research=store
    spec=SupervisedSpec(dataset='data/input.csv',target='y',features=['x'],metric='MAE').model_dump()
    frozen={**spec,'data_hash':data_hash,'train_ids':['a','b','c'],'validation_ids':['d','e'],
            'feature_dtypes':{'x':'float'},'higher_is_better':False,'baseline_value':1.5}
    payload={'X_train':[[0],[1],[2]],'X_validation':[[3],[4]],'columns':['x'],'y_train':[0.,0.,0.],'permuted_y_train':[0.,0.,0.],'seed':1,'task_type':'regression'}
    monkeypatch.setattr(supervised,'prepare',lambda *args:(frozen,payload,np.array([1.,2.]),np.array([0.,2.]),np.zeros(3),np.zeros(3)))
    # handoff imported prepare by name, so patch the same frozen input provider.
    monkeypatch.setattr('ai_system.modules.experiments.handoff.prepare',supervised.prepare)
    monkeypatch.setattr(supervised,'current',lambda *args,**kwargs:True)
    monkeypatch.setattr('ai_system.modules.experiments.handoff.current',supervised.current)
    class DB:
        record=None
        reservations=[]
        def __init__(self,*args):pass
        def read(self,*args):return self.record
        def policy(self):return {'version':1,'spec':spec}
        def create(self,tid,revision,record):record['id']='mock-comparison';self.record=record;return record
        def save(self,record):self.record=record;state['record']=record
        def reserve(self,*args,**kwargs):self.reservations.append(args);return True
    db=DB()
    monkeypatch.setattr(supervised,'ExperimentStore',lambda _:db)
    monkeypatch.setattr('ai_system.modules.experiments.handoff.ExperimentStore',lambda _:db)
    calls=[]
    async def ask(ctx,schema,system,prompt,timeout,*args):
        assert schema is CandidateCode
        index=json.loads(prompt)['candidate']['variant'];calls.append(index)
        return CandidateCode(code=f'def make_estimator(seed):\n    return {index}\n')
    monkeypatch.setattr(supervised,'ask',ask)
    candidates=[{'name':name,'method_keys':['regression'],'idea':'fixed candidate','formulation':'','steps':[]} for name in ('A','B')]
    branch={'subproblem':{'id':'Q1','question':'compare frozen methods'},'design':{'candidates':candidates,'chosen':candidates[0]},'research_contract':{'requirements':[]}}
    return SimpleNamespace(ctx=ctx,branch=branch,db=db,sessions=sessions,creation=creation,calls=calls)


def test_two_candidates_keep_winner_memory_without_locking_each_other(comparison):
    async def check():
        c=comparison
        try:
            record=await asyncio.wait_for(supervised.compare(c.ctx,c.branch),2)
            assert record['chosen_index']==1 and all(r['status']=='valid' for r in record['branches']), [(r['status'], r.get('error')) for r in record['branches']]
            assert c.calls==[0,1] and c.creation==['Q1~comparison0','Q1~comparison1']
            assert {(k.settings.cpu,k.settings.memory) for k in c.ctx.kernels._kernels.values()}=={('1','768Mi')}
            assert c.sessions['Q1~comparison0'].closed and not c.sessions['Q1~comparison1'].closed
            before=copy.deepcopy(c.db.reservations)
            repeated=await supervised.compare(c.ctx,c.branch)
            assert repeated is record and c.db.reservations==before and c.calls==[0,1]
            assert sum(s.fits for s in c.sessions.values())==2
        finally:await c.ctx.kernels.close()
        with edit_lock(c.ctx.workspace):pass
    asyncio.run(check())


def test_publication_third_kernel_does_not_refit_or_deadlock(comparison):
    async def check():
        c=comparison
        try:
            record=await asyncio.wait_for(supervised.compare(c.ctx,c.branch),2)
            report=await asyncio.wait_for(publish_comparison(c.ctx,c.branch,record,[]),2)
            assert report['status']=='completed'
            assert c.creation==['Q1~comparison0','Q1~comparison1','Q1']
            assert all(c.sessions[key].closed for key in ('Q1~comparison0','Q1~comparison1'))
            assert sum(s.fits for s in c.sessions.values())==2 and c.sessions['Q1'].fits==0
        finally:await c.ctx.kernels.close()
    asyncio.run(check())


def test_independent_pool_wait_is_bounded_and_submits_no_code(comparison):
    async def check():
        c=comparison;first=c.ctx.kernels.get('ordinary')
        other=KernelPool(c.ctx.workspace,c.ctx.fs,timeout=.02)
        try:
            await first.execute('ordinary step')
            entered=asyncio.Event();release=asyncio.Event()
            original=first.session.execute
            async def busy(*args):
                entered.set();await release.wait()
                return await original(*args)
            first.session.execute=busy
            active=asyncio.create_task(first.execute('second step'))
            await entered.wait()
            contender=other.get('other-pool')
            with pytest.raises(TimeoutError):await contender.execute('must not run')
            assert c.sessions['other-pool'].calls==[]
            release.set();await active
            assert len(c.sessions['ordinary'].calls)==2
            # An idle retained interpreter no longer blocks another pool.
            await contender.execute('independent idle access')
        finally:
            await other.close();await c.ctx.kernels.close()
    asyncio.run(check())


def test_build_and_experiment_style_files_never_change_source_version(tmp_path):
    root=tmp_path/'private/project-files/project';root.mkdir(parents=True)
    (root/'styles').mkdir();(root/'fonts').mkdir()
    (root/'paper.tex').write_text('\\documentclass{article}\n\\input{chapter}\n\\begin{document}text\\end{document}')
    (root/'chapter.tex').write_text('User chapter')
    (root/'styles/user.sty').write_text('user style')
    (root/'fonts/user.ttf').write_bytes(b'user font')
    nested=root/'chapters/experiments/user.sty';nested.parent.mkdir(parents=True);nested.write_text('a real user dependency')
    (root/'.hidden.sty').write_text('not a public source')
    before=paper_project.snapshot(root)
    (root/'.build').mkdir()
    with tempfile.TemporaryDirectory(dir=root/'.build',prefix='paper-project-') as name:
        staging=Path(name)
        for suffix in ('.sty','.cls','.bst','.cfg','.ttf','.otf'):
            (staging/('copied'+suffix)).write_bytes(b'copied build input')
            target=root/'experiments'/'attempt'/('generated'+suffix);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'experiment output')
        during=paper_project.snapshot(root)
        assert during['version']==before['version']
        assert {r['path'] for r in during['files']}=={'paper.tex','chapter.tex','styles/user.sty','fonts/user.ttf','chapters/experiments/user.sty'}
        (staging/'copied.sty').write_text('poll while the compiler still runs')
        assert paper_project.snapshot(root)['version']==before['version']
    assert paper_project.snapshot(root)['version']==before['version']
    (root/'styles/user.sty').write_text('actual user change')
    assert paper_project.snapshot(root)['version']!=before['version']


@pytest.mark.parametrize('path',['.build/main.tex','experiments/attempt/paper.tex','styles/../paper.tex','/etc/paper.tex'])
def test_explicit_source_dependencies_cannot_enter_internal_trees(tmp_path,path):
    root=tmp_path/'private/project-files/project';root.mkdir(parents=True)
    (root/'paper.tex').write_text('\\input{'+path+'}')
    with pytest.raises(ValueError):paper_project.files(root)


def test_single_file_limit_prevents_hash_and_atomic_archive_copy(tmp_path):
    fs=WorkspaceFS(tmp_path/'private/project-files/project');fs.init_structure();run=fs.create_run()
    source=fs.abs('outputs/large.csv');source.write_bytes(b'x'*65)
    with pytest.raises(FileLimitExceeded):output_hashes(fs,max_file_bytes=64)
    with pytest.raises(FileLimitExceeded):capture_local_outputs(fs,run,{},expected={'outputs/large.csv':hashlib.sha256(source.read_bytes()).hexdigest()},max_file_bytes=64)
    assert source.read_bytes()==b'x'*65
    assert not fs.abs(f'runs/{run}/artifacts/outputs/large.csv').exists()
    assert not list(fs.run_dir(run).rglob('.write-*'))
    source.write_bytes(b'a'*40);fs.write_text('outputs/other.csv','b'*40)
    assert len(output_hashes(fs,max_file_bytes=64))==2, 'no aggregate/project quota'


def test_closing_one_container_keeps_every_other_durable_lease(tmp_path):
    import time
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    from services.file_services.workspace_layout import private_root
    root=tmp_path/'private/project-files/project';root.mkdir(parents=True)
    path=private_root(root)/'sandbox-lease.json';path.parent.mkdir(parents=True)
    sessions=[]
    for key in ('first','second'):
        session=OpenSandboxSession.__new__(OpenSandboxSession)
        session.lease_path=path;session.lease_id=key
        session._save_lease({'sandbox_id':key,'expires_at':time.time()+60})
        sessions.append(session)
    assert set(json.loads(path.read_text())['leases'])=={'first','second'}
    sessions[0]._remove_lease()
    assert set(json.loads(path.read_text())['leases'])=={'second'}
    with edit_lock(root):pass
    from services.file_services.workspace_lock import execution_lock
    async def runtime_blocked():
        with pytest.raises(HTTPException,match='TTL'):
            async with execution_lock(root):pass
    asyncio.run(runtime_blocked())
    async def cannot_destroy():raise ConnectionError('destroy unconfirmed')
    sessions[1].host=SimpleNamespace(sandbox=SimpleNamespace(destroy=cannot_destroy),interpreter=None)
    async def close_unconfirmed():
        with pytest.raises(ConnectionError):await sessions[1].close()
    asyncio.run(close_unconfirmed())
    assert set(json.loads(path.read_text())['leases'])=={'second'}
    # Simulate a later confirmed destroy; only then can this lease disappear.
    sessions[1]._remove_lease()
    assert not path.exists()
    with edit_lock(root):pass


@pytest.mark.parametrize('mode',['input','output','error-log'])
def test_runtime_enforces_single_file_and_total_log_limits(tmp_path,mode):
    async def check():
        root=tmp_path/'private/project-files/project';state={'active':0}
        class ResourceSession(MemorySession):
            async def execute(self,code,run_id,output):
                self.calls.append(code)
                await output('stdout','head\n')
                if mode=='output':(self.remote/'outputs/large.csv').write_bytes(b'x'*65)
                return ExecutionOutcome(error='x'*200 if mode=='error-log' else None)
        session=ResourceSession(root,'resources',state)
        session.settings=SandboxSettings(provider='mock',max_file_bytes=64,max_log_bytes=32)
        kernel=RemoteKernel(root,session,session.settings)
        if mode=='input':kernel.fs.write_text('data/large.csv','x'*65)
        try:
            result=await kernel.execute('test')
            info=kernel.fs.get_run_info(result.run_id)
            logs=sum((kernel.fs.run_dir(result.run_id)/name).stat().st_size for name in ('stdout.log','stderr.log'))
            assert logs<=32
            if mode=='input':
                assert not result.ok and result.failure_kind=='resource' and not session.calls
                assert not list((kernel.fs.run_dir(result.run_id)/'inputs').rglob('large.csv'))
            elif mode=='output':
                assert result.ok and result.delivery_error and '单文件上限' in result.delivery_error
                assert not info.get('evidence_archived') and len(session.calls)==1
                assert not info['artifacts']
            else:
                assert not result.ok and result.failure_kind=='code'
                assert len(result.error.encode('utf-8'))<=32
        finally:await kernel.close()
    asyncio.run(check())


def test_pdf_version_check_and_publication_share_edit_lock(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from ai_system.export.latex import LatexResult
    from services.file_services.workspace_files import WorkspaceFileService
    root=tmp_path/'private/project-files/project';root.mkdir(parents=True)
    (root/'paper.tex').write_text('\\documentclass{article}\\begin{document}text\\end{document}')
    (root/'user.sty').write_text('user style')
    before=paper_project.snapshot(root)['version']
    @contextmanager
    def transaction():yield object()
    store=SimpleNamespace(root=root,research_id='mock',_transaction=transaction,_read=lambda _: {'feedback':[]})
    publication_phase={'pending':False,'checked':False}
    real_snapshot=paper_project.snapshot
    def checked_snapshot(workspace):
        if Path(workspace)==root and publication_phase['pending']:
            with pytest.raises(HTTPException):
                with edit_lock(root):pass
            publication_phase.update(pending=False,checked=True)
        return real_snapshot(workspace)
    monkeypatch.setattr(paper_project,'snapshot',checked_snapshot)
    async def fake_compile(staging,main,*,verified_snapshot=False):
        assert verified_snapshot
        # No outer edit lock while the real compiler would acquire execution_lock.
        with edit_lock(root):pass
        assert paper_project.snapshot(root)['version']==before
        (staging/'paper.pdf').write_bytes(b'mocked PDF')
        log=staging/'.build/latex/compile.log';log.parent.mkdir(parents=True);log.write_text('mocked completion')
        publication_phase['pending']=True
        return LatexResult(tex_path=main,pdf_path='paper.pdf',log_path='.build/latex/compile.log',exit_code=0)
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',fake_compile)
    from ai_system.sandbox.content_store import ContentStore
    original_copy=ContentStore.materialize
    checked=[]
    def copy(self,source,target,*args,**kwargs):
        if Path(target)==root/'paper.pdf':
            # A normal file API write cannot slip between version check and replace.
            with pytest.raises(HTTPException) as busy:
                WorkspaceFileService().write_file('project','paper.tex','racing edit')
            assert busy.value.status_code==409
            checked.append(True)
        return original_copy(self,source,target,*args,**kwargs)
    monkeypatch.setattr(ContentStore,'materialize',copy)
    result=asyncio.run(paper_project.compile_project(store))
    assert checked and publication_phase['checked'] and result['compile']['state']=='current'
    assert result['pdf_version']==before and paper_project.snapshot(root)['version']==before
    assert (root/'paper.pdf').read_bytes()==b'mocked PDF'
