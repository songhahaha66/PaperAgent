"""Bound model context without changing saved messages or execution records."""
import json
import logging
from typing import List,Any,Callable,Awaitable
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class WorkNotes(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notes: list[str]
    pending: list[str]
    files: list[str]


def summary_history(messages):
    """Summarize public observations, never private reasoning or wire metadata."""
    return [{'role':m.type,'content':m.content,
             **({'tool_calls':m.tool_calls} if isinstance(m,AIMessage) else {}),
             **({'tool_call_id':m.tool_call_id} if isinstance(m,ToolMessage) else {})}
            for m in messages]


def token_estimate(value):
    # Conservative fallback for providers without a tokenizer; usage stays authoritative.
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return sum(1 if ord(c) > 127 else .3 for c in text).__ceil__()


class ContextMiddleware(AgentMiddleware):
    def __init__(self, ctx, llm, session, *, pinned=None):
        super().__init__()
        self.ctx, self.llm, self.session = ctx, llm, session
        self.messages = []
        self.pinned=pinned

    async def awrap_model_call(self, request, handler):
        from ai_system.research.runtime import execution_scope
        from ai_system.research.store import digest
        from ai_system.graph.llm_utils import as_messages, call_structured, cap_output
        store = self.ctx.research
        self.messages = list(request.messages)
        from langchain_core.utils.function_calling import convert_to_openai_tool
        tool_tokens = token_estimate([convert_to_openai_tool(t) for t in request.tools])
        profile = getattr(self.llm, 'profile', None) or {}
        limit = int(profile.get('max_input_tokens') or 32000)
        budget = max(2048, limit - 4800 - tool_tokens)
        messages=[]
        # Keep tool arguments truthful. Summarize complete old turns below;
        # prose substituted into a code argument can be copied as executable code.
        for message in self.messages:
            if isinstance(message,ToolMessage) and token_estimate(message.content)>4000:
                raw=str(message.content)
                path='.system/context-outputs/'+digest(raw)+'.json'
                self.ctx.fs.write_text(path,raw)
                try:
                    data=json.loads(raw)
                    if not isinstance(data,dict):raise ValueError()
                    data={k:v for k,v in data.items() if k in {'path','version','total_chars','question_id','experiment_id','run_id','execution_status','revision','status','input_path','stdout_path','failure_kind','error','delivery_error'} and token_estimate(v)<600}
                    data.update(excerpt=raw[:3500]+'\n…\n'+raw[-1500:],_full_output_path=path)
                    content=json.dumps(data,ensure_ascii=False)
                except (ValueError,TypeError):
                    content=raw[:3500]+'\n…\n'+raw[-1500:]+'\n完整输出：'+path
                message=message.model_copy(update={'content':content})
            messages.append(message)
        key = 'context:' + self.session
        with store._connect() as db:
            row = db.execute('SELECT response FROM commands WHERE research_id=? AND id=?', (store.research_id,key)).fetchone()
        saved = json.loads(row[0]) if row else {}
        # The first system/project messages remain verbatim. Only complete old turns
        # can be summarized, so no outstanding tool call is separated from its reply.
        start = next((i for i,m in enumerate(messages) if isinstance(m,AIMessage)),len(messages))
        cut = saved.get('cut',0)
        def prefix(end): return messages_to_dict(messages[start:end])
        if saved.get('format')!=2 or cut <= start or cut > len(messages) or saved.get('hash') != digest(prefix(cut)):
            saved = {};cut = start
        def view():
            facts=self.pinned
            if facts is None and self.ctx.fs.root==store.root:
                from ai_system.research.planner import decision_context
                current=decision_context(store,store.snapshot())[1]
                facts={k:current[k] for k in ('source','questions','runs','inputs','document','document_diagnostics') if k in current}
            notes=([HumanMessage(content='历史工作笔记（可能有误，仅供检索；任务范围与当前事实以原题、下方状态和实际文件为准）：\n'+saved['summary'])] if saved else [])
            current=([HumanMessage(content=json.dumps(facts,ensure_ascii=False))] if saved else [])
            return messages[:start]+notes+messages[cut:]+current
        bounded = view()
        if token_estimate(messages_to_dict(bounded)) > budget:
            boundaries = [i for i,m in enumerate(messages) if isinstance(m,AIMessage) and i>cut]
            # Keep complete recent turns; adapt their size to the available window.
            eligible = [i for i in boundaries if token_estimate(messages_to_dict(messages[i:])) < budget*.5]
            if boundaries:
                end = eligible[0] if eligible else boundaries[-1]
                old = summary_history(messages[cut:end])
                from ai_system.graph.tracing import labelled
                with labelled('context_summary'):
                    try:
                        notes = await call_structured(cap_output(self.llm,1800),WorkNotes,as_messages(
                            '整理历史工作笔记供同一Agent继续。只记录讨论过的方法、遇到的错误、尝试的修正、文件路径和待核实项。'
                            '原题与当前状态由系统直接提供，不重新定义任务、日期、参数或覆盖范围。模型声称正确只记为“曾声称”，不能改写为校验通过；'
                            '无独立检查不能写“未知：无”。不解决新问题，不补充事实。',
                            json.dumps({'original_request':store.snapshot()['spec']['requirement'],
                                        'previous_summary':saved.get('summary',''),'completed_history':old},ensure_ascii=False)),
                            json_first=True,retries=0)
                        summary=notes.model_dump_json()
                    except ValueError:
                        # A malformed note is optional context, never a new plan.
                        # Keep the verified prior note and current durable facts.
                        logger.warning('历史工作笔记格式无效，保留原有笔记和实际记录')
                        summary=saved.get('summary') or '旧轮次已归档；需要时读取已有代码、日志与文件，当前事实见下方状态。'
                saved={'format':2,'cut':end,'hash':digest(prefix(end)),'summary':summary}
                with store._transaction() as db:
                    db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?,?)',
                        (store.research_id,key,saved['hash'],json.dumps(saved,ensure_ascii=False)))
                cut=end;bounded=view()
        stats={'messages_estimated_tokens':token_estimate(messages_to_dict(bounded)),
               'tools_estimated_tokens':tool_tokens,'original_messages_estimated_tokens':token_estimate(messages_to_dict(self.messages)),
               'summarized_messages':max(0,cut-start)}
        with execution_scope(context_usage=stats):
            return await handler(request.override(messages=bounded))


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


