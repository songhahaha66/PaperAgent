"""Continue an effective parent request without reusing its tool closures."""
import json
import re
import shutil
from copy import deepcopy

from langchain_core.messages import HumanMessage, ToolMessage, messages_from_dict

from .context import CURRENT_TURN
from .store import AgentStore


ALLOWED_CAPABILITIES=frozenset({
    'research.list_files','research.search_files','research.read_artifact',
    'research.materialize_artifact','research.run_python','research.run_file',
    'research.list_skills','research.read_skill','workspace.edit','literature.collect',
})
BOOTSTRAP='.system/fork-request.json'
CONTEXT_FILE=re.compile(r'\.system/(?:context-outputs/[a-f0-9]{64}\.json|paper-project/previews/[a-f0-9]{64}/[1-9][0-9]*\.png)')
PREAMBLE=(
    '本次运行是主Agent刚刚委派的fork子任务。前面的消息是背景，委派已经发生，不要再次委派。'
    '只完成下面的task；主Agent负责总体计划、论文、用户交互和最终交付。'
    '工具声明与主Agent保持一致，但执行权限受限：只能读取本任务材料、检索、运行计算，'
    '以及修改本任务工作区的code/和config/；不能修改主论文、全局研究状态或调用其他子Agent。'
    '历史中未分配的文件不保证可用，以本任务文件工具返回为准；Python使用独立环境。'
    '返回范围内的结论、文件、校验、失败和未知，然后结束；需要额外信息时将问题交回主Agent。\n\n'
)


def _copy_context_files(parent, child, snapshot):
    """Copy only immutable refs already visible in the inherited request."""
    pending=list(CONTEXT_FILE.findall(json.dumps(snapshot,ensure_ascii=False)))
    seen=set()
    while pending:
        name=pending.pop()
        if name in seen:continue
        seen.add(name)
        target=child.fs.abs(name)
        if not target.is_file():
            source=parent.fs.abs(name)
            if not source.is_file():raise ValueError('fork上下文引用已不可用：'+name)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        if name.endswith('.json'):
            pending.extend(CONTEXT_FILE.findall(target.read_text(encoding='utf-8')))


def prepare_fork(parent, child, session):
    """Freeze once; follow-ups and crash recovery keep this task's own prefix."""
    from ai_system.config.generation import request_model
    from ai_system.core_handlers.model_runtime import model_identity
    if child.fs.exists(BOOTSTRAP):
        snapshot=json.loads(child.fs.read_text(BOOTSTRAP))
    else:
        if AgentStore(parent.research.root).messages(session):
            raise ValueError('旧fork会话没有完整请求前缀快照，请创建新子任务。')
        turn=CURRENT_TURN.get()
        snapshot=AgentStore(parent.research.root).request(turn.thread_id) if turn else None
        if snapshot is None:raise ValueError('fork需要主Agent实际模型请求的前缀快照，请由主会话委派新任务。')
        snapshot=deepcopy(snapshot)
        _copy_context_files(parent,child,snapshot)
        child.fs.write_text(BOOTSTRAP,json.dumps(snapshot,ensure_ascii=False))
    model=getattr(parent,'agent_model',None) or request_model(parent.planner_llm)
    if model_identity(model)!=snapshot['model_identity']:
        raise ValueError('fork原模型连接或生成配置已变化，请恢复原配置继续，或创建新子任务。')
    child.allowed_capabilities=ALLOWED_CAPABILITIES
    system=messages_from_dict([snapshot['system']])[0]
    history=messages_from_dict(snapshot['messages'])
    return model,system,snapshot['tools'],history


def task_message(task, files, aliases, task_id):
    from ai_system.research.store import digest
    return HumanMessage(content=PREAMBLE+json.dumps({
        'role':'fork','task':task,'files':files,'local_paths':aliases,
        'allowed_tools':sorted(name.replace('.','_') for name in ALLOWED_CAPABILITIES),
    },ensure_ascii=False),id='fork-task:'+task_id+':'+digest(task))


def executor(ctx, tools, remember_run):
    """Keep schemas identical, but resolve every handler against the child ctx."""
    from .runtime import dispatch, ToolInvocation, recover_tool
    from ai_system.modules.registry import capabilities
    from ai_system.modules.file_edits import observed_version
    from ai_system.research.runtime import execution_scope
    from ai_system.research.review_cache import error_detail
    registry=capabilities()
    declared={tool['function']['name'] for tool in tools}
    mapping={c.name.replace('.','_'):c.name for c in registry.model_capabilities()}

    async def execute(call,*,recover_only=False,queued_at=None):
        name=mapping.get(call['name'],call['name'])
        args=call.get('args') or {}
        key='fork-tool:'+CURRENT_TURN.get().thread_id+':'+call['_message_id']+':'+call['id']
        native={**call,'id':key,'name':name}
        if recover_only:
            result=await recover_tool(ctx,native)
            if result is not None:return result.model_copy(update={'tool_call_id':call['id'],'name':call['name']})
            return ToolMessage(content='新用户消息已到达，尚未提交的调用未执行。',tool_call_id=call['id'],name=call['name'],status='error')
        if name=='workspace.edit':
            args={**args,'expected_version':observed_version(ctx.agent_messages,args.get('path',''))}
        async def perform():
            if call['name'] not in declared:raise ValueError('工具不在继承的声明中：'+call['name'])
            before=set(ctx.fs._manifest.get('runs',{}))
            try:
                if name=='literature.collect':
                    from ai_system.research.literature import LiteratureService
                    from ai_system.modules.registry import CapabilityProduct
                    values=capability.inputs.model_validate(args).model_dump()
                    return CapabilityProduct('literature',await LiteratureService(ctx.research.root).search(**values))
                return await registry.execute(name,args,ctx)
            finally:
                for rid in ctx.fs._manifest.get('runs',{}).keys()-before:remember_run(rid)
        capability=registry.get(name)
        try:
            with execution_scope(action_id=key):
                result=await dispatch(ctx,ToolInvocation(name,key,args,capability.access if capability else 'write'),
                                      perform,queued_at=queued_at)
            content=result.value if isinstance(result.value,(str,list)) else json.dumps(result.value,ensure_ascii=False)
            return ToolMessage(content=content,tool_call_id=call['id'],name=call['name'],status='success' if result.status=='completed' else 'error')
        except (ValueError,KeyError,FileNotFoundError) as exc:
            return ToolMessage(content=error_detail(exc),tool_call_id=call['id'],name=call['name'],status='error')
    return execute
