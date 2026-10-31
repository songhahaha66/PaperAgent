"""Durable page references, route-aware metering and request-only image bytes.

Refs remain in checkpoints. Only the disposable model-input copy contains
base64, following the attachment/projection boundary used by DeepSeek Harness.
"""
import base64
from dataclasses import dataclass
import hashlib
import json
import math
import re
from urllib.parse import urlparse

from langchain_core.messages import ToolMessage, HumanMessage
from services.file_services.workspace_layout import workspace_path
from ..config.generation import ModelContextLimit

PAGE_PATH=re.compile(r'\.system/paper-project/previews/[a-f0-9]{64}/[1-9][0-9]*\.png')


@dataclass(frozen=True)
class VisionPolicy:
    enabled: bool
    strategy: str
    upper_bound: int | None = None
    # Deployment bound, also below DeepSeek's 48 MiB inline request limit.
    max_inline_bytes: int = 32*1024*1024

    def tokens(self,width,height):
        if self.upper_bound is not None:return self.upper_bound
        if self.strategy=='deepseek':return 1024  # published per-image upper bound
        if self.strategy in {'openai-tiles','openai-mini-tiles'}:
            scale=min(1,2048/max(width,height),768/min(width,height))
            tiles=math.ceil(width*scale/512)*math.ceil(height*scale/512)
            return (2833+5667*tiles) if self.strategy=='openai-mini-tiles' else (85+170*tiles)
        if self.strategy.startswith('openai-patches'):
            cap=6144 if self.strategy.endswith('6144') else 2500
            multiplier=1.62 if self.strategy=='openai-patches-mini-6144' else 1.2
            count=math.ceil(width/32)*math.ceil(height/32)
            if count>cap:
                scale=math.sqrt(cap*32*32/(width*height))
                w,h=width*scale,height*scale
                scale*=min(math.floor(w/32)/(w/32),math.floor(h/32)/(h/32))
                count=math.ceil(math.floor(width*scale)/32)*math.ceil(math.floor(height*scale)/32)
            return math.ceil(count*multiplier)
        if self.strategy=='claude':return math.ceil(width/28)*math.ceil(height/28)
        if self.strategy=='gemini':return 1120  # Gemini 3/4 high resolution
        raise ModelContextLimit('该模型尚未配置图片计量；请设置 image_tokens_per_image 上界后查看图片。')


def vision_policy(llm):
    profile=getattr(llm,'profile',None) or {}
    settings=(getattr(llm,'metadata',None) or {}).get('generation_settings',{})
    declared=settings.get('image_inputs')
    if declared is None:declared=profile.get('image_inputs')
    bound=settings.get('image_tokens_per_image') or profile.get('image_tokens_per_image')
    if bound is not None and (not isinstance(bound,int) or isinstance(bound,bool) or bound<=0):
        raise ValueError('image_tokens_per_image 必须为正整数')
    name=str(getattr(llm,'model_name',None) or getattr(llm,'model',None) or '').removeprefix('models/').lower()
    base=getattr(llm,'openai_api_base',None) or getattr(llm,'base_url',None)
    host=urlparse(str(base)).hostname if base else None
    strategy='unknown';known=False
    if host=='api.deepseek.com':
        known=name in {'deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp'}
        strategy='deepseek'
        # The SDK's OpenAI catalog may predate DeepSeek's native vision. Only
        # explicit connection settings override the official route declaration.
        declared=settings.get('image_inputs')
    elif name.startswith(('gpt-','chatgpt-')) and host in {None,'api.openai.com'}:
        if name.startswith('gpt-4o-mini'):strategy='openai-mini-tiles'
        elif name.startswith(('gpt-4o','gpt-4.1')) and not name.startswith(('gpt-4.1-mini','gpt-4.1-nano')):strategy='openai-tiles'
        elif name.startswith('gpt-4.1-mini'):strategy='openai-patches-mini-6144'
        elif name.startswith('gpt-5.2'):strategy='openai-patches-6144'
        elif any(name==family or re.fullmatch(re.escape(family)+r'-\d{4}-\d{2}-\d{2}',name)
                 for family in ('gpt-6-astra','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna','gpt-5.5','gpt-5.4','gpt-5.4-mini','gpt-5.4-nano')):
            strategy='openai-patches'
        known=strategy!='unknown'
    elif name.startswith('claude-'):
        strategy='claude';known=True
    elif name.startswith(('gemini-3','gemini-4')):
        strategy='gemini';known=True
    enabled=bool(known if declared is None else declared)
    return VisionPolicy(enabled,strategy,bound)


def page_ref(root,row):
    """Validate/hash bounded local bytes once at the tool boundary."""
    from PIL import Image
    from io import BytesIO
    name=row['path']
    if not PAGE_PATH.fullmatch(name):raise ValueError('PDF预览路径无效')
    path=workspace_path(root,name)
    with path.open('rb') as stream:raw=stream.read(20_000_001)
    if len(raw)>20_000_000:raise ValueError('PDF预览图片超过20MB')
    with Image.open(BytesIO(raw)) as image:
        width,height=image.size
        if image.format!='PNG' or not 0<width<=1600 or not 0<height<=1600:raise ValueError('PDF预览图片尺寸或格式无效')
    return {**row,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'width':width,'height':height,'detail':'high'}


def image_value(message):
    if not isinstance(message,ToolMessage) or not isinstance(message.content,str):return None
    try:value=json.loads(message.content)
    except (ValueError,TypeError):return None
    return value if isinstance(value,dict) and isinstance(value.get('_images'),list) else None


