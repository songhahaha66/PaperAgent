"""One bounded read surface for public files, retained runs and context archives."""
import asyncio
import fnmatch
import hashlib
import re
from copy import copy, deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from pydantic import Field
from ai_system.modules.registry import Inputs, CapabilityProduct


@dataclass(frozen=True)
class FileReadView:
    fs: object
    research: object
    names: tuple[str, ...]


def read_view(ctx):
    """Called on the owning event loop before handing any work to a thread.

    Copy nested bindings too: a shallow manifest copy still shares run/file rows.
    Worker threads never receive the live GraphContext or its mutable manifest.
    """
    fs=copy(ctx.fs)
    fs._manifest=deepcopy(ctx.fs._manifest)
    from types import SimpleNamespace
    frozen=SimpleNamespace(fs=fs,research=ctx.research)
    return FileReadView(fs,ctx.research,tuple(known_files(frozen)))


def known_files(ctx):
    if isinstance(ctx,FileReadView):return list(ctx.names)
    names={p for p,m in ctx.fs._manifest.get('files',{}).items() if m.get('visibility')=='user'}
    if ctx.fs.root==ctx.research.root:
        names.update(ctx.research.file_inputs())
        names.update(a['payload'].get('path') for a in ctx.research.snapshot()['artifacts'].values())
        names.update(p for p in ('paper.md','paper.tex','paper-sources.bib') if ctx.fs.exists(p))
    for info in ctx.fs._manifest.get('runs',{}).values():
        names.update(info.get(k) for k in ('input','stdout','stderr'))
    return sorted(p for p in names if p and not any(part.startswith('.') for part in PurePosixPath(p).parts))


def resolve(ctx, name):
    if name not in known_files(ctx) and not re.fullmatch(r'\.system/context-outputs/[a-f0-9]+\.json',name):
        raise ValueError('只能读取当前原始资料或已保存的工作区文件；请先research_list_files定位路径')
    path=ctx.fs.canonical_artifact(name) if name.startswith('outputs/research/') else ctx.fs.abs(name)
    if not path.is_file():raise ValueError('资料文件不可用：'+name)
    if path.stat().st_size>20_000_000:raise ValueError('文件超过20MB读取上限，请在计算工具中分块处理')
    return path


def text_file(path, suffix=None):
    raw=path.read_bytes()
    if len(raw)>20_000_000:raise ValueError('文件超过20MB读取上限')
    suffix=(suffix or path.suffix).lower()
    if suffix=='.pdf':
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text='\n\n'.join(f'第{i+1}页\n'+(p.extract_text() or '本页无可提取文本') for i,p in enumerate(pdf.pages))
    elif suffix in {'.xlsx','.xls'}:
        import pandas as pd
        with pd.ExcelFile(path) as book:
            text='\n\n'.join('工作表：'+s+'\n'+book.parse(s,nrows=40).to_string(index=False) for s in book.sheet_names)
        text+='\n（每张工作表仅预览前40行；完整计算请直接读取原文件。）'
    else:
        try:text=raw.decode('utf-8')
        except UnicodeError:raise ValueError('此二进制文件无法直接提取文本，请使用其真实格式读取')
    return hashlib.sha256(raw).hexdigest(),text


class ReadFile(Inputs):
    task_id: str | None = None
    path: str
    start: int = Field(default=0,ge=0)
    limit: int = Field(default=12000,ge=1,le=24000)


class ListFiles(Inputs):
    pattern: str = Field(default='*',max_length=500,description='工作区相对路径glob，如attachment/*或*.csv；不搜索沙箱根目录。')
    offset: int = Field(default=0,ge=0)
    limit: int = Field(default=30,ge=1,le=100)


class SearchFiles(ListFiles):
    query: str = Field(min_length=1,max_length=500,description='按字面文本搜索，不执行正则或代码。')


class MaterializeFile(Inputs):
    path: str
    destination: str = Field(description='计算用副本路径：outputs/intermediate/imported/...或code/imported/...')


