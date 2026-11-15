"""Bound active context; canonical messages, tool arguments and receipts stay intact."""
import json
import asyncio
import logging
from copy import deepcopy
from typing import Any, Callable, Awaitable, Annotated
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, ConfigDict, Field
from .images import ImageProjection,disable_image_inputs,image_value,images_unsupported

logger = logging.getLogger(__name__)


class WorkNotes(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # The total token budget is checked separately, relative to the input window.
    notes: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(min_length=1,max_length=16)


def summary_history(messages):
    def public_content(message):
        if not isinstance(message.content,list):return message.content
        return [part for part in message.content if not isinstance(part,dict)
                or part.get('type') not in {'thinking','redacted_thinking','reasoning','tool_use'}]
    return [{'role':m.type,'content':public_content(m),
             **({'tool_calls':deepcopy(m.tool_calls)} if isinstance(m,AIMessage) else {}),
             **({'tool_call_id':m.tool_call_id,'status':m.status} if isinstance(m,ToolMessage) else {})}
            for m in messages]


def state_message(facts):
    return HumanMessage(content=json.dumps({'snapshot_scope':'此条消息时的工作区状态；之后以工具回执和更新事件为准。',
                                           **facts},ensure_ascii=False,sort_keys=True),
                        additional_kwargs={'context_snapshot':True})


def state_update(before, after):
    """Append only changed facts; list records are addressed by their real IDs."""
    changes=[]
    def diff(old,new,path):
        if old==new:return
        if isinstance(old,dict) and isinstance(new,dict):
            for key in old:
                if key not in new:changes.append({'path':[*path,key],'removed':True})
            for key,value in new.items():
                if key not in old:changes.append({'path':[*path,key],'value':value})
                else:diff(old[key],value,[*path,key])
            return
        if isinstance(old,list) and isinstance(new,list):
            rows=old+new
            key=next((key for key in ('path','run_id','id','title') if rows
                      and all(isinstance(row,dict) and key in row for row in rows)
                      and all(len({row[key] for row in part})==len(part) for part in (old,new))),None)
            if key:
                diff({row[key]:row for row in old},{row[key]:row for row in new},path)
                return
            if path and path[-1]=='files' and all(isinstance(row,str) for row in rows):
                diff(dict.fromkeys(old,True),dict.fromkeys(new,True),path)
                return
        changes.append({'path':path,'value':new})
    diff(before,after,[])
    if not changes:return None
    return HumanMessage(content=json.dumps({'workspace_updates':changes,
        'scope':'只列本次变化；路径或ID定位列表记录，未列字段保持原值，之后以新回执为准。'},ensure_ascii=False),
        additional_kwargs={'context_update':True})


from .metering import message_estimate, token_estimate, request_estimate, input_limit


def summary_model(llm):
    """Auxiliary notes have their own small budget; research thinking stays enabled."""
    from ..config.generation import request_model
    return request_model(llm,'summary')


def complete_boundaries(messages, *, include_end=False):
    pending=set();cuts=[]
    for index,message in enumerate(messages):
        if isinstance(message,AIMessage):
            if not pending:cuts.append(index)
            pending.update(t['id'] for t in message.tool_calls)
        elif isinstance(message,ToolMessage):pending.discard(message.tool_call_id)
    if include_end and not pending:cuts.append(len(messages))
    return cuts


def public_journal(messages, locators):
    """Deterministic fallback, never a fabricated summary or executable tool turn.

    Keep public decisions verbatim. Bulky arguments/results remain retrievable at
    exact character offsets in the immutable span archive. Private reasoning is
    neither summarized nor copied into this journal.
    """
    rows=summary_history(messages)
    for row,locator in zip(rows,locators):
        row['source']=locator
        for call in row.get('tool_calls',[]):
            call['args']={k:v if token_estimate(v)<300 else {'archived_argument':k,'source':locator}
                          for k,v in call['args'].items()}
    # Original user events are retained by view(), outside these historical notes.
    return [row for row in rows if row['role']!='human']


class ContextManager:
    def __init__(self,ctx,llm,session,*,pinned=None,tail=None,preserved_prefix=0):
        self.ctx,self.llm,self.session=ctx,llm,session
        self.messages=[];self.pinned=pinned
        self.tail=tail
        self.preserved_prefix=preserved_prefix
        self.last_request=None

    def notes(self,messages):
        from ai_system.research.runtime import ACTIVE_BUDGET
        budget=ACTIVE_BUDGET.get()
        identity='budget-wrapup:'+self.session
        notes=list(self.tail(messages)) if self.tail else []
        if budget is not None and not any(m.id==identity for m in messages):
            note=budget.wrap_up()
            if note:notes.append(HumanMessage(content=note,id=identity))
        return notes

    def archive(self,value):
        from ai_system.research.store import digest
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        path='.system/context-outputs/'+digest(text)+'.json'
        if not self.ctx.fs.exists(path):self.ctx.fs.write_text(path,text)
        return path

    def offload(self,message,*,old=False):
        if not isinstance(message,ToolMessage):return message
        original=message.content if isinstance(message.content,str) else None
        # Model observations omit transport telemetry and repeated task inputs.
        # Full immutable results remain available through the same file reader.
        try:value=json.loads(message.content)
        except (ValueError,TypeError):value=None
        name=(message.name or '').replace('.','_')
        omitted=({'phase_timings','transfer'} if name in {'research_run_file','research_run_python'} else
                 {'task','initial_task','model_task','inputs','context_mode'} if name=='research_delegate' else set())
        if isinstance(value,dict) and omitted.intersection(value):
            value={k:v for k,v in value.items() if k not in omitted}
            value['_full_output_path']=self.archive(original)
            message=message.model_copy(update={'content':json.dumps(value,ensure_ascii=False)})
        images=None
        value=image_value(message)
        if value is not None:
            images=value['_images']
            if old:
                value.pop('_images')
                value['image_refs']=images
                value['visual_review']='历史页图已移出当前上下文；需要时按路径和页码重新读取，writing_preview查看当前稿件。'
                message=message.model_copy(update={'content':json.dumps(value,ensure_ascii=False)})
        if isinstance(message.content,list):
            if not old:return message
            # Old page images leave context; preview reads the current manuscript.
            text='\n'.join(p.get('text','') for p in message.content if isinstance(p,dict) and p.get('type')=='text')
            message=message.model_copy(update={'content':text})
        raw=str(message.content)
        if token_estimate(raw)<=(500 if old else 4000):return message
        path=self.archive(original if original is not None else raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):data={}
        except (ValueError,TypeError):data={}
        fields={'path','version','total_chars','view','json_pointer','question_id','experiment_id','run_id','execution_status',
                'revision','status','input_path','stdout_path','failure_kind','error','delivery_error',
                'source_version','pdf_sha256','total_pages','next_offset','next_start','total','read_tool'}
        data={k:v for k,v in data.items() if k in fields and token_estimate(v)<200}
        if images is not None:
            data['image_refs' if old else '_images']=images
            if old:data['visual_review']='历史页图已移出；需要时按路径和页码重新读取，writing_preview查看当前稿件。'
        data.update(excerpt=raw[:240] if old else raw[:2000]+'\n…\n'+raw[-1000:],_full_output_path=path)
        return message.model_copy(update={'content':json.dumps(data,ensure_ascii=False)})

    async def call(self,request,handler):
        from ai_system.research.runtime import execution_scope,ModelServiceError
        from ai_system.research.store import digest
        from ai_system.graph.llm_utils import as_messages, call_structured
        from ai_system.config.generation import ensure_complete,ModelGenerationLimit,ModelContextLimit,ModelContextOverflow,FixedContextLimit,ContextCompactionFailed
        from langchain_core.utils.function_calling import convert_to_openai_tool
        store=self.ctx.research
        self.messages=list(request.messages)
        self.ctx.agent_messages=self.messages
        schemas=[convert_to_openai_tool(t) for t in request.tools]
        tools=token_estimate(schemas)
        system=getattr(request,'system_message',None)
        static_hash=digest([schemas,messages_to_dict([system]) if system else []])
        overhead=tools+(message_estimate([system]) if system else 0)
        profile=getattr(self.llm,'profile',None) or {}
        # Output reservation is deducted once; targets are not model hard limits.
        limit=input_limit(self.llm)
        high=int(limit*.8);low=int(limit*.5);safe=int(limit*.95)
        messages=list(self.messages)
        boundaries=complete_boundaries(messages)
        start=boundaries[0] if boundaries else len(messages)
        key='context:'+self.session
        with store._connect() as db:
            row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,key)).fetchone()
        saved=json.loads(row[0]) if row else {}
        cut=saved.get('cut',start)
        def signature(end):return digest(messages_to_dict(self.messages[:end]))
        if saved.get('format')!=6 or cut<start or cut>len(messages) or saved.get('hash')!=signature(cut):
            # Reconstruct from canonical history rather than trusting an older,
            # potentially incomplete handoff. No executed operation is replayed.
            saved={'format':6,'cut':start,'hash':signature(start),'offloads':{}}
            cut=start
        previous=json.dumps(saved,ensure_ascii=False,sort_keys=True)
        media=ImageProjection(self.ctx.workspace,self.llm,saved.setdefault('image_offloads',{}))
        def estimate(value):return token_estimate(value,image_counter=media.native_tokens)
        def estimate_messages(rows):return message_estimate(rows,image_counter=media.native_tokens)
        def current_route():
            return digest([type(self.llm).__module__,getattr(self.llm,'model_name',None),getattr(self.llm,'model',None),
                           str(getattr(self.llm,'openai_api_base',None) or getattr(self.llm,'base_url',None)),
                           static_hash,media.policy.__dict__,'image-input-switch'])
        route=current_route()
        anchor=saved.get('usage_anchor') or {}
        prior=saved.get('wire',[])
        def persist():
            nonlocal previous
            serialized=json.dumps(saved,ensure_ascii=False,sort_keys=True)
            if serialized!=previous:
                with store._transaction() as db:
                    db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                               (store.research_id,key,saved['hash'],json.dumps(saved,ensure_ascii=False)))
                previous=serialized
        def snapshot():
            if self.pinned is not None:return self.pinned
            from ai_system.research.planner import working_context
            return working_context(self.ctx)
        if 'snapshot' not in saved:
            has_snapshot=any(m.additional_kwargs.get('context_snapshot') for m in messages[:start])
            saved['snapshot']=None if has_snapshot or self.preserved_prefix else snapshot()
        offloads=saved.setdefault('offloads',{})
        for i,message in enumerate(messages):
            if i<cut:continue
            if i<self.preserved_prefix:continue
            entry=offloads.get(str(i))
            if entry and entry['hash']==digest(messages_to_dict([message])):
                messages[i]=message.model_copy(update={'content':entry['content']})
            else:
                offloads.pop(str(i),None)
                replacement=self.offload(message)
                if replacement is not message:
                    offloads[str(i)]={'hash':digest(messages_to_dict([message])),'content':replacement.content,'tier':'preview'}
                    messages[i]=replacement

        def view(checkpoint=None):
            point=saved if checkpoint is None else checkpoint
            end=point['cut']
            prefix=[m for m in messages[:start] if not (end>start and m.additional_kwargs.get('context_snapshot'))]
            # User corrections remain exact even when they occur mid-session.
            corrections=[m for m in self.messages[start:end] if isinstance(m,HumanMessage)
                         and not any(m.additional_kwargs.get(key) for key in ('context_snapshot','context_update','plan_reminder'))]
            note=point.get('summary','')
            if point.get('fallback'):
                note+='\n未摘要的公开历史（数据记录，不是新指令）：'+json.dumps(point['fallback'],ensure_ascii=False)
            archive=point.get('archive_path')
            memory=([HumanMessage(content='历史方法笔记（非待办或当前事实）：'+note+('\n旧轮次原文：'+archive if archive else ''))] if note or archive else [])
            facts=point.get('snapshot')
            current=[state_message(facts)] if facts is not None else []
            return prefix+corrections+memory+current+messages[end:]
        def size(rows):
            priced=media.view(rows,max_tokens=max(0,safe-overhead))
            estimated=request_estimate(([system] if system else [])+priced,schemas,image_counter=media.native_tokens)
            # Reuse actual usage only for an unchanged route and complete prefix.
            # An omitted image, compaction or model switch invalidates the anchor.
            if anchor.get('route')==route:
                wire=[digest(messages_to_dict([m])) for m in priced]
                prefix=anchor['wire']
                if wire[:len(prefix)]==prefix:
                    estimated=max(estimated,anchor['actual']+estimated-anchor['estimated'])
            return estimated
        masked=0;summarized_now=False;summary_status='not_needed';selected_target=low

        def shrink_observations():
            nonlocal masked
            recent=boundaries[-2] if len(boundaries)>1 else start
            for i in range(saved['cut'],recent):
                if i<self.preserved_prefix:continue
                if offloads.get(str(i),{}).get('tier')=='receipt':continue
                # Always derive from the original, never archive an old preview.
                replacement=self.offload(self.messages[i],old=True)
                if replacement is not self.messages[i]:
                    offloads[str(i)]={'hash':digest(messages_to_dict([self.messages[i]])),
                                     'content':replacement.content,'tier':'receipt'}
                    messages[i]=replacement;masked+=1

        async def compact(*,force=False):
            nonlocal saved,cut,summarized_now,summary_status,selected_target
            before=size(view())
            shrink_observations()
            if not force and size(view())<=high:return
            if force and size(view())<=min(high,int(before*.8)):return
            current=snapshot()
            reserve=min(1800,max(256,low//5))
            candidates=[i for i in boundaries if i>saved['cut']]
            # Preserve recent complete protocol turns when possible; a soft goal
            # must not reject a legal large latest turn. Force mode requires an
            # actual reduction before one rejected request may be retried.
            end=None
            targets=(low,high,safe) if not force else tuple(min(t,int(size(view())*.8)) for t in (low,high,safe))
            for target in targets:
                end=next((i for i in candidates if size(view({**saved,'cut':i,'summary':'','fallback':[],
                        'snapshot':current}))+reserve+200<=target),None)
                if end is not None:
                    selected_target=target;break
            if end is None:
                if not force and size(view())<=safe:return
                # A huge *completed* latest turn can be archived as one unit.
                # Never split a pending call from its required protocol state.
                closed=complete_boundaries(self.messages,include_end=True)
                if len(messages) in closed and len(messages)>saved['cut'] and size(view({**saved,'cut':len(messages),
                        'summary':'','fallback':[],'snapshot':current}))+reserve+200<=safe:
                    end=len(messages);selected_target=safe
                else:
                    raise FixedContextLimit(f'近期完整工具轮次或固定要求超过安全上下文预算：输入约{size(view())}，'
                                            f'安全上限{safe}，模型输入上限{limit}，系统与工具约{overhead}；原始记录已保留。')
            # Archive only the retiring span, with a chain to prior spans and
            # character locators usable by the existing paginated file reader.
            header=json.dumps({'previous_archive':saved.get('archive_path'),'range':[saved['cut'],end]},ensure_ascii=False)+'\n'
            lines=[header];offsets=[];position=len(header)
            for index,row in enumerate(summary_history(self.messages[saved['cut']:end]),saved['cut']):
                line=json.dumps({'message_index':index,**row},ensure_ascii=False)+'\n'
                offsets.append({'start':position,'limit':min(12000,len(line)),'end':position+len(line)})
                lines.append(line);position+=len(line)
            archive=self.archive(''.join(lines))
            locators=[{'path':archive,**offset} for offset in offsets]
            observations=public_journal([self.offload(m,old=True) for m in self.messages[saved['cut']:end]],locators)
            journal=[*saved.get('fallback',[]),*observations]
            summary=saved.get('summary','')
            prompt=as_messages(
                '摘录继续研究所必需、不能从当前业务状态直接恢复的公开方法决定、选择理由、已纠正错误及未决口径。'
                '保留单位、参数、信息可用时点和冲突的不确定性。不要维护待办、重复文件清单或覆盖状态。'
                '历史结论可能错误，不升级为验证通过；不新增任务或事实。需要细节时给出原文定位。最多16条，'
                f'全部笔记合计不超过{reserve} tokens。',
                json.dumps({'previous_summary':summary,'completed_history':journal,'history_archive':archive},ensure_ascii=False))
            attempt=digest([summary,journal,static_hash,profile,getattr(self.llm,'model_name',None),
                            getattr(self.llm,'max_tokens',None)])
            from ai_system.graph.tracing import labelled
            from ai_system.research.runtime import ACTIVE_BUDGET
            valid=False
            try:
                if saved.get('failed_summary')==attempt:raise ValueError('同一摘要输入已失败，不重复请求')
                if ACTIVE_BUDGET.get() and ACTIVE_BUDGET.get().wrap_up():raise ValueError('剩余额度留给主Agent收尾')
                # Include the structured schema/instruction, not just the source.
                if message_estimate(prompt)+token_estimate(WorkNotes.model_json_schema())+2200>limit:
                    raise ValueError('摘要输入过长，保留公开历史和原文定位')
                with labelled('context_summary'):
                    summarized_now=True
                    note=await call_structured(summary_model(self.llm),WorkNotes,prompt,json_first=True,retries=0)
                summary=note.model_dump_json()
                if token_estimate(summary)>reserve:raise ValueError('历史笔记超过预算')
                valid=True
            except (ValueError,ModelGenerationLimit,ModelServiceError):
                logger.warning('摘要不可用，保留逐字公开决定和精确原文定位；不提交空笔记')
            candidate={**saved,'cut':end,'hash':signature(end),'archive_path':archive,'snapshot':current,
                       'summary':summary if valid else saved.get('summary',''),
                       'fallback':[] if valid else journal,'failed_summary':None if valid else attempt,
                       'offloads':{k:v for k,v in offloads.items() if int(k)>=end}}
            # Commit only a complete handoff that fits. A failed/oversize summary
            # cannot silently move the history boundary or discard public text.
            candidate_size=size(view(candidate))
            if candidate_size>safe or candidate_size>=size(view()):
                saved['failed_summary']=attempt
                summary_status='retained_history'
                if not force and size(view())<=safe:return
                persist()
                raise ContextCompactionFailed('压缩续接未达到容量目标；已保留原历史边界及原文定位，未重放计算。')
            saved=candidate;cut=end
            summary_status='summarized' if valid else 'public_history_fallback'

        # Keep an inherited effective request intact while it still fits; only
        # capacity pressure may retire the shared prefix through normal compaction.
        threshold=safe if self.preserved_prefix>saved['cut'] else high
        if size(view())>threshold:await compact()

        degraded=False
        async def send():
            nonlocal previous,prior,media,route,degraded
            route=current_route()
            bounded=media.view(view(),max_tokens=max(0,safe-overhead))
            # size() projects again and clears this count; keep the decision from the request copy.
            attached,absent_reason=media.attached,media.absent_reason
            if size(bounded)>safe:
                raise ModelContextLimit(f'完整请求约{size(bounded)} tokens超过安全输入上限{safe}；原始记录已保留。')
            wire=[digest(messages_to_dict([m])) for m in bounded]
            common=0
            for left,right in zip(prior,wire):
                if left!=right:break
                common+=1
            if saved.get('static_hash')!=static_hash:common=0
            changed_history=bool(prior) and common<len(prior)
            phase='cold' if not prior else ('after_compaction' if masked or summarized_now else 'prefix_reset') if changed_history else 'stable'
            saved.update(wire=wire,static_hash=static_hash)
            persist()
            prior=wire
            if attached:logger.info('本次请求附图 %s 张',attached)
            else:logger.info('本次请求未附图：%s',absent_reason)
            groups={}
            for message in bounded:
                category=('state_snapshot' if message.additional_kwargs.get('context_snapshot') else
                          'state_update' if message.additional_kwargs.get('context_update') else
                          'plan_reminder' if message.additional_kwargs.get('plan_reminder') else message.type)
                groups[category]=groups.get(category,0)+estimate_messages([message])
            stats={'messages_estimated_tokens':estimate_messages(bounded),
                   'tools_estimated_tokens':tools,'total_estimated_tokens':size(bounded),
                   'message_groups_estimated_tokens':groups,
                   'input_limit':limit,'high_watermark':high,'low_watermark':low,'safe_input_limit':safe,
                   'compaction_target':selected_target,'summary_status':summary_status,
                   'original_messages_estimated_tokens':media.original_tokens(self.messages,estimate_messages),
                   'images_attached':attached,'images_absent_reason':absent_reason,
                   'image_placement':media.policy.strategy,'image_offloaded_occurrences':len(media.omitted),
                   'summarized_messages':max(0,cut-start),'offloaded_observations':len(saved['offloads']),
                   'cache_phase':phase,'reused_prefix_messages':common}
            try:
                with execution_scope(context_usage=stats):
                    self.last_request=request.override(messages=bounded)
                    wire_messages=await asyncio.to_thread(media.hydrate,bounded)
                    response=await handler(request.override(messages=wire_messages))
                    ensure_complete(response)
                    for reply in reversed(getattr(response,'result',None) or [response]):
                        actual=(getattr(reply,'usage_metadata',None) or {}).get('input_tokens')
                        if isinstance(actual,int) and actual>0:
                            saved['usage_anchor']={'route':route,'wire':wire,'actual':actual,
                                'estimated':overhead+estimate_messages(bounded)}
                            persist()
                            break
                    return response
            except Exception as exc:
                if degraded or not attached or not images_unsupported(exc):raise
                degraded=True
                logger.warning('图片输入被拒绝，已关闭开关并去掉图片重试一次')
                disable_image_inputs(self.llm)
                media=ImageProjection(self.ctx.workspace,self.llm,saved.setdefault('image_offloads',{}))
                return await send()
        try:return await send()
        except ModelContextOverflow:
            # Only a rejected request before any response content is eligible.
            # No executed tool is replayed, and a second overflow propagates.
            await compact(force=True)
            return await send()


MALFORMED_NOTE = "[系统提醒] 你上一次工具调用的参数不是合法 JSON，没有执行。请重新调用，代码里的引号和换行要正确转义。"


def drop_invalid_calls(message: AIMessage) -> AIMessage:
    """参数解析失败的工具调用不能留在历史里：发回接口时会被当成正常调用，又没有对应的工具结果，接口直接 400"""
    if not message.invalid_tool_calls:
        return message
    return message.model_copy(update={"invalid_tool_calls": []})
