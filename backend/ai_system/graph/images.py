"""Durable page references and request-only image bytes.

Refs remain in checkpoints. Only the disposable model-input copy contains
base64. Whether images are sent is the model config's image_inputs switch.
DeepSeek's official API still receives images in a following user message;
that placement is a transport detail, not a capability gate.
"""
import base64
from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlparse

from langchain_core.messages import ToolMessage, HumanMessage
from services.file_services.workspace_layout import workspace_path
from ..config.generation import ModelContextLimit

PAGE_PATH=re.compile(r'\.system/paper-project/previews/[a-f0-9]{64}/[1-9][0-9]*\.png')
# One reserve for a preview page whose long side is at most 1600px.
PAGE_IMAGE_TOKENS=3000
IMAGE_UNSUPPORTED_NOTE='本次未附图：模型不支持图片输入，已自动关闭'
_IMAGE_UNSUPPORTED=re.compile(
    r'image[_ ]url|'
    r"(?:does not|do not|doesn't|don't|cannot|can't|\bnot) support(?:ing)? (?:an? )?images?|"
    r'image inputs? (?:are |is )?(?:not supported|unsupported)|'
    r'(?:does not|do not|not) support (?:vision|multimodal)|'
    r'unsupported (?:image|vision)|'
    r'unknown variant [`\'"]?image|'
    r'expected [`\'"]?text[`\'"]?.{0,80}image',
    re.I)


@dataclass(frozen=True)
class VisionPolicy:
    enabled: bool
    strategy: str
    upper_bound: int = PAGE_IMAGE_TOKENS
    # Deployment bound, also below DeepSeek's 48 MiB inline request limit.
    max_inline_bytes: int = 32*1024*1024
    disabled_reason: str | None = None

    def tokens(self,width,height):
        return self.upper_bound


def vision_policy(llm):
    """The config switch is the only gate. Missing means on."""
    metadata=getattr(llm,'metadata',None) or {}
    settings=metadata.get('generation_settings') or {}
    declared=settings.get('image_inputs')
    if declared is None:enabled=True
    elif isinstance(declared,bool):enabled=declared
    else:raise ValueError('image_inputs 必须是布尔值')
    base=getattr(llm,'openai_api_base',None) or getattr(llm,'base_url',None)
    host=urlparse(str(base)).hostname if base else None
    strategy='deepseek' if host=='api.deepseek.com' else 'inline'
    reason=metadata.get('image_inputs_disabled_reason') if not enabled else None
    if not isinstance(reason,str):reason=None
    return VisionPolicy(enabled,strategy,disabled_reason=reason)


def _status(exc):
    status=getattr(exc,'status_code',None) or getattr(getattr(exc,'response',None),'status_code',None)
    return status if isinstance(status,int) else None


def _error_text(exc):
    parts=[str(exc)[:2000]]
    body=getattr(exc,'body',None)
    if isinstance(body,dict):parts.append(json.dumps(body,ensure_ascii=False)[:2000])
    elif isinstance(body,str):parts.append(body[:2000])
    message=getattr(exc,'message',None)
    if isinstance(message,str):parts.append(message[:2000])
    return '\n'.join(parts)


def images_unsupported(exc):
    """True only for a client error that says this model cannot take images."""
    seen=set();current=exc
    while current is not None and id(current) not in seen and len(seen)<8:
        seen.add(id(current))
        if _status(current) in {400,422} and _IMAGE_UNSUPPORTED.search(_error_text(current)):
            return True
        current=current.__cause__ or current.__context__
    return False


def disable_image_inputs(llm):
    """Turn the switch off in memory and on the saved model config when it has an id."""
    metadata=getattr(llm,'metadata',None)
    if not isinstance(metadata,dict):
        metadata={}
        try:setattr(llm,'metadata',metadata)
        except (AttributeError,ValueError,TypeError):object.__setattr__(llm,'metadata',metadata)
    settings=dict(metadata.get('generation_settings') or {})
    settings.pop('image_tokens_per_image',None)
    settings['image_inputs']=False
    metadata['generation_settings']=settings
    metadata['image_inputs_disabled_reason']=IMAGE_UNSUPPORTED_NOTE
    from ..config.generation import disable_saved_image_inputs
    disable_saved_image_inputs(metadata.get('model_config_id'))


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
        self.attached=0
        self.absent_reason='no_images'
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
            if rows:public['visual_review']='本次请求附实际页面图像；未附的页面仅保留文本及固定版本重读入口。'
            elif self.policy.disabled_reason:public['visual_review']=self.policy.disabled_reason
            else:public['visual_review']='当前未附图片，仅有PDF文本；不能声称已做视觉检查。可按source_version、pdf_sha256和页码重新writing_preview。'
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
        self.attached=sum(len(rows) for rows in selected.values())
        if self.attached:self.absent_reason=None
        elif not candidates:self.absent_reason='no_images'
        elif not self.policy.enabled:self.absent_reason=self.policy.disabled_reason or 'image_inputs_off'
        else:self.absent_reason='budget'
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
