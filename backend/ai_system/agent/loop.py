"""The shared, durable model/tool loop for lead and child agents."""
import asyncio
import json
import logging
from dataclasses import dataclass, replace
from itertools import groupby
from time import monotonic
from uuid import uuid4
from contextvars import ContextVar
from weakref import WeakValueDictionary

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .context import CURRENT_TOOL, CURRENT_TURN, session_turn
from .store import AgentStore

MODEL_CALLBACKS=ContextVar('agent_model_callbacks',default=None)
_owners=WeakValueDictionary()
logger = logging.getLogger('ai_system.agent.tools')


class NoResearchProgress(RuntimeError):pass


@dataclass
class ModelRequest:
    messages: list
    tools: list
    system_message: SystemMessage

    def override(self, **changes):
        return replace(self, **changes)


def pending_calls(messages):
    pending=[]
    for message in messages:
        if isinstance(message,AIMessage):
            pending.extend({**call,'_message_id':message.id} for call in message.tool_calls)
        elif isinstance(message,ToolMessage):
            match=next((call for call in pending if call['id']==message.tool_call_id),None)
            if match:pending.remove(match)
    return pending


def observed_messages(messages):
    pending={call['_message_id'] for call in pending_calls(messages)}
    for index,message in enumerate(messages):
        if isinstance(message,AIMessage) and message.id in pending:
            return messages[:index]
    return messages


def skipped_calls(calls, reason):
    return [ToolMessage(content=reason, tool_call_id=call['id'], name=call['name'],
                        status='error', additional_kwargs={'skipped': True}) for call in calls]


