"""Document assets and inspection of the current compiled PDF."""
import asyncio
import hashlib
import json
from pathlib import Path

from pydantic import Field
from ..registry import Inputs, CapabilityProduct
from services.chat_services import paper_project as project
from services.file_services.workspace_layout import workspace_path
from services.file_services.workspace_lock import edit_lock
from ai_system.sandbox.content_store import ContentStore

ASSETS = '.system/paper-project/assets.json'


class AssetInput(Inputs):
    source: str = Field(description='research_list_files返回的实际结果路径。')
    destination: str = Field(description='论文内使用的新路径，如figures/forecast.png；不得覆盖已有不同文件。')


class ReferenceEdit(Inputs):
    reference: int = Field(ge=0, description='本轮论文引用的reference编号')
    replacement: str = Field(max_length=100000)


class ReferenceEdits(Inputs):
    edits: list[ReferenceEdit] = Field(min_length=1, max_length=24)


async def propose_edits(ctx, inputs):
    turn = ctx.research.snapshot().get('conversation_input') or {}
    context = turn.get('paper_context')
    if not context:
        raise ValueError('本轮没有论文引用，请先选择需要修改的源码')
    project.initialize(ctx.research)
    patches=[]
    for edit in inputs['edits']:
        if edit['reference'] >= len(context['references']):
            raise ValueError('reference不属于本轮引用')
        selection=context['references'][edit['reference']].get('selection')
        if not selection:
            raise ValueError('阅读引用没有确认的源码范围，不能猜测位置；请用户选择源码')
        project.current_selection(ctx.workspace,selection)
        patches.append({**selection,'replacement':edit['replacement']})
    change=project.propose_change(ctx.research,context.get('thread_id') or 'chat',turn['id'],patches)
    return CapabilityProduct('paper',{'change_id':change['id'],'status':'pending',
        'message':'修改集已提出，等待用户接受；论文尚未修改'})


class PreviewInput(Inputs):
    pages: list[int] = Field(default_factory=lambda:[1],min_length=1,max_length=4,description='实际PDF页码，从1开始，每次最多4页。')


async def use_asset(ctx, inputs):
    from ai_system.research.files import resolve
    from services.file_services.workspace_layout import safe_join
    source=resolve(ctx,inputs['source'])
    name=inputs['destination']
    target=safe_join(ctx.workspace,name)
    if Path(name).parts[0] not in {'figures','tables','paper-assets'}:
        raise ValueError('论文依赖请放入figures/、tables/或paper-assets/')
    if Path(name).suffix.lower() not in project.ASSET_SUFFIXES | {'.csv','.json','.py','.tex'}:
        raise ValueError('不支持此类论文依赖')
    store=ContentStore(ctx.workspace,20_000_000)
    version=await asyncio.to_thread(store.put,source)
    with edit_lock(ctx.workspace):
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=version:
            raise ValueError('目标已有不同内容，请选择新路径；旧论文依赖已保留')
        store.materialize(version,target)
        path=workspace_path(ctx.workspace,ASSETS)
        bindings=json.loads(path.read_text()) if path.exists() else {}
        bindings[name]={'source':inputs['source'],'sha256':version,'size':target.stat().st_size}
        project.atomic_text(path,json.dumps(bindings,ensure_ascii=False))
    ctx.fs.register_file(name,kind='document',created_by='ai',visibility='user')
    await ctx.events.file_changed(name)
    return CapabilityProduct('paper',{'path':name,**bindings[name],
        'instruction':'在正文引用该实际路径；已固定字节版本，不会随新的计算结果自动替换。'})


def render_pages(root, pages):
    from ai_system.graph.pdf_pages import render_pdf_pages
    data=project.meta(root);build=data['compile']
    result=render_pdf_pages(root,Path(root)/'paper.pdf',data['pdf_sha256'],pages)
    result.pop('pdf_sha256')
    return {'path':'paper.pdf',**result,
            'warnings':build.get('warnings',[]),'diagnostics':build.get('diagnostics',[])}


async def preview(ctx, inputs):
    from ai_system.agent.runtime import tool_metadata
    data=await project.compile_project(ctx.research,on_compile=lambda:tool_metadata(ctx,preview_stage='compiling',compiled=True))
    compiled=data['compiled']
    compilation={key:value for key,value in data['compile'].items() if key!='source_version'}
    if not compilation['success']:
        return CapabilityProduct('paper',{'status':'failed','execution_status':'failed','compile':compilation,
            'compiled':compiled,'warnings':compilation['warnings']},'failed','当前稿件尚未编译成功')
    await ctx.events.file_changed('paper.pdf')
    tool_metadata(ctx,preview_stage='reading',compiled=compiled)
    result=await asyncio.to_thread(render_pages,ctx.workspace,inputs['pages'])
    result['compiled']=compiled
    result['current']=True
    result['instruction']='先检查相关页，再用writing_edit的edits集中修订；修改后重新预览当前稿件。以本次visual_pages为实际附图范围，未附图时仅检查文本。核对图表、截断、缺项及结论；可按需只读委派复核当前稿件。'
    return CapabilityProduct('paper',result)


def tool_content(ctx, value, llm):
    """Durable tool messages contain refs only; request middleware hydrates them."""
    return json.dumps(value,ensure_ascii=False,default=str)


def register(registry):
    registry.register('writing','writing.propose_edits','paper',propose_edits,ReferenceEdits,model_callable=True,
        purpose='用户要求修改本轮论文引用时，按reference编号提出版本化修改集，等待用户接受。无确认源码范围时不能猜测位置。')
    registry.register('writing','writing.use_asset','paper',use_asset,AssetInput,model_callable=True,
        purpose='把选定结果复制为固定版本的论文图片、表格或代码依赖，返回实际引用路径；保留来源，不覆盖旧版本。')
    registry.register('writing','writing.preview','paper',preview,PreviewInput,model_callable=True,
        purpose='编译或复用当前TeX项目的PDF，查看实际页面、文本和排版警告；修改正文后重新预览。')