class ImageProjection:
    def __init__(self,root,llm,omitted):
        self.root,self.policy,self.omitted=root,vision_policy(llm),omitted
        self._native_costs={}

    def native_tokens(self,block):
        """Price old native image blocks without counting their base64 as text."""
        from io import BytesIO
        from PIL import Image
        url=(block.get('image_url') or {}).get('url','') if block.get('type')=='image_url' else ''
        if not url:
            source=block.get('source') or {}
            if source.get('type')=='base64':url='data:image/unknown;base64,'+source.get('data','')
        if not url.startswith('data:image/') or ';base64,' not in url:
            raise ModelContextLimit('图片缺少可计量的本地尺寸；请通过图片读取工具提供固定版本。')
        key=hashlib.sha256(url.encode()).hexdigest()
        if key not in self._native_costs:
            raw=base64.b64decode(url.split(';base64,',1)[1],validate=True)
            if len(raw)>20_000_000:raise ModelContextLimit('图片超过20MB，请缩小图片后读取。')
            with Image.open(BytesIO(raw)) as image:self._native_costs[key]=self.policy.tokens(*image.size)
        return self._native_costs[key]

    def view(self,messages,*,max_tokens):
        """Omit oldest occurrences when image-only/token or byte budgets require it.

        Decisions are stored with the context checkpoint, so an old image never
        silently reappears. A new preview call remains free to read it again.
        """
        candidates=[]
        for message in messages:
            value=image_value(message)
            if value is None:continue
            for index,row in enumerate(value['_images']):
                if not PAGE_PATH.fullmatch(row.get('path','')):raise ValueError('PDF预览路径无效')
                # Receipts created before metadata was added still point at the
                # same immutable PDF page; no execution or model call is replayed.
                if not all(key in row for key in ('width','height','bytes','sha256')):row=page_ref(self.root,row)
                width,height=row['width'],row['height']
                if not isinstance(width,int) or not isinstance(height,int) or not 0<width<=1600 or not 0<height<=1600:raise ValueError('图片尺寸无效')
                if not isinstance(row['bytes'],int) or not 0<row['bytes']<=20_000_000 or not re.fullmatch(r'[a-f0-9]{64}',row['sha256']):raise ValueError('图片版本或大小无效')
                key=str(message.id or message.tool_call_id)+':'+str(index)+':'+row['sha256']
                tokens=self.policy.tokens(width,height) if self.policy.enabled else 0
                candidates.append((message,key,row,tokens))
        selected={};tokens=0;payload=0
        for message,key,row,cost in reversed(candidates):
            encoded=4*((row['bytes']+2)//3)
            if not self.policy.enabled or key in self.omitted:continue
            if tokens+cost>max_tokens or payload+encoded>self.policy.max_inline_bytes:
                self.omitted[key]=True
                continue
            selected.setdefault(id(message),[]).append((key,row,cost))
            tokens+=cost;payload+=encoded
        output=[];pending=[]
        def flush_images():
            if pending:
                output.append(HumanMessage(content=list(pending)))
                pending.clear()
        for message in messages:
            if not isinstance(message,ToolMessage):flush_images()
            value=image_value(message)
            if value is None:output.append(message);continue
            public={k:v for k,v in value.items() if k!='_images'}
            rows=list(reversed(selected.get(id(message),[])))
            public['image_refs']=value['_images']
            shown={row['page'] for _,row,_ in rows}
            public['visual_pages']=sorted(shown)
            public['visual_review']=('本次请求附实际页面图像；未附的页面仅保留文本及固定版本重读入口。' if rows else
                '当前未附图片，仅有PDF文本；不能声称已做视觉检查。可按source_version、pdf_sha256和页码重新writing_preview。')
            blocks=[{'type':'text','text':json.dumps(public,ensure_ascii=False)}]
            blocks.extend({'type':'image_ref','image_ref':row,'tokens':cost} for _,row,cost in rows)
            if rows and self.policy.strategy=='deepseek':
                # Follow the documented user-image shape. Keep every required
                # tool response before adding the derived image-bearing user turn.
                output.append(message.model_copy(update={'content':blocks[0]['text']}))
                pending.append({'type':'text','text':json.dumps({'tool_call_id':message.tool_call_id,
                    'source_version':value.get('source_version'),'pdf_sha256':value.get('pdf_sha256'),
                    'visual_pages':sorted(shown)},ensure_ascii=False)})
                pending.extend(blocks[1:])
            else:output.append(message.model_copy(update={'content':blocks if rows else blocks[0]['text']}))
        flush_images()
        return output

    def original_tokens(self,messages,estimate):
        total=estimate([message.model_dump() for message in messages])
        if self.policy.enabled:
            for message in messages:
                value=image_value(message)
                if value:
                    for row in value['_images']:
                        if 'width' not in row:row=page_ref(self.root,row)
                        total+=self.policy.tokens(row['width'],row['height'])
        return total

    def hydrate(self,messages):
        """Disposable request copy. These bytes never enter a checkpoint/transcript."""
        output=[];cache={}
        for message in messages:
            if not isinstance(message.content,list):output.append(message);continue
            blocks=[]
            for block in message.content:
                if not isinstance(block,dict) or block.get('type')!='image_ref':blocks.append(block);continue
                row=block['image_ref'];name=row['path']
                if not PAGE_PATH.fullmatch(name):raise ValueError('PDF预览路径无效')
                key=row['sha256']
                if key not in cache:
                    with workspace_path(self.root,name).open('rb') as stream:raw=stream.read(row['bytes']+1)
                    if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=key:raise ValueError('PDF预览图片版本校验失败')
                    cache[key]='data:image/png;base64,'+base64.b64encode(raw).decode()
                blocks.append({'type':'image_url','image_url':{'url':cache[key],'detail':row['detail']}})
            output.append(message.model_copy(update={'content':blocks}))
        return output