class AgentLoop:
    def __init__(self, model, tools, system_prompt, *, ctx, context=None, prepare=None,
                 execute=None, finish=None):
        self.model,self.tools,self.system_prompt=model,tools,system_prompt
        self.ctx,self.context=ctx,context
        self.prepare,self.execute,self.finish=prepare,execute,finish
        self.tool_map={tool.name:tool for tool in tools if hasattr(tool,'name')}
        self.parallel_tools = {name for name, tool in self.tool_map.items()
                               if (getattr(tool, 'metadata', None) or {}).get('access') == 'read'}

    async def _execute(self, call, *, recover_only=False, queued_at=None):
        if self.execute:
            return await self.execute(call, recover_only=recover_only, queued_at=queued_at)
        from .runtime import call_tool, recover_tool
        tool=self.tool_map.get(call['name'])
        native={**call,'id':call['_message_id']+':'+call['id']}
        if recover_only:
            result=await recover_tool(self.ctx,native)
            if result is not None:return result.model_copy(update={'tool_call_id':call['id']})
            return ToolMessage(content='新用户消息已到达，此调用尚未提交，未执行。',
                               tool_call_id=call['id'],name=call['name'],status='error')
        if tool is None:
            return ToolMessage(content='未知工具：'+call['name'],tool_call_id=call['id'],name=call['name'],status='error')
        access = 'read' if call['name'] in self.parallel_tools else 'write'
        result=await call_tool(self.ctx,native,lambda:tool.ainvoke(call['args']),access=access,queued_at=queued_at)
        return result.model_copy(update={'tool_call_id':call['id']})

    async def _run_batch(self, store, session, history, calls):
        queued_at = monotonic()
        try:
            for parallel, group in groupby(calls, key=lambda call: call['name'] in self.parallel_tools):
                if parallel:
                    # ToolGates admits four readers and advances waiting calls as slots free up.
                    async with asyncio.TaskGroup() as tasks:
                        pending = [tasks.create_task(self._execute(call, queued_at=queued_at)) for call in group]
                    results = [task.result() for task in pending]
                    store.append_messages(session, results)
                    history.extend(results)
                    continue
                for call in group:
                    result = await self._execute(call, queued_at=queued_at)
                    if result.additional_kwargs.get('agent_stop'):
                        return call, result
                    store.append_message(session, result)
                    history.append(result)
        finally:
            logger.info('Tool batch session=%s message_id=%s calls=%d wall_seconds=%.6f',
                        session, calls[0]['_message_id'], len(calls), monotonic()-queued_at)

    async def run(self, session, messages, *, on_resume=None, new_user_turn_id=None):
        key=(str(self.ctx.research.root),session)
        owner=_owners.get(key)
        if owner is None:
            owner=asyncio.Lock();_owners[key]=owner
        async with owner:
            return await self._run(session,messages,on_resume=on_resume,new_user_turn_id=new_user_turn_id)

    async def _run(self, session, messages, *, on_resume=None, new_user_turn_id=None):
        from ai_system.config.generation import ensure_complete
        from ai_system.agent.commentary import publish_progress
        from ai_system.agent.prompt import ContextManager
        store=AgentStore(self.ctx.research.root)
        history=store.messages(session)
        resumed=bool(history)
        seen={message.id for message in history}
        incoming=[message for message in (messages[-1:] if resumed else messages) if not message.id or message.id not in seen]
        saved=store.session(session)
        pending = pending_calls(history)
        if pending and saved and saved['status'] in {'paused', 'waiting'}:
            # Older sessions left the remainder of a stopped batch pending.
            skipped = skipped_calls(pending, '已暂停或等待用户输入，本次后续调用未执行。')
            store.append_messages(session, skipped)
            history.extend(skipped)
            pending = []
        if resumed and on_resume:on_resume(bool(pending))
        if resumed and not incoming and saved and saved['status']=='completed':
            return {'messages':history,'status':'completed'}
        self.ctx.agent_messages=observed_messages(history)
        if resumed and incoming:
            for call in pending:
                result=await self._execute(call,recover_only=True)
                store.append_message(session,result);history.append(result)
        for message in incoming:
            store.append_message(session,message);history.append(message)
        context=self.context or ContextManager(self.ctx,self.model,session,pinned={})
        store.set_session(session,'running')
        last_finish_feedback=None
        async def complete(response):
            nonlocal last_finish_feedback
            feedback=await self.finish(response,history) if self.finish else None
            if feedback:
                if feedback==last_finish_feedback:raise NoResearchProgress(feedback)
                last_finish_feedback=feedback
                note=HumanMessage(content=feedback)
                store.append_message(session,note);history.append(note)
                return None
            store.set_session(session,'completed')
            return {'messages':history,'status':'completed'}
        try:
            if resumed and not incoming and isinstance(history[-1],AIMessage) and not history[-1].tool_calls and not history[-1].additional_kwargs.get('tool_diagnostic'):
                done=await complete(history[-1])
                if done:return done
            while True:
                self.ctx.agent_messages=observed_messages(history)
                calls = pending_calls(history)
                if calls:
                    last_finish_feedback=None
                    stopped = await self._run_batch(store, session, history, calls)
                    if stopped:
                        call, result = stopped
                        stop = result.additional_kwargs['agent_stop']
                        remaining = calls[calls.index(call)+1:]
                        if stop == 'complete':
                            skipped = skipped_calls(remaining, '已申请结束，本次后续调用未执行。')
                            response = AIMessage(content=call['args'].get('reason') or call['args'].get('title') or '交付文件已保存。')
                            store.append_messages(session, [result, *skipped, response])
                            history.extend([result, *skipped, response])
                            done = await complete(response)
                            if done:return done
                        else:
                            skipped = skipped_calls(remaining, '已暂停或等待用户输入，本次后续调用未执行。')
                            store.append_messages(session, [result, *skipped], status=stop, reason=result.content)
                            history.extend([result, *skipped])
                            return {'messages': history, 'status': stop}
                seen={message.id for message in history}
                for message in list(history):
                    diagnostic=message.additional_kwargs.get('tool_diagnostic') if isinstance(message,AIMessage) else None
                    identity='diagnostic:'+message.id
                    if diagnostic and identity not in seen:
                        note=HumanMessage(content=diagnostic,id=identity)
                        store.append_message(session,note);history.append(note)
                if self.prepare:
                    for message in await self.prepare(history):
                        store.append_message(session,message);history.append(message)
                for message in context.notes(history):
                    store.append_message(session,message);history.append(message)
                system=self.system_prompt if isinstance(self.system_prompt,SystemMessage) else SystemMessage(content=self.system_prompt)
                request=ModelRequest(history,self.tools,system)
                async def invoke(request):
                    from langchain_core.messages import message_to_dict, messages_to_dict
                    from langchain_core.utils.function_calling import convert_to_openai_tool
                    from ai_system.core_handlers.model_runtime import model_identity
                    effective=getattr(context,'last_request',None) or request
                    snapshot={'system':message_to_dict(effective.system_message),
                              'messages':messages_to_dict(effective.messages),
                              'tools':[convert_to_openai_tool(t) for t in effective.tools],
                              'model_identity':model_identity(self.model)}
                    store.save_request(session,snapshot)
                    self.ctx.agent_model=self.model
                    model=self.model.bind_tools(request.tools) if request.tools else self.model
                    return await model.ainvoke([request.system_message,*request.messages],config={'callbacks':MODEL_CALLBACKS.get(),'metadata':{'agent_session':session}})
                response=await context.call(request,invoke)
                ensure_complete(response)
                invalid=response.invalid_tool_calls
                if invalid:
                    # Invalid arguments never enter the executable tool-call journal.
                    response=response.model_copy(update={'invalid_tool_calls':[],
                        'additional_kwargs':{**{key:value for key,value in response.additional_kwargs.items() if key!='tool_calls'},
                            'tool_diagnostic':'工具参数不是有效JSON，请纠正本次调用，不要重做已有计算：'+json.dumps(invalid,ensure_ascii=False)}})
                store.append_message(session,response);history.append(response)
                await publish_progress(self.ctx.events,response,scope=session)
                if invalid:
                    continue
                if response.tool_calls:continue
                done=await complete(response)
                if done:return done
        except BaseException as exc:
            store.set_session(session,'interrupted' if isinstance(exc,asyncio.CancelledError) else 'failed',type(exc).__name__)
            raise


async def run_session(agent, session, messages, *, ctx=None, on_resume=None, new_user_turn_id=None):
    """UI turns share the same engine and message store as the lead agent."""
    from .protocol import MessageItem
    from ai_system.agent.commentary import public_progress
    import time
    parent=CURRENT_TURN.get()
    child=CURRENT_TOOL.get() is not None
    if parent is None or child:
        with session_turn(ctx or agent.ctx,session) as turn:
            if child and messages:
                entry=MessageItem(id=uuid4().hex,thread_id=turn.thread_id,turn_id=turn.turn_id,
                    type='userMessage',text=str(messages[-1].content),started_at=time.time())
                turn.put(entry.model_dump(),'item/started')
                entry.status='completed';entry.finished_at=time.time()
                turn.put(entry.model_dump(),'item/completed')
            result=await agent.run(session,messages,on_resume=on_resume,new_user_turn_id=new_user_turn_id)
            if child:
                text='\n\n'.join(public_progress(result['messages'][-1].content))
                entry=MessageItem(id=uuid4().hex,thread_id=turn.thread_id,turn_id=turn.turn_id,
                    type='agentMessage',phase='final_answer',text=text,started_at=time.time())
                turn.put(entry.model_dump(),'item/started')
                entry.status='completed';entry.finished_at=time.time()
                turn.put(entry.model_dump(),'item/completed')
            return result
    return await agent.run(session,messages,on_resume=on_resume,new_user_turn_id=new_user_turn_id)
