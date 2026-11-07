"""One tool dispatch path: identity, concurrency, receipts and Item lifecycle.

Handlers return ToolResult to the model. Only this runtime creates tool Items;
helpers may update the current invocation's progress but cannot add UI rows.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import inspect
import json
import time
from uuid import uuid4
from .context import CURRENT_TOOL, CURRENT_TURN, item_id, session_turn
from .protocol import PlanItem, ToolItem
from .projection import tool_projection, safe_diagnostic


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    call_id: str
    arguments: dict
    access: str = 'write'


@dataclass
class ToolResult:
    value: object
    kind: str | None = None
    status: str = 'completed'
    message: str | None = None


class ToolGates:
    """Bound parallel readers; a writer excludes reads and other writes.

    Delegation has a separate limit: a parent awaiting a child must not hold the
    workspace write gate. Child workspaces have their own gates.
    """
    def __init__(self):
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.waiting_writers = 0
        self.delegates = asyncio.Semaphore(2)
        self.calls = {}

    @asynccontextmanager
    async def acquire(self, access):
        if access == 'delegate':
            async with self.delegates:
                yield
            return
        read = access == 'read'
        async with self.condition:
            if not read:self.waiting_writers += 1
            try:
                await self.condition.wait_for(lambda: not self.writer and (
                    self.readers < 4 and not self.waiting_writers if read else self.readers == 0))
                if read:self.readers += 1
                else:self.writer = True
            finally:
                if not read:
                    self.waiting_writers -= 1
                    self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                if read:self.readers -= 1
                else:self.writer = False
                self.condition.notify_all()


def gates(ctx):
    if getattr(ctx, 'tool_gates', None) is None:
        ctx.tool_gates = ToolGates()
    return ctx.tool_gates


class ToolExecution:
    def __init__(self, ctx, turn, invocation):
        from ai_system.research.runtime import execution_links
        from ai_system.modules.registry import display_inputs
        self.ctx, self.turn, self.invocation = ctx, turn, invocation
        tool_type = ('commandExecution' if invocation.name in {'research.run_python', 'run_python'} else
                     'fileChange' if invocation.name in {'writing.edit', 'writing.use_asset', 'writing.save_source'} else
                     'collabToolCall' if invocation.name == 'research.delegate' else 'toolCall')
        self.item = dict(id=item_id(turn.thread_id, turn.turn_id + ':' + invocation.call_id), thread_id=turn.thread_id,
                         turn_id=turn.turn_id, type=tool_type, call_id=invocation.call_id,
                         tool_name=invocation.name, status='running', started_at=time.time())
        self.metadata = {**execution_links(), **self.item, 'inputs': display_inputs(invocation.name, invocation.arguments)}
        self.metadata.pop('parent_call_id', None)
        self.metadata['question_id'] = invocation.arguments.get('question_id') or self.metadata.get('question_id')
        self.metadata['task_id'] = invocation.arguments.get('task_id') or invocation.arguments.get('experiment_id')
        if invocation.name == 'research.update_plan':
            self.item = PlanItem(**{k:self.item[k] for k in ('id','thread_id','turn_id','started_at')}).model_dump(exclude_none=True)

    def snapshot(self):
        if self.item['type'] == 'plan':
            return self.item.copy()
        research = getattr(self.ctx, 'research', None)
        row = tool_projection(self.turn.store.root,
                              research.research_id if research else None, self.metadata,
                              fs=getattr(self.ctx, 'fs', None))
        for key in ('child_thread_id','child_turn_id'):
            if self.metadata.get(key):row[key] = self.metadata[key]
        return ToolItem(**{**self.item, **row}).model_dump(mode='json', exclude_none=True)

    def update(self, **changes):
        if 'output' in changes:
            changes['output'] = {**self.metadata.get('output', {}), **changes['output']}
        self.metadata.update(changes)
        if self.item['type'] == 'plan':
            self.item.update({k:v for k,v in changes.items() if k in {'steps','explanation'}})
        # A helper can record an unsuccessful result, but only the dispatcher
        # closes the Item after its return value/exception has been reconciled.
        snapshot = self.snapshot()
        snapshot['status'] = 'running'
        snapshot.pop('finished_at', None)
        self.turn.put(snapshot, 'item/updated')

    def finish(self, status, *, receipt=None):
        self.metadata.update(status=status, finished_at=time.time())
        self.item.update(status=status, finished_at=self.metadata['finished_at'])
        return self.turn.put(self.snapshot(), 'item/completed', receipt=receipt)


def update_tool(ctx, **changes):
    current = CURRENT_TOOL.get()
    if current and current.ctx is ctx:
        current.update(**changes)


def tool_metadata(ctx, **output):
    update_tool(ctx, output=output)


def _result(value):
    from ai_system.modules.registry import CapabilityProduct
    if isinstance(value, ToolResult):return value
    if isinstance(value, CapabilityProduct):
        result = ToolResult(value.value, value.kind, value.execution_status or 'completed', value.execution_message)
    else:
        result = ToolResult(value)
    data = result.value
    if isinstance(data, dict):
        state = data.get('execution_status')
        if state in {'failed','cancelled','interrupted'}:result.status = 'interrupted' if state == 'cancelled' else state
        if data.get('research_blocked'):
            result.status, result.message = 'failed', data.get('message')
    return result


async def _reconcile(ctx, invocation):
    """A submitted process is a receipt even if its model response was lost."""
    if invocation.name not in {'research.run_python','run_python'}:return None
    run = next(((rid, info) for rid, info in ctx.fs._manifest.get('runs', {}).items()
                if info.get('tool_call_id') == invocation.call_id), None)
    if not run:return None
    rid, info = run
    if invocation.name == 'research.run_python':
        from ai_system.research.runs import run_result
        value = {**run_result(ctx, rid), 'recovered': True, 'note': '复用已有运行回执；未重放代码。执行未知时先核实日志。'}
        result = _result(value)
        result.kind = 'research_state'
        return result
    output = ctx.fs.read_text(info['stdout']) if info.get('stdout') and ctx.fs.exists(info['stdout']) else ''
    status = 'completed' if info.get('status') == 'success' else 'failed' if info.get('status') in {'error','failed','timeout'} else 'interrupted'
    update_tool(ctx, run_id=rid, output={'output_tail':output[-6000:]})
    return ToolResult('复用已有运行 '+rid+'，未重放代码。状态：'+str(info.get('status','unknown'))+'\n'+output[-6000:], status=status)


async def dispatch(ctx, invocation, handler):
    """All model and scheduler calls enter here; raw helper calls stay internal."""
    if CURRENT_TURN.get() is None:
        research = getattr(ctx, 'research', None)
        with session_turn(ctx, (research.research_id if research else 'workspace')+'-lead'):
            return await dispatch(ctx, invocation, handler)
    turn = CURRENT_TURN.get()
    from ai_system.research.store import digest
    from ai_system.research.runtime import execution_scope
    from ai_system.modules.registry import display_output
    signature = digest([invocation.name, invocation.arguments])
    limits = gates(ctx)
    # Per-call serialization also covers parallel duplicate deliveries.
    lock = limits.calls.setdefault((turn.thread_id, invocation.call_id), asyncio.Lock())
    async with lock:
        turn.store.bind_call(turn.thread_id, invocation.call_id, signature)
        saved = turn.store.receipt(turn.thread_id, invocation.call_id, signature)
        if saved is not None:return ToolResult(**saved)
        operation = ToolExecution(ctx, turn, invocation)
        existing = turn.store.get_item(operation.item['id'])
        if existing:
            # A crash after submission but before the terminal transaction is
            # recovered from the run manifest below, never by replaying code.
            operation.item.update({k:existing[k] for k in ('id','turn_id','started_at','sequence')})
        else:
            turn.put(operation.snapshot(), 'item/started')
        token = CURRENT_TOOL.set(operation)
        try:
            async with limits.acquire(invocation.access):
                with execution_scope(tool_call_id=invocation.call_id, parent_call_id=operation.item['id'],
                                     events=getattr(ctx,'events',None), research_id=getattr(getattr(ctx,'research',None),'research_id',None)):
                    result = await _reconcile(ctx, invocation)
                    if result is None:
                        value = handler()
                        result = _result(await value if inspect.isawaitable(value) else value)
                    if isinstance(result.value, dict):
                        operation.metadata['output'] = {**operation.metadata.get('output',{}), **display_output(invocation.name, result.value)}
                        for name in ('run_id','question_id','experiment_id'):
                            if result.value.get(name):operation.metadata[name] = result.value[name]
                        if operation.item['type'] == 'plan':
                            plan = result.value.get('progress_plan') or {}
                            operation.item['steps'] = [{'step':s['title'],'status':s['status'],'note':s.get('note','')} for s in plan.get('steps',[])]
                    if operation.metadata.get('status') in {'failed','interrupted'}:
                        result.status = operation.metadata['status']
                    if result.status != 'completed':
                        operation.metadata.setdefault('error_type','ExecutionIncomplete')
                        operation.metadata.setdefault('message', result.message or '本次操作未完成，已有输出已保留。')
                    operation.finish(result.status, receipt=(invocation.call_id, signature, asdict(result)))
                    return result
        except BaseException as exc:
            from ai_system.research.review_cache import error_detail
            operation.metadata.update(error_type=type(exc).__name__, message=error_detail(exc))
            if operation.item['type']=='plan':operation.item['explanation']=safe_diagnostic(turn.store.root,error_detail(exc))
            operation.finish('interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed')
            raise
        finally:
            CURRENT_TOOL.reset(token)


async def invoke(ctx, registry, name, arguments, *, call_id=None):
    capability = registry.get(name)
    from ai_system.research.runtime import CURRENT_EXECUTION
    # Explicit model call IDs survive checkpoints; scheduled actions use their
    # durable action identity. Standalone invocations get a fresh identity.
    call_id = call_id or CURRENT_EXECUTION.get().get('tool_call_id') or uuid4().hex
    access = 'delegate' if name == 'research.delegate' else capability.access if capability else 'write'
    return await dispatch(ctx, ToolInvocation(name, call_id, arguments, access),
                          lambda: registry.execute(name, arguments, ctx))


async def call_tool(ctx, call, handler, *, access='write'):
    """Adapter for LangChain child/experiment tools; model formatting stays here."""
    from langchain_core.messages import ToolMessage
    async def run():
        message = await handler()
        if isinstance(message, ToolMessage):
            return ToolResult(message.content, status='failed' if message.status == 'error' else 'completed')
        return message
    try:
        result = await dispatch(ctx, ToolInvocation(call['name'], call['id'], call.get('args') or {}, access), run)
        content = result.value if isinstance(result.value, (str,list)) else json.dumps(result.value, ensure_ascii=False)
        return ToolMessage(content=content, tool_call_id=call['id'], name=call['name'], status='success' if result.status == 'completed' else 'error')
    except (ValueError, KeyError, FileNotFoundError) as exc:
        from ai_system.research.review_cache import error_detail
        return ToolMessage(content=error_detail(exc), tool_call_id=call['id'], name=call['name'], status='error')


async def reject_call(ctx, call_id, name, message):
    async def reject():raise ValueError(message)
    try:await dispatch(ctx, ToolInvocation(name, call_id, {}), reject)
    except ValueError:pass
