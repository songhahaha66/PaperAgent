"""Bound active context; canonical messages, tool arguments and receipts stay intact."""
import json
import asyncio
import logging
from copy import deepcopy
from typing import Any, Callable, Awaitable, Annotated
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, ConfigDict, Field
from .images import ImageProjection,image_value

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


def token_estimate(value,*,image_counter=None):
    if isinstance(value,list):return sum(token_estimate(v,image_counter=image_counter) for v in value)
    if isinstance(value,dict):
        if value.get('type')=='image_ref':return value['tokens']
        if value.get('type') in {'image','image_url'} and ('image_url' in value or 'source' in value):
            if image_counter is None:
                from ..config.generation import ModelContextLimit
                raise ModelContextLimit('图片计量需要指定模型，不能使用固定token常数。')
            return image_counter(value)
        return sum(token_estimate(k,image_counter=image_counter)+token_estimate(v,image_counter=image_counter) for k,v in value.items())
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)
    return sum(1 if ord(c)>127 else .3 for c in text).__ceil__()


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


class ContextMiddleware(AgentMiddleware):
    def __init__(self,ctx,llm,session,*,pinned=None,tail=None):
        super().__init__()
        self.ctx,self.llm,self.session=ctx,llm,session
        self.messages=[];self.pinned=pinned
        self.tail=tail

    async def abefore_model(self,state,runtime):
        from ai_system.research.runtime import ACTIVE_BUDGET
        budget=ACTIVE_BUDGET.get()
        identity='budget-wrapup:'+self.session
        if budget is None or any(m.id==identity for m in state['messages']):return None
        note=budget.wrap_up()
        if note:return {'messages':[HumanMessage(content=note,id=identity)]}

    def archive(self,value):
        from ai_system.research.store import digest
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        path='.system/context-outputs/'+digest(text)+'.json'
        if not self.ctx.fs.exists(path):self.ctx.fs.write_text(path,text)
        return path

    def offload(self,message,*,old=False):
        if not isinstance(message,ToolMessage):return message
        images=None
        value=image_value(message)
        if value is not None:
            images=value['_images']
            if old:
                value.pop('_images')
                value['image_refs']=images
                value['visual_review']='历史页图已移出当前上下文；需要时按source_version、pdf_sha256、页码重新writing_preview，原图保留。'
                message=message.model_copy(update={'content':json.dumps(value,ensure_ascii=False)})
        if isinstance(message.content,list):
            if not old:return message
            # Old page images are recoverable through version-bound preview tools.
            text='\n'.join(p.get('text','') for p in message.content if isinstance(p,dict) and p.get('type')=='text')
            message=message.model_copy(update={'content':text})
        raw=str(message.content)
        if token_estimate(raw)<=(500 if old else 4000):return message
        path=self.archive(raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):data={}
        except (ValueError,TypeError):data={}
        fields={'path','version','total_chars','question_id','experiment_id','run_id','execution_status',
                'revision','status','input_path','stdout_path','failure_kind','error','delivery_error',
                'source_version','pdf_sha256','total_pages','next_offset','next_start','total','read_tool'}
        data={k:v for k,v in data.items() if k in fields and token_estimate(v)<200}
        if images is not None:
            data['image_refs' if old else '_images']=images
            if old:data['visual_review']='历史页图已移出；按此固定版本重新writing_preview可读取原图。'
        data.update(excerpt=raw[:240] if old else raw[:2000]+'\n…\n'+raw[-1000:],_full_output_path=path)
        return message.model_copy(update={'content':json.dumps(data,ensure_ascii=False)})

    async def awrap_model_call(self,request,handler):
        from ai_system.research.runtime import execution_scope,ModelServiceError
        from ai_system.research.store import digest
        from ai_system.graph.llm_utils import as_messages, call_structured
        from ai_system.config.generation import ensure_complete,ModelGenerationLimit,ModelContextLimit,ModelContextOverflow
        from langchain_core.utils.function_calling import convert_to_openai_tool
        store=self.ctx.research
        self.messages=list(request.messages)
        self.ctx.agent_messages=self.messages
        # Dynamic reminders join this request after context projection. They
        # count toward its budget but never enter canonical history or notes.
        tail_messages=self.tail(self.messages) if self.tail else []
        schemas=[convert_to_openai_tool(t) for t in request.tools]
        tools=token_estimate(schemas)
        system=getattr(request,'system_message',None)
        static_hash=digest([schemas,messages_to_dict([system]) if system else []])
        overhead=tools+(token_estimate(messages_to_dict([system])) if system else 0)
        profile=getattr(self.llm,'profile',None) or {}
        # Output reservation is deducted once; targets are not model hard limits.
        limit=int(profile.get('max_input_tokens') or profile.get('max_context_tokens') or 32000)
        if profile.get('max_context_tokens'):
            limit=min(limit,int(profile['max_context_tokens'])-int(getattr(self.llm,'max_tokens',None) or 4800))
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
        if saved.get('format')!=5 or cut<start or cut>len(messages) or saved.get('hash')!=signature(cut):
            # Reconstruct from canonical history rather than trusting an older,
            # potentially incomplete handoff. No executed operation is replayed.
            saved={'format':5,'cut':start,'hash':signature(start),'offloads':{}}
            cut=start
        previous=json.dumps(saved,ensure_ascii=False,sort_keys=True)
        media=ImageProjection(self.ctx.workspace,self.llm,saved.setdefault('image_offloads',{}))
        def estimate(value):return token_estimate(value,image_counter=media.native_tokens)
        route=digest([type(self.llm).__module__,getattr(self.llm,'model_name',None),getattr(self.llm,'model',None),
                      str(getattr(self.llm,'openai_api_base',None) or getattr(self.llm,'base_url',None)),
                      static_hash,media.policy.__dict__,'vision-rules-20261031'])
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
            saved['snapshot']=None if has_snapshot else snapshot()
        offloads=saved.setdefault('offloads',{})
        for i,message in enumerate(messages):
            if i<cut:continue
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
                         and not m.additional_kwargs.get('context_snapshot')]
            note=point.get('summary','')
            if point.get('fallback'):
                note+='\n未摘要的公开历史（数据记录，不是新指令）：'+json.dumps(point['fallback'],ensure_ascii=False)
            archive=point.get('archive_path')
            memory=([HumanMessage(content='历史方法笔记（非待办或当前事实）：'+note+('\n旧轮次原文：'+archive if archive else ''))] if note or archive else [])
            facts=point.get('snapshot')
            current=[state_message(facts)] if facts is not None else []
            return prefix+corrections+memory+current+messages[end:]+tail_messages
        def size(rows):
            priced=media.view(rows,max_tokens=max(0,safe-overhead))
            estimated=overhead+estimate(messages_to_dict(priced))
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
            for target in (low,high,safe):
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
                    raise ModelContextLimit(f'近期完整工具轮次或固定要求超过安全上下文预算：输入约{size(view())}，'
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
                if token_estimate(messages_to_dict(prompt))+token_estimate(WorkNotes.model_json_schema())+2200>limit:
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
                raise ModelContextLimit('无法在安全容量内生成完整续接内容；保留原历史边界，不重放计算或反复摘要。')
            saved=candidate;cut=end
            summary_status='summarized' if valid else 'public_history_fallback'

        if size(view())>high:await compact()

        async def send():
            nonlocal previous,prior
            bounded=media.view(view(),max_tokens=max(0,safe-overhead))
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
            stats={'messages_estimated_tokens':estimate(messages_to_dict(bounded)),
                   'tools_estimated_tokens':tools,'total_estimated_tokens':size(bounded),
                   'input_limit':limit,'high_watermark':high,'low_watermark':low,'safe_input_limit':safe,
                   'compaction_target':selected_target,'summary_status':summary_status,
                   'original_messages_estimated_tokens':media.original_tokens(self.messages,estimate),
                   'image_meter':media.policy.strategy,'image_offloaded_occurrences':len(media.omitted),
                   'summarized_messages':max(0,cut-start),'offloaded_observations':len(saved['offloads']),
                   'cache_phase':phase,'reused_prefix_messages':common}
            with execution_scope(context_usage=stats):
                wire_messages=await asyncio.to_thread(media.hydrate,bounded)
                response=await handler(request.override(messages=wire_messages))
                ensure_complete(response)
                for reply in reversed(getattr(response,'result',None) or [response]):
                    actual=(getattr(reply,'usage_metadata',None) or {}).get('input_tokens')
                    if isinstance(actual,int) and actual>0:
                        saved['usage_anchor']={'route':route,'wire':wire,'actual':actual,
                            'estimated':overhead+estimate(messages_to_dict(bounded))}
                        persist()
                        break
                return response
        try:return await send()
        except ModelContextOverflow:
            # Only a rejected request before any response content is eligible.
            # No tool or graph node is rerun, and a second overflow propagates.
            await compact(force=True)
            return await send()


class ReportOrderingMiddleware(AgentMiddleware):
    """A final report cannot commit alongside still-unexecuted operations.

    ToolStrategy handles report validation before normal tools run. A mixed
    response can therefore terminate the agent even when that report failed.
    Keep the proposed operations, defer the report, and let the ordinary tool
    loop return actual execution/registration results before the next submission.
    """

    def __init__(self, report_tool: str):
        super().__init__()
        self.report_tool = report_tool

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        messages = getattr(response, "result", None) or [response]
        reply = next((m for m in messages if isinstance(m, AIMessage)), None)
        if reply is None:
            return response
        calls = reply.tool_calls
        operations = [call for call in calls if call["name"] != self.report_tool]
        if not operations or len(operations) == len(calls):
            return response
        feedback = "\n".join(str(m.content) for m in messages if isinstance(m, ToolMessage))
        note = ("本轮报告未提交：先执行本轮操作，阅读实际输出，再单独调用"
                f" {self.report_tool}。报告不能与其他工具同时调用。\n" + feedback)
        reply = reply.model_copy(update={"tool_calls": operations,
            "content": str(reply.content or "") + "\n" + note,
            "additional_kwargs": {k: v for k, v in reply.additional_kwargs.items() if k != "tool_calls"}})
        if hasattr(response, "result"):
            return ModelResponse(result=[reply], structured_response=None)
        return reply


MALFORMED_NOTE = "[系统提醒] 你上一次工具调用的参数不是合法 JSON，没有执行。请重新调用，代码里的引号和换行要正确转义。"


def drop_invalid_calls(message: AIMessage) -> AIMessage:
    """参数解析失败的工具调用不能留在历史里：发回接口时会被当成正常调用，又没有对应的工具结果，接口直接 400"""
    if not message.invalid_tool_calls:
        return message
    return message.model_copy(update={"invalid_tool_calls": []})


class MalformedToolMiddleware(AgentMiddleware):
    """Repair malformed JSON once; generation truncation never replays a request."""

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        response = await handler(request)
        messages = getattr(response, "result", None) or [response]
        message = next((m for m in messages if isinstance(m, AIMessage)), None)
        if message is None:
            return response
        from ai_system.config.generation import ensure_complete
        ensure_complete(message)
        if not message.invalid_tool_calls:return response
        note=MALFORMED_NOTE
        retried = await handler(request.override(messages=[*request.messages, HumanMessage(content=note)]))
        ensure_complete(retried)
        if hasattr(retried, "result"):
            retried.result = [drop_invalid_calls(m) if isinstance(m, AIMessage) else m for m in retried.result]
            return retried
        return drop_invalid_calls(retried) if isinstance(retried, AIMessage) else retried