TRUNCATED_NOTE = "[系统提醒] 你上一次输出太长，在中途被截断，没有执行。请把代码拆成更短的一段，只写这一步需要的部分。"
MALFORMED_NOTE = "[系统提醒] 你上一次工具调用的参数不是合法 JSON，没有执行。请重新调用，代码里的引号和换行要正确转义。"


def drop_invalid_calls(message: AIMessage) -> AIMessage:
    """参数解析失败的工具调用不能留在历史里：发回接口时会被当成正常调用，又没有对应的工具结果，接口直接 400"""
    if not message.invalid_tool_calls:
        return message
    return message.model_copy(update={"invalid_tool_calls": []})


class TruncationGuardMiddleware(AgentMiddleware):
    """工具调用不完整时带提示重试一次：输出撞到长度上限，或参数不是合法 JSON。

    这两种情况下 tool_calls 为空，循环会误以为模型收尾了；坏掉的调用留在历史里，
    下一次请求还会因为"工具调用没有对应结果"被接口拒绝（官方 DeepSeek API 返回 400，v2l 的 Q1 因此整段失败）。
    """

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        response = await handler(request)
        messages = getattr(response, "result", None) or [response]
        message = next((m for m in messages if isinstance(m, AIMessage)), None)
        if message is None:
            return response
        if (message.response_metadata or {}).get("finish_reason") == "length":
            note = TRUNCATED_NOTE
            # 截断的调用会被丢掉、transcript 里看不到；记下开头和结尾，能看出是代码太长还是重复输出停不下来
            partial = " ".join(str(c.get("args") or "") for c in message.invalid_tool_calls) or str(message.content)
            logger.warning("输出被截断（%d 字）：%r ... %r", len(partial), partial[:150], partial[-300:])
        elif message.invalid_tool_calls:
            note = MALFORMED_NOTE
        else:
            return response
        retried = await handler(request.override(messages=[*request.messages, HumanMessage(content=note)]))
        if hasattr(retried, "result"):
            retried.result = [drop_invalid_calls(m) if isinstance(m, AIMessage) else m for m in retried.result]
            return retried
        return drop_invalid_calls(retried) if isinstance(retried, AIMessage) else retried
