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
    def __init__(self, root, key, state):
        super().__init__(root)
        self.calls=[];self.state=state
    async def execute(self, code, run_id, output):
        assert self.state['active']==0
        self.state['active']+=1
        try:
            with edit_lock(self.backend_root):pass
            self.calls.append(code)
            await output('stdout','2.0\n')
            return ExecutionOutcome()
        finally:self.state['active']-=1
    async def finalize(self,run_id):pass
    async def close(self):pass
    async def interrupt(self):pass


@pytest.fixture
def computation(tmp_path,monkeypatch):
    root=tmp_path/'private/project-files/project';root.mkdir(parents=True)
    sessions={};state={'active':0}
    def factory(workspace,*,timeout,fs,experiment_id,execution_scope,**kwargs):
        session=MemorySession(Path(workspace),experiment_id,state);sessions[experiment_id]=session
        return RemoteKernel(workspace,session,SandboxSettings(provider='mock'),timeout=timeout,fs=fs,execution_scope=execution_scope)
    monkeypatch.setattr('ai_system.graph.context.create_kernel',factory)
    return SimpleNamespace(ctx=GraphContext.create(root,planner_llm=None),sessions=sessions)


def test_independent_pool_wait_is_bounded_and_submits_no_code(computation):
    async def check():
        c=computation;first=c.ctx.kernels.get('ordinary')
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
