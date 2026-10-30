"""Bound active context; canonical messages, tool arguments and receipts stay intact."""
import json
import logging
from typing import Any, Callable, Awaitable, Annotated
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class WorkNotes(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notes: list[Annotated[str, Field(max_length=240)]] = Field(max_length=8)


def summary_history(messages):
    return [{'role':m.type,'content':m.content,
             **({'tool_calls':m.tool_calls} if isinstance(m,AIMessage) else {}),
             **({'tool_call_id':m.tool_call_id} if isinstance(m,ToolMessage) else {})}
            for m in messages]


def state_message(facts):
    return HumanMessage(content=json.dumps({'snapshot_scope':'此条消息时的工作区状态；之后以工具回执和更新事件为准。',
                                           **facts},ensure_ascii=False,sort_keys=True),
                        additional_kwargs={'context_snapshot':True})


def token_estimate(value):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)
    return sum(1 if ord(c)>127 else .3 for c in text).__ceil__()


def summary_model(llm):
    """Auxiliary notes have their own small budget; research thinking stays enabled."""
    from ..config.generation import request_model
    return request_model(llm,'summary')


def complete_boundaries(messages):
    pending=set();cuts=[]
    for index,message in enumerate(messages):
        if isinstance(message,AIMessage):
            if not pending:cuts.append(index)
            pending.update(t['id'] for t in message.tool_calls)
        elif isinstance(message,ToolMessage):pending.discard(message.tool_call_id)
    return cuts