async def materialize_artifact(ctx,inputs):
    from ai_system.sandbox.content_store import ContentStore
    from services.file_services.workspace_lock import edit_lock
    source=resolve(ctx,inputs['path']);name=inputs['destination']
    if not name.startswith(('outputs/intermediate/imported/','code/imported/')):
        raise ValueError('计算副本只能写入outputs/intermediate/imported/或code/imported/')
    target=ctx.fs.abs(name);content=ContentStore(ctx.workspace,20_000_000)
    version=await asyncio.to_thread(content.put,source)
    with edit_lock(ctx.workspace):
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=version:
            raise ValueError('该副本已有其他内容，请使用新路径')
        content.materialize(version,target)
        ctx.fs.register_file(name,kind='code' if name.endswith('.py') else 'data',created_by='ai',visibility='user')
        ctx.fs._manifest['files'][name]['origin']={'path':inputs['path'],'sha256':version}
        ctx.fs._save_manifest()
    await ctx.events.file_changed(name)
    return CapabilityProduct('research_state',{'path':name,'version':version,'source':inputs['path'],
        'runtime_available':'下次计算时按此路径同步，不会重跑产生原文件的计算。'})


async def read_artifact(ctx, inputs):
    view=read_view(ctx)
    path=await asyncio.to_thread(resolve,view,inputs['path'])
    version,text=await asyncio.to_thread(text_file,path)
    start=inputs['start'];end=start+inputs['limit']
    return CapabilityProduct('research_state',{'path':inputs['path'],'version':version,'total_chars':len(text),
        'excerpt':text[start:end],'next_start':end if end<len(text) else None,'read_tool':'research_read_artifact',
        'location':'工作区文件；Python计算沙箱不保证有此文件的副本'})


async def list_files(ctx, inputs):
    view=read_view(ctx)
    names=[p for p in view.names if fnmatch.fnmatchcase(p,inputs['pattern'])]
    rows=[]
    for name in names[inputs['offset']:inputs['offset']+inputs['limit']]:
        try:
            path=await asyncio.to_thread(resolve,view,name)
            version=await asyncio.to_thread(lambda:hashlib.sha256(path.read_bytes()).hexdigest())
            rows.append({'path':name,'size':path.stat().st_size,'version':version,
                         'source_run':(view.fs.get_file_meta(name) or {}).get('source_run'),'available':True})
        except (ValueError,FileNotFoundError) as exc:rows.append({'path':name,'available':False,'error':str(exc)})
    end=inputs['offset']+len(rows)
    return CapabilityProduct('research_state',{'files':rows,'total':len(names),'next_offset':end if end<len(names) else None,
        'read_tool':'research_read_artifact','location':'工作区文件列表，包含后端正文和已交付的计算结果；不要在Python里搜索正文位置'})


async def search_files(ctx, inputs):
    view=read_view(ctx)
    names=[p for p in view.names if fnmatch.fnmatchcase(p,inputs['pattern'])]
    matches=[];errors=[];end=inputs['offset']
    for name in names[inputs['offset']:inputs['offset']+inputs['limit']]:
        end+=1
        try:
            path=await asyncio.to_thread(resolve,view,name)
            version,text=await asyncio.to_thread(text_file,path)
        except (ValueError,FileNotFoundError) as exc:
            errors.append({'path':name,'error':str(exc)});continue
        pos=text.find(inputs['query'])
        if pos>=0:matches.append({'path':name,'version':version,'start':pos,'excerpt':text[max(0,pos-120):pos+480]})
    return CapabilityProduct('research_state',{'matches':matches,'unreadable':errors,'total_files':len(names),
        'next_offset':end if end<len(names) else None,'read_tool':'research_read_artifact'})


def register(registry):
    registry.register('research','research.materialize_artifact','research_state',materialize_artifact,MaterializeFile,
        model_callable=True,purpose='将已有归档结果按固定版本复制为下一次Python可读取的输入，不重算，不覆盖原始资料或其他版本。')
    for name,handler,schema,purpose in (
        ('read_artifact',read_artifact,ReadFile,'分页读取工作区原始资料、正文、代码、结果和运行日志，返回内容与版本；不需要进入Python或重新计算。'),
        ('list_files',list_files,ListFiles,'按glob分页列出工作区文件、版本和可用性；正文也在这里，Python沙箱不是文件列表的来源。'),
        ('search_files',search_files,SearchFiles,'按字面文本搜索工作区文件，返回匹配片段、版本与读取位置；pattern限定目录，offset继续下一页。')):
        registry.register('research','research.'+name,'research_state',handler,schema,model_callable=True,purpose=purpose,access='read')
