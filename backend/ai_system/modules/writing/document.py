"""Version-bound document assets and actual PDF inspection, without another writer."""
import asyncio
import base64
import hashlib
import io
import json
import re
import threading
from contextlib import closing
from pathlib import Path

from pydantic import Field
from ..registry import Inputs, CapabilityProduct
from services.chat_services import paper_project as project
from services.file_services.workspace_layout import workspace_path
from services.file_services.workspace_lock import edit_lock
from ai_system.sandbox.content_store import ContentStore

PDF_LOCK = threading.Lock()  # PDFium's native API is not thread-safe.
ASSETS = '.system/paper-project/assets.json'


class AssetInput(Inputs):
    source: str = Field(description='research_list_files返回的实际结果路径。')
    destination: str = Field(description='论文内使用的新路径，如figures/forecast.png；不得覆盖已有不同文件。')


class PreviewInput(Inputs):
    source_version: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$',description='省略时编译当前正文；指定时只读已编译的历史版本。')
    pdf_sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$',description='复核子任务传入固定PDF哈希，保证复核同一产物。')
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


def render_pages(root, version, pdf_hash, pages):
    import pypdfium2 as pdfium
    archive=workspace_path(root,project.VERSIONS)/version
    if pdf_hash is None:pdf_hash=json.loads((archive/'build.json').read_text())['pdf_sha256']
    build=json.loads((archive/'builds'/f'{pdf_hash}.json').read_text())
    if build['source_version']!=version:raise ValueError('PDF与源码版本不匹配')
    content=ContentStore(root,20_000_000)
    if not content.has(pdf_hash):raise ValueError('该PDF版本不可用')
    pdf=content.path(pdf_hash)
    images=[];texts=[]
    with PDF_LOCK:
        with pdfium.PdfDocument(pdf) as document:
            if any(p<1 or p>len(document) for p in pages):raise ValueError('PDF页码超出范围，总页数：'+str(len(document)))
            for number in dict.fromkeys(pages):
                name=f'.system/paper-project/previews/{pdf_hash}/{number}.png'
                target=workspace_path(root,name)
                with closing(document[number-1]) as page:
                    with closing(page.get_textpage()) as textpage:texts.append({'page':number,'text':textpage.get_text_range()[:12000]})
                    if not target.exists():
                        width,height=page.get_size()
                        if width<=0 or height<=0:raise ValueError('PDF页面尺寸无效')
                        scale=min(1.5,1600/max(width,height))
                        bitmap=page.render(scale=scale)
                        try:
                            image=bitmap.to_pil();buffer=io.BytesIO();image.save(buffer,format='PNG')
                        finally:bitmap.close()
                        from services.file_services.file_helper import atomic_write
                        atomic_write(target,lambda stream:stream.write(buffer.getvalue()))
                images.append({'page':number,'path':name})
            total=len(document)
    return {'source_version':version,'pdf_sha256':pdf_hash,'total_pages':total,'pages':texts,
            'warnings':build.get('warnings',[]),'diagnostics':build.get('diagnostics',[]),'_images':images}


async def preview(ctx, inputs):
    version=inputs['source_version']
    if version is None:
        current=await asyncio.to_thread(project.snapshot,ctx.workspace)
        if not current['main'].endswith('.tex'):raise ValueError('当前实际PDF预览支持TeX项目；Markdown请先按项目输出格式导出')
        data=project.meta(ctx.workspace)
        if data.get('pdf_version')!=current['version'] or not data.get('pdf_sha256') or data.get('compile',{}).get('state')!='current':
            data=await project.compile_project(ctx.research)
        if not data.get('compile',{}).get('success') or data.get('pdf_version')!=current['version']:
            return CapabilityProduct('paper',{'status':'failed','execution_status':'failed','compile':data.get('compile',{})},'failed','当前稿件尚未编译成功')
        version=current['version']
        await ctx.events.file_changed('paper.pdf')
    result=await asyncio.to_thread(render_pages,ctx.workspace,version,inputs['pdf_sha256'],inputs['pages'])
    result['current']=project.snapshot(ctx.workspace)['version']==version and project.meta(ctx.workspace).get('pdf_sha256')==result['pdf_sha256']
    result['instruction']='此回执含实际PDF页面。逐页检查图表、截断、缺项及结论；编译成功不表示论文质量通过。可继续读取其余页，或按需research_delegate purpose=document_review复核固定版本。'
    return CapabilityProduct('paper',result)


def tool_content(ctx, value, llm):
    """Keep only image locators in operation receipts; hydrate at model boundary."""
    if not isinstance(value,dict) or not value.get('_images'):return json.dumps(value,ensure_ascii=False,default=str)
    public={k:v for k,v in value.items() if k!='_images'}
    if not (getattr(llm,'profile',None) or {}).get('image_inputs'):
        public['visual_review']='当前模型未声明图像输入能力，仅返回实际PDF文本；不能声称已检查图像和排版。'
        return json.dumps(public,ensure_ascii=False,default=str)
    blocks=[{'type':'text','text':json.dumps(public,ensure_ascii=False,default=str)}]
    for row in value['_images']:
        if not re.fullmatch(r'\.system/paper-project/previews/[a-f0-9]{64}/[0-9]+\.png',row['path']):raise ValueError('PDF预览路径无效')
        data=workspace_path(ctx.workspace,row['path']).read_bytes()
        blocks.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode()}})
    return blocks


def register(registry):
    registry.register('writing','writing.use_asset','paper',use_asset,AssetInput,model_callable=True,
        purpose='把选定结果复制为固定版本的论文图片、表格或代码依赖，返回实际引用路径；保留来源，不覆盖旧版本。')
    registry.register('writing','writing.preview','paper',preview,PreviewInput,model_callable=True,
        purpose='编译当前TeX项目并查看真正的PDF页面、文本和排版警告；source_version指定历史版本时只读，不改正文。')