class ContextMiddleware(AgentMiddleware):
    def __init__(self,ctx,llm,session,*,pinned=None):
        super().__init__()
        self.ctx,self.llm,self.session=ctx,llm,session
        self.messages=[];self.pinned=pinned

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
        raw=str(message.content)
        if token_estimate(raw)<=(500 if old else 4000):return message
        path=self.archive(raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):data={}
        except (ValueError,TypeError):data={}
        fields={'path','version','total_chars','question_id','experiment_id','run_id','execution_status',
                'revision','status','input_path','stdout_path','failure_kind','error','delivery_error'}
        data={k:v for k,v in data.items() if k in fields and token_estimate(v)<200}
        data.update(excerpt=raw[:240] if old else raw[:2000]+'\n…\n'+raw[-1000:],_full_output_path=path)
        return message.model_copy(update={'content':json.dumps(data,ensure_ascii=False)})

    async def awrap_model_call(self,request,handler):
        from ai_system.research.runtime import execution_scope
        from ai_system.research.store import digest
        from ai_system.graph.llm_utils import as_messages, call_structured
        from ai_system.config.generation import ensure_complete,ModelGenerationLimit
        from langchain_core.utils.function_calling import convert_to_openai_tool
        store=self.ctx.research
        self.messages=list(request.messages)
        self.ctx.agent_messages=self.messages
        schemas=[convert_to_openai_tool(t) for t in request.tools]
        tools=token_estimate(schemas)
        system=getattr(request,'system_message',None)
        static_hash=digest([schemas,messages_to_dict([system]) if system else []])
        overhead=tools+(token_estimate(messages_to_dict([system])) if system else 0)
        profile=getattr(self.llm,'profile',None) or {}
        # max_input_tokens is an input ceiling, not a context+output ceiling.
        limit=int(profile.get('max_input_tokens') or 32000)
        if profile.get('max_context_tokens'):
            limit=min(limit,int(profile['max_context_tokens'])-int(getattr(self.llm,'max_tokens',None) or 4800))
        high=int(limit*.8);low=int(limit*.5)
        messages=list(self.messages)
        boundaries=complete_boundaries(messages)
        start=boundaries[0] if boundaries else len(messages)
        key='context:'+self.session
        with store._connect() as db:
            row=db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',(store.research_id,key)).fetchone()
        saved=json.loads(row[0]) if row else {}
        cut=saved.get('cut',start)
        def signature(end):return digest(messages_to_dict(self.messages[:end]))
        if saved.get('format')!=4 or cut<start or cut>len(messages) or saved.get('hash')!=signature(cut):
            saved={'format':4,'cut':start,'hash':signature(start),'offloads':{}}
            cut=start
        previous=json.dumps(saved,ensure_ascii=False,sort_keys=True)
        def snapshot():
            if self.pinned is not None:return self.pinned
            from ai_system.research.planner import working_context
            return working_context(self.ctx)
        # A projection also serves delegated sessions, whose task is already a
        # canonical event. Never move this snapshot to the tail on each call.
        if 'snapshot' not in saved:
            has_snapshot=any(m.additional_kwargs.get('context_snapshot') for m in messages[:start])
            saved['snapshot']=None if has_snapshot else snapshot()
        prefix=[m for m in messages[:start] if not (cut>start and m.additional_kwargs.get('context_snapshot'))]
        offloads=saved.setdefault('offloads',{})
        for i,message in enumerate(messages):
            entry=offloads.get(str(i))
            if entry and entry['hash']==digest(messages_to_dict([message])):
                messages[i]=message.model_copy(update={'content':entry['content']})
            else:
                replacement=self.offload(message)
                if replacement is not message:
                    offloads[str(i)]={'hash':digest(messages_to_dict([message])),'content':replacement.content}
                    messages[i]=replacement
        def view(end=None,notes=None):
            end=cut if end is None else end
            note=saved.get('summary','') if notes is None else notes
            archive=saved.get('archive_path')
            memory=([HumanMessage(content='历史方法笔记（非待办或当前事实）：'+note+('\n旧轮次原文：'+archive if archive else ''))] if note or archive else [])
            facts=saved['snapshot']
            current=[state_message(facts)] if facts is not None else []
            return prefix+memory+current+messages[end:]
        def size(rows):return overhead+token_estimate(messages_to_dict(rows))
        bounded=view()
        masked=0;summarized_now=False
        if size(bounded)>high:
            # Cheap observation offloading precedes any model summarization.
            recent=boundaries[-2] if len(boundaries)>1 else start
            for i in range(cut,recent):
                if str(i) in offloads:continue
                replacement=self.offload(messages[i],old=True)
                if replacement is not messages[i]:
                    offloads[str(i)]={'hash':digest(messages_to_dict([self.messages[i]])),'content':replacement.content}
                    messages[i]=replacement;masked+=1
            bounded=view()
        if size(bounded)>high:
            summarized_now=True
            saved['snapshot']=snapshot()
            prefix=[m for m in prefix if not m.additional_kwargs.get('context_snapshot')]
            reserve=min(1200,max(100,low//5))
            candidates=[i for i in boundaries if i>cut]
            end=next((i for i in candidates if size(view(i,''))+reserve+200<=low),None)
            if end is None:
                raise ValueError('近期完整工具轮次或固定要求超过上下文预算；请缩小单次工具参数/状态，原始记录已保留。')
            history=summary_history(self.messages[start:end])
            archive=self.archive(history)
            summary=saved.get('summary','')
            observations=summary_history(messages[cut:end])
            # This is summarizer input, not tool-call history. Large code/edit
            # arguments remain verbatim in the archive and active recent turns.
            for row in observations:
                if 'tool_calls' in row:
                    actions=[]
                    for call in row.pop('tool_calls'):
                        args=call['args']
                        actions.append({'name':call['name'],
                            'arguments':{k:v for k,v in args.items() if token_estimate(v)<300},
                            'archived_arguments':[k for k,v in args.items() if token_estimate(v)>=300]})
                    row['actions']=actions
            prompt=as_messages(
                '只摘录无法从当前问题、文件、运行记录恢复的方法选择理由和关键纠错经验。不维护待办、文件清单或覆盖状态。'
                '历史结论可能错误，不升级为验证通过；不新增任务或事实。最多8条简短笔记。',
                json.dumps({'previous_summary':summary,'completed_history':observations,'history_archive':archive},ensure_ascii=False))
            from ai_system.graph.tracing import labelled
            with labelled('context_summary'):
                try:
                    from ai_system.research.runtime import ACTIVE_BUDGET
                    if ACTIVE_BUDGET.get() and ACTIVE_BUDGET.get().wrap_up():raise ValueError('剩余额度留给主Agent收尾，历史已归档')
                    if token_estimate(messages_to_dict(prompt))>limit-2000:raise ValueError('摘要输入过长，使用归档检索')
                    note=await call_structured(summary_model(self.llm),WorkNotes,prompt,json_first=True,retries=0)
                    summary=note.model_dump_json()
                    if token_estimate(summary)>reserve:raise ValueError('历史笔记超过预算')
                except (ValueError,ModelGenerationLimit):
                    logger.warning('历史方法笔记无效或超限，保留原有笔记与原始归档')
                    if token_estimate(summary)>reserve:summary=''
            saved.update(cut=end,hash=signature(end),summary=summary,archive_path=archive,snapshot=snapshot(),
                         offloads={k:v for k,v in offloads.items() if int(k)>=end})
            cut=end
            prefix=[m for m in prefix if not m.additional_kwargs.get('context_snapshot')]
            bounded=view()
            if size(bounded)>low:raise ValueError('压缩后上下文未达到目标，原始记录已保留；不重复调用摘要。')
        wire=[digest(messages_to_dict([m])) for m in bounded]
        prior=saved.get('wire',[])
        common=0
        for left,right in zip(prior,wire):
            if left!=right:break
            common+=1
        if saved.get('static_hash')!=static_hash:common=0
        changed_history=bool(prior) and common<len(prior)
        phase='cold' if not prior else ('after_compaction' if masked or summarized_now else 'prefix_reset') if changed_history else 'stable'
        saved.update(wire=wire,static_hash=static_hash)
        if json.dumps(saved,ensure_ascii=False,sort_keys=True)!=previous:
            with store._transaction() as db:
                db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                           (store.research_id,key,saved['hash'],json.dumps(saved,ensure_ascii=False)))
        stats={'messages_estimated_tokens':token_estimate(messages_to_dict(bounded)),
               'tools_estimated_tokens':tools,'total_estimated_tokens':size(bounded),
               'input_limit':limit,'high_watermark':high,'low_watermark':low,
               'original_messages_estimated_tokens':token_estimate(messages_to_dict(self.messages)),
               'summarized_messages':max(0,cut-start),'offloaded_observations':len(saved['offloads']),
               'cache_phase':phase,'reused_prefix_messages':common}
        with execution_scope(context_usage=stats):
            response=await handler(request.override(messages=bounded))
            ensure_complete(response)
            return response


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
