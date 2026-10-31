"""Pure local contracts: no model, network, compiler or real sandbox is started."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncio
import io
import json
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.file_services.workspace_layout import workspace_path, private_root, evidence_root, safe_join
from services.file_services.file_helper import FileHelper, atomic_write, open_read
from services.file_services.workspace_fs import WorkspaceFS
from services.file_services.workspace_files import WorkspaceFileService
from services.file_services.workspace_lock import execution_lock, edit_lock
from services.file_services.archive_safety import validate_archive
from services.file_services.project_access import require_current
from ai_system.sandbox.evidence import freeze_inputs, seal_run, verify_run
from ai_system.sandbox.artifacts import capture_local_outputs, output_hashes
from ai_system.sandbox.factory import create_kernel
from ai_system.sandbox.settings import SandboxSettings
from schemas.schemas import PaperTemplateUpdate
from models.models import Base, User, PaperTemplate, Project
from services.data_services.crud import force_delete_paper_template, create_work
from schemas.schemas import WorkCreate


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('PA_DATA_PATH', str(tmp_path/'private'))
    monkeypatch.delenv('PAPERAGENT_SANDBOX_CONFIG', raising=False)


def test_path_rejection_is_reported_as_http_error(tmp_path):
    with pytest.raises(HTTPException) as error:
        FileHelper(tmp_path).resolve('../x')
    assert error.value.status_code == 400


def test_links_are_rejected_even_inside_tree(tmp_path):
    (tmp_path/'real').mkdir();(tmp_path/'link').symlink_to(tmp_path/'real', target_is_directory=True)
    with pytest.raises(HTTPException):FileHelper(tmp_path).write_text('link/x','bad')
    with pytest.raises(ValueError):safe_join(tmp_path/'link','x')


def test_failed_stream_keeps_original_and_cleans_temporary(tmp_path):
    helper=FileHelper(tmp_path);helper.write_text('data.csv','old')
    with pytest.raises(HTTPException):helper.write_bytes_stream('data.csv',io.BytesIO(b'large'),max_bytes=2)
    assert helper.read_text('data.csv')=='old'
    assert sorted(p.name for p in tmp_path.iterdir())==['data.csv']


def test_parent_link_swap_during_write_cannot_escape(tmp_path):
    root=tmp_path/'root';outside=tmp_path/'outside';root.mkdir();outside.mkdir()
    parent=root/'data';parent.mkdir()
    def writer(stream):
        parent.rename(root/'old-data');parent.symlink_to(outside,target_is_directory=True)
        stream.write(b'safe')
    atomic_write(parent/'value.csv',writer)
    assert not (outside/'value.csv').exists()
    assert (root/'old-data/value.csv').read_bytes()==b'safe'


def test_state_and_receipts_are_outside_public_tree(tmp_path):
    root=tmp_path/'private/project-files'/'project';root.mkdir(parents=True)
    fs=WorkspaceFS(root);fs.init_structure();run=fs.create_run()
    fs.save_run_input(run,'print(1)')
    assert not fs.run_dir(run).is_relative_to(root)
    assert fs.run_dir(run).is_relative_to(evidence_root(root))
    assert workspace_path(root,'.system/research.sqlite').is_relative_to(private_root(root))
    assert not (root/'manifest.json').exists()
    nested=root/'experiments'/'a'/'workspace'
    assert workspace_path(root,'experiments/a/workspace/runs/r/input.py')==workspace_path(nested,'runs/r/input.py')
    assert fs.rel(fs.run_dir(run)/'input.py')==f'runs/{run}/input.py'


def test_corrupt_manifest_is_not_reset(tmp_path):
    root=tmp_path/'private/project-files'/'project';fs=WorkspaceFS(root);fs.init_structure();fs._save_manifest()
    manifest=workspace_path(root,'manifest.json');manifest.write_text('{')
    with pytest.raises(ValueError,match='损坏'):WorkspaceFS(root)
    assert manifest.read_text()=='{'


def test_receipt_is_required_idempotent_and_tamper_evident(tmp_path):
    root=tmp_path/'private/project-files'/'project';fs=WorkspaceFS(root);fs.init_structure()
    fs.write_text('data/input.csv','x\n1\n');run=fs.create_run()
    fs.save_run_input(run,'print(1)');fs.save_run_stdout(run,'1\n');fs.save_run_stderr(run,'')
    freeze_inputs(fs,run);before=output_hashes(fs)
    fs.write_text('outputs/result.csv','y\n2\n');capture_local_outputs(fs,run,before)
    fs.finish_run(run,'success')
    with pytest.raises(ValueError,match='未完整归档'):verify_run(fs,run)
    seal_run(fs,run);seal_run(fs,run);verify_run(fs,run)
    fs.write_text('data/input.csv','changed')
    assert (fs.run_dir(run)/'inputs/data/input.csv').read_text()=='x\n1\n'
    (fs.run_dir(run)/'stdout.log').write_text('changed')
    with pytest.raises(ValueError,match='校验'):verify_run(fs,run)


def test_runtime_lock_does_not_block_document_edits(tmp_path):
    async def check():
        root=tmp_path/'private/project-files'/'project'
        async with execution_lock(root):
            with edit_lock(root):pass
        with edit_lock(root):pass
    asyncio.run(check())


def test_uncertain_container_lease_blocks_runtime_access_only(tmp_path):
    import time
    root=tmp_path/'private/project-files'/'project';lease=private_root(root)/'sandbox-lease.json';lease.parent.mkdir(parents=True)
    lease.write_text(json.dumps({'expires_at':time.time()+60,'sandbox_id':'uncertain'}))
    with edit_lock(root):pass
    async def check():
        with pytest.raises(HTTPException,match='TTL'):
            async with execution_lock(root):pass
    asyncio.run(check())


@pytest.mark.parametrize('name', ['../escape','/absolute','a/../escape','a\\escape'])
def test_archive_rejects_unsafe_members(tmp_path,name):
    path=tmp_path/'input.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr(name,b'x')
    with pytest.raises(ValueError):validate_archive(path)


def test_archive_rejects_links_and_bombs(tmp_path):
    path=tmp_path/'input.zip';member=zipfile.ZipInfo('link');member.create_system=3;member.external_attr=(stat.S_IFLNK|0o777)<<16
    with zipfile.ZipFile(path,'w') as z:z.writestr(member,'/etc/passwd')
    with pytest.raises(ValueError):validate_archive(path)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('bomb',b'0'*1000000)
    with pytest.raises(ValueError):validate_archive(path)


def test_legacy_and_local_execution_are_closed(tmp_path):
    with pytest.raises(HTTPException):require_current(SimpleNamespace(architecture_version=1))
    with pytest.raises(HTTPException):require_current(SimpleNamespace())
    require_current(SimpleNamespace(architecture_version=2))
    with pytest.raises(ValueError):create_kernel(tmp_path,settings=SandboxSettings(provider='local'))
    with pytest.raises(ValueError):PaperTemplateUpdate(file_path='/private/file')


def test_export_allowlist_omits_databases_credentials_and_checkpoints(tmp_path):
    root=tmp_path/'private'/'workspaces'/'old';root.mkdir(parents=True)
    for name in ('paper.md','code/main.py','attachment/data.csv','.system/graph.sqlite','.env','owner.json','runs/run/input.py'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
    service=WorkspaceFileService();target=Path(service.export_workspace('old',legacy=True))
    try:
        with zipfile.ZipFile(target) as archive:
            names=archive.namelist()
            assert 'paper.md' in names and 'code/main.py' in names and '研究记录.md' in names
            assert any(n.endswith('run/input.py') for n in names)
            assert not any(n.endswith(('.sqlite','.env','owner.json')) for n in names)
    finally:
        import shutil
        shutil.rmtree(target.parent)


def test_template_delete_detaches_projects_without_deleting_them(tmp_path,monkeypatch):
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr('services.file_services.template_files.template_file_service.delete_file',lambda _: True)
    with Session(engine) as db:
        db.add_all([User(id=1,username='a',email='a@x',password_hash='x'),User(id=2,username='b',email='b@x',password_hash='x')]);db.flush()
        template=PaperTemplate(id=1,name='private',file_path='template.md',created_by=1,output_format='markdown');db.add(template);db.flush()
        db.add_all([Project(work_id='a',title='a',created_by=1,template_id=1),Project(work_id='b',title='b',created_by=2,template_id=1)]);db.commit()
        with pytest.raises(HTTPException) as denied:create_work(db,WorkCreate(title='new',template_id=1),2)
        assert denied.value.status_code==403
        force_delete_paper_template(db,1,1)
        assert db.query(Project).count()==2
        assert all(p.template_id is None for p in db.query(Project))


def test_sdk_mounts_only_server_local_execution_cache(tmp_path,monkeypatch):
    from ai_system.sandbox.opensandbox_provider import OpenSandboxSession
    from opensandbox import Sandbox
    from code_interpreter import CodeInterpreter
    root=tmp_path/'private/project-files'/'project';root.mkdir(parents=True)
    calls={}
    async def nothing(*args,**kwargs):pass
    async def create(*args,**kwargs):
        calls.update(kwargs)
        async def read(path):raise AssertionError('shared-filesystem probe must not exist')
        return SimpleNamespace(id='fake-only',files=SimpleNamespace(write_file=nothing,read_file=read,create_directories=nothing),destroy=nothing)
    async def interpreter(*args,**kwargs):
        async def context(*args):return SimpleNamespace(id='context')
        async def ready(*args,**kwargs):return SimpleNamespace(error=None,complete=True)
        return SimpleNamespace(codes=SimpleNamespace(create_context=context,run=ready),aclose=nothing)
    monkeypatch.setattr(Sandbox,'create',create);monkeypatch.setattr(CodeInterpreter,'create',interpreter)
    settings=SandboxSettings(api_key='fake-test-key',host_workspace_root=str(tmp_path/'server-files'))
    async def check():
        session=OpenSandboxSession(settings,workspace_dir=root)
        await session.start()
        mounts=calls['volumes']
        assert len(mounts)==1
        assert mounts[0].host.path==str(tmp_path/'server-files'/session.cache_key)
        assert mounts[0].mount_path=='/workspace' and not mounts[0].read_only
        assert not any(str(private_root(root)) in m.host.path for m in mounts)
        assert calls['network_policy'].default_action=='deny'
        assert 'DATABASE_URL' not in calls['env'] and 'fake-test-key' not in calls['env'].values()
        await session.close()
        assert not session.lease_path.exists()
    asyncio.run(check())


from helpers.remote_files import RemoteFiles


class PersistentFake(RemoteFiles):
    """Controlled remote writer; it cannot read the backend filesystem."""
    def __init__(self,path,*,wait=False,finalize_failure=False):
        super().__init__(path)
        self.wait=wait;self.ready=asyncio.Event();self.finalize_failure=finalize_failure;self.interrupted=False
    async def execute(self,code,run_id,output):
        from ai_system.sandbox.contracts import ExecutionOutcome
        self.calls.append(code)
        await output('stdout',code+'\n')
        (self.remote/'outputs').mkdir(exist_ok=True)
        (self.remote/'outputs/result.csv').write_text(code)
        self.ready.set()
        if self.wait:await asyncio.Event().wait()
        return ExecutionOutcome()
    async def finalize(self,run_id):
        if self.finalize_failure:raise RuntimeError('figure save unavailable')


def test_mock_cancel_never_creates_usable_evidence(tmp_path):
    from ai_system.sandbox.remote_kernel import RemoteKernel
    async def check():
        root=tmp_path/'private/project-files'/'project';session=PersistentFake(root,wait=True)
        kernel=RemoteKernel(root,session,SandboxSettings(provider='fake'))
        task=asyncio.create_task(kernel.execute('unknown'))
        await session.ready.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        info=next(iter(kernel.fs._manifest['runs'].values()))
        assert info['status']=='cancelled' and not info.get('evidence_archived')
        assert session.interrupted and session.closed
        await kernel.close()
    asyncio.run(check())


def test_mock_lost_memory_does_not_fake_figure_recovery(tmp_path):
    from ai_system.sandbox.remote_kernel import RemoteKernel
    async def check():
        root=tmp_path/'private/project-files'/'project';session=PersistentFake(root,finalize_failure=True)
        kernel=RemoteKernel(root,session,SandboxSettings(provider='fake'))
        first=await kernel.execute('3')
        assert first.ok and first.delivery_error and not kernel.fs.get_run_info(first.run_id).get('evidence_archived')
        await kernel.close()
        replacement=PersistentFake(root)
        rebuilt=RemoteKernel(root,replacement,SandboxSettings(provider='fake'))
        with pytest.raises(RuntimeError,match='内存'):await rebuilt.retry_outputs()
        assert not replacement.calls
        await rebuilt.close()
    asyncio.run(check())


@pytest.mark.parametrize('version,path,expected',[(1,'/api/works/p',200),(1,'/api/workspace/p/export',200),(1,'/api/workspace/p/files',409),(2,'/api/workspace/p/files',200)])
def test_application_wide_version_gate(tmp_path,monkeypatch,version,path,expected):
    from fastapi import FastAPI,Depends
    from fastapi.testclient import TestClient
    import routers
    from database.database import get_db
    monkeypatch.setattr(routers,'verify_token',lambda _:7)
    monkeypatch.setattr('services.data_services.crud.get_work',lambda *args:SimpleNamespace(created_by=7,architecture_version=version))
    app=FastAPI()
    @app.get('/api/works/{work_id}',dependencies=[Depends(routers.project_route_access)])
    @app.get('/api/workspace/{work_id}/export',dependencies=[Depends(routers.project_route_access)])
    @app.get('/api/workspace/{work_id}/files',dependencies=[Depends(routers.project_route_access)])
    def endpoint(work_id):return {'id':work_id}
    app.dependency_overrides[get_db]=lambda:SimpleNamespace(close=lambda:None)
    with TestClient(app) as client:
        assert client.get(path,headers={'Authorization':'Bearer unit-test'}).status_code==expected


def test_word_template_export_creates_scratch_before_pandoc(tmp_path,monkeypatch):
    import ai_system.export.docx as module
    root=tmp_path/'private/project-files'/'project';template=root/'template/original.docx';template.parent.mkdir(parents=True);template.write_bytes(b'template')
    async def fake_pandoc(markdown,cwd,args):
        assert (cwd/'.build').is_dir()
        assert f'--reference-doc={template}' in args
        (cwd/'.build/export.docx').write_bytes(b'new-docx')
    monkeypatch.setattr(module,'pandoc',fake_pandoc)
    result=asyncio.run(module.export_docx.__wrapped__(root,'# Title',template_docx=template))
    assert result.reference=='template' and (root/'paper.docx').read_bytes()==b'new-docx'
