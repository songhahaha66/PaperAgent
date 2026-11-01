"""Continuous Python execution, saved files and a plain research report.

The host records code, output and artifacts. The model never registers evidence
or satisfies a second result-inventory contract before returning its findings.
"""


import ast
import json
import logging
import re
from typing import Callable, Dict, List, Optional

from langchain_core.messages import messages_to_dict
from langchain_core.tools import StructuredTool, ToolException

from ai_system.graph.context import GraphContext
from ai_system.graph.llm_utils import clip


logger = logging.getLogger(__name__)

OBSERVATION_CHARS = 6000


# 网关的工具调用解析偶尔把模型的对话模板标记（DeepSeek 的 <｜DSML｜…>、</think>）连同后面的文字
# 一起塞进代码参数，整段执行时 SyntaxError；2024_C 一次运行 30 次执行里有 4 次这样浪费掉
TEMPLATE_TOKEN = re.compile(r"</?｜|｜DSML｜|</think>|<think>")

# v2h/v2k 的返工阶段各有 30~40 次 print('x') / print('ok') 这种空执行：每次占一步预算、一次模型往返
# 前面带几行 import 的也算（v3 的 2022_C Q3："import pandas as pd, numpy as np\nprint('q3')" 执行了十几次）
PLACEHOLDER_REPLY = (
    "这段代码只包含占位语句或固定字符串，没有执行。不需要用 run_python 占位或确认："
    "已有结果直接用于回答，无需再次执行或登记。"
)


def is_placeholder(code: str) -> bool:
    try:
        body = ast.parse(code).body
    except SyntaxError:
        return False  # Let execution return the real syntax error.
    def empty(node):
        if isinstance(node, ast.Pass):
            return True
        if not isinstance(node, ast.Expr):
            return False
        value = node.value
        if isinstance(value, ast.Constant):
            return True
        return (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == 'print' and not value.keywords and len(value.args) == 1
                and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str)
                and re.fullmatch(r'[\w\s.!-]{0,12}', value.args[0].value) is not None)
    return not body or (any(empty(node) for node in body) and all(
        empty(node) or isinstance(node, (ast.Import, ast.ImportFrom)) for node in body))


def strip_template_tokens(code: str) -> str:
    """截掉代码里第一个对话模板标记及其之后的内容"""
    match = TEMPLATE_TOKEN.search(code)
    return code[: match.start()].rstrip() if match else code


def solution_code_path(subproblem_id: str) -> str:
    return f"code/{subproblem_id}_solution.py"


def export_solution_code(ctx: GraphContext, subproblem_id: str, run_log: List[Dict]) -> None:
    """Export actual executed statements in order, retaining useful failed-cell prefixes.

    The main research session shares one kernel. Saved code also lets a resumed
    session reuse functions without assuming that earlier memory survived.
    """
    runs = []
    for run in run_log:
        code = run.get("code") or ""
        if is_placeholder(code):
            continue
        if not run.get("ok"):
            # A failed cell can already have imported modules or defined values
            # subsequently used by successful cells. Retain only complete
            # top-level statements before the actual failing statement.
            info = ctx.fs.get_run_info(run["run_id"])
            if not info or not info.get("stdout"):
                continue
            trace = ctx.fs.read_text(info["stdout"]).rsplit("Traceback (most recent call last):\n", 1)[-1]
            frame = re.search(r'(?:File "<cell>", line |Cell In\[\d+\], line )(\d+)', trace)
            if not frame:
                continue
            try:
                nodes = ast.parse(code).body
            except SyntaxError:
                continue
            prefix = [node for node in nodes if node.end_lineno < int(frame[1])]
            code = "\n".join(code.splitlines()[:prefix[-1].end_lineno]) if prefix else ""
        if code.strip():
            runs.append({**run, "code": code})
    if not runs:
        return
    body = "\n\n".join(f"# ---- {r['run_id']} ----\n{r['code'].rstrip()}" for r in runs)
    header = f"# {subproblem_id} 已执行代码（按顺序，含探索及出错语句之前的完整前缀）。复用时复制需要的函数和处理步骤。\n\n"
    images = sorted({info['runtime_image'] for run in runs
        if (info := ctx.fs.get_run_info(run['run_id']) or {}).get('runtime_image')})
    if images:
        header += '# 原执行环境镜像：' + '、'.join(images) + '\n# 在对应依赖环境中运行；不同库版本可能存在 API 差异。\n\n'
    if 'matplotlib' in body:
        from inspect import getsource
        from ai_system.sandbox.runtime_helpers import choose_font
        header += ('import matplotlib\nmatplotlib.use("Agg")\nfrom matplotlib.font_manager import fontManager\n\n'
            + getsource(choose_font) + '\nmatplotlib.rcParams["font.sans-serif"] = [choose_font({f.name for f in fontManager.ttflist})]\n'
            + 'matplotlib.rcParams["axes.unicode_minus"] = False\n\n')
    try:
        ctx.fs.write_text(solution_code_path(subproblem_id), header + body)
    except Exception as exc:  # 只是给后续子问题的参考，失败不影响本问
        logger.warning("保存 %s 的代码失败: %s", subproblem_id, exc)


async def publish_outputs(ctx, run_ids):
    """Publish and index the same saved run for initial delivery and recovery."""
    from ai_system.sandbox.artifacts import capture_run_files
    paths=capture_run_files(ctx.fs,ctx.fs,run_ids=run_ids)
    store=ctx.research
    if store and any(set(run_ids).intersection(a.get('run_ids', [])) for a in store.snapshot()['executions'].values()):
        store.sync_execution_files(run_ids)
    for rid in run_ids:
        info=ctx.fs.get_run_info(rid) or {}
        if info.pop('publication_error', None):ctx.fs._save_manifest()
    for path in paths:
        await ctx.events.file_changed(path)
    return paths


async def recover_outputs(ctx):
    """Finish outstanding root-session delivery before research or writing resumes."""
    snapshot=ctx.research.snapshot()
    owned={rid for attempt in snapshot['executions'].values() if attempt.get('workspace')=='.'
           for rid in attempt.get('run_ids',[])}
    legacy = set(owned)
    from ai_system.research.runs import direct_runs
    owned.update(direct_runs(ctx.research, ctx.fs))
    if not owned:return
    inventory=ctx.fs._manifest.get('runs',{})
    pending={rid for rid in owned if (inventory.get(rid) or {}).get('delivery_error') or
             ((inventory.get(rid) or {}).get('execution_mode') == 'process' and
              ((inventory.get(rid) or {}).get('submission_state') in {'submitting', 'unknown'} or
               (inventory.get(rid) or {}).get('status') in {'starting', 'running'}))}
    if pending:
        from ai_system.research.views import activity
        with activity(ctx,'retry_outputs'):
            kernel=ctx.kernels.get('research')
            for _ in pending:
                run_id=await kernel.retry_outputs()
                if run_id:
                    await publish_outputs(ctx,[run_id])
                    owned.discard(run_id)
    indexed={a['payload'].get('path') for a in snapshot['artifacts'].values()}
    missing=[]
    for rid in owned:
        info=inventory.get(rid) or {}
        if not info.get('evidence_archived') or info.get('status')!='success':continue
        paths=['outputs/research/'+rid+'/'+path.split('/artifacts/',1)[1] for path in info.get('artifacts',[])]
        if (any(not ctx.fs.get_file_meta(path) or not ctx.fs.exists(path) for path in paths)
                or (rid in legacy and any(path not in indexed for path in info.get('artifacts',[])))):missing.append(rid)
    if missing:await publish_outputs(ctx,missing)


def build_tools(
    ctx: GraphContext,
    subproblem_id: str,
    run_log: Optional[List[Dict]] = None,
    kernel_key: Optional[str] = None,
    on_run: Optional[Callable[[str], None]] = None,
) -> List[StructuredTool]:
    """Execution and file tools; no evidence-registration side channel."""
    kernel = ctx.kernels.get(kernel_key or subproblem_id)
    run_log = run_log if run_log is not None else []

    async def read_file(path: str, start: int = 0, limit: int = 12000) -> str:
        """读取工作区已有文本文件，不重跑计算；使用start/limit分段读取。"""
        if start < 0 or not 1 <= limit <= 24000:return '读取范围无效'
        from ai_system.research.views import activity
        with activity(ctx,'read_file',subproblem_id,display_file=path,inputs={'path':path,'start':start,'limit':limit}) as operation:
            if ctx.fs.abs(path).suffix.lower() in {'.png', '.jpg', '.jpeg', '.pdf', '.xlsx', '.zip'}:
                return f'这是二进制文件，可从文件面板预览或下载：{path}；不要按文本读取，也不需要重新生成。'
            text=ctx.fs.read_text(path)
            operation['output']={'total_chars':len(text),'excerpt':text[start:start+limit]}
            return text[start:start+limit]

    async def search_literature(query: str, information_need: str = "") -> str:
        """为当前实验的明确信息需求检索文献；遵守项目外部检索设置，保存来源。"""
        from ai_system.research.citations import literature_root
        from ai_system.research.literature import LiteratureStore, LiteratureService
        from ai_system.research.views import activity
        root=literature_root(ctx)
        if LiteratureStore(root).policy()=='off':return '项目已关闭外部检索。'
        with activity(ctx,'literature.collect',subproblem_id) as operation:
            operation['inputs']={'query':query,'information_need':information_need}
            value=await LiteratureService(root).search(query,limit=3,fetch_open_access=True)
            operation['output']=value
            return json.dumps(value,ensure_ascii=False)

    async def run_python(code: str, title: str = '') -> str:
        """在沙箱独立 Python 进程中执行代码，返回落盘日志、错误和自动保存的图片。

        每次执行不保留内存变量；复用逻辑请保存到 code/ 并 import，复用结果请读取已保存文件。

        title: 给用户看的简短操作名称，如“计算线性趋势预测”或“保存预测结果表”。说明代码实际用途，不含内部编号。可省略。
        """
        code = strip_template_tokens(code)
        if is_placeholder(code):
            return PLACEHOLDER_REPLY
        from ai_system.research.views import activity
        publication_note = ''
        with activity(ctx,'run_python',subproblem_id,reuse_parent='research.run_python',
                      inputs={'code':code},display_label=' '.join(title.split())[:60],phase='preparing') as operation:
            import time
            from ai_system.research.views import activity_progress
            last_push = 0.0
            async def progress(event):
                nonlocal last_push
                operation['run_id'] = event['run_id']
                operation['phase'] = 'delivery' if event['type']=='finished' else 'compute' if event['type'] in {'running','output'} else 'preparing'
                if on_run:on_run(event['run_id'])
                output = operation.setdefault('output', {})
                if event['type'] == 'output':
                    output['output_tail'] = (output.get('output_tail', '') + event['text'])[-6000:]
                now = time.monotonic()
                if event['type'] != 'output' or now - last_push >= 1:
                    activity_progress(ctx, operation)
                    last_push = now
            # A pending transfer is retried before accepting another computation.
            if getattr(kernel,'pending_run',None):
                recovered=await kernel.retry_outputs()
                if recovered:await publish_outputs(ctx,[recovered])
            result = await kernel.execute(code, on_event=progress)
            operation['phase'] = 'delivery'
            if on_run:on_run(result.run_id)
            if result.delivery_error and hasattr(kernel,'retry_outputs'):
                for _ in range(2):
                    try:
                        await kernel.retry_outputs()
                        result.delivery_error=None
                        info=ctx.fs.get_run_info(result.run_id) or {}
                        result.files=info.get('files',result.files)
                        if info.get('execution_mode') == 'process' and info.get('process_receipt'):
                            result.ok = info.get('status') == 'success'
                            result.error = info.get('error')
                            result.timed_out = info.get('status') == 'timeout'
                            result.failure_kind = info.get('failure_kind')
                            result.exit_code = info.get('exit_code')
                            result.duration_seconds = info.get('duration_seconds')
                            logs = [(ctx.fs.run_dir(result.run_id) / (name + '.log')).read_text(encoding='utf-8')
                                    for name in ('stdout', 'stderr')]
                            result.stdout = '\n'.join(filter(None, logs))[-20000:]
                        break
                    except (OSError,ValueError,RuntimeError,TimeoutError):
                        pass

            # Publish the run before its terminal timeline event resolves file links.
            try:
                await publish_outputs(ctx,[result.run_id])
            except (OSError, ValueError) as exc:
                publication_note = f'文件交付未完成：{exc}。计算结果已保留，不要重算，系统将重试同步。'
                ctx.fs.get_run_info(result.run_id)['publication_error'] = publication_note
                ctx.fs._save_manifest()
            status='interrupted' if result.delivery_error or publication_note or result.failure_kind=='transport_unknown' else 'completed' if result.ok else 'failed'
            operation.update(run_id=result.run_id,status=status,
                output={**operation.get('output', {}),'output_tail':operation.get('output',{}).get('output_tail',result.stdout[-6000:]),
                        'ok':result.ok,'run_id':result.run_id, 'files':result.files, 'delivery_error': result.delivery_error, 'failure_kind': result.failure_kind})
            if result.delivery_error or publication_note:
                operation.update(error_type='DeliveryIncomplete',message='计算已结束，文件交付未完成；已有结果保留，请重试文件同步。')
            if result.recovery_note:
                operation['message'] = result.recovery_note
            if not result.ok:
                operation.update(error_type='TimeoutError' if result.timed_out else 'SandboxLost' if result.failure_kind=='sandbox_lost' else 'PythonExecutionError',
                                 message=result.error or result.recovery_note)
        run_log.append({"run_id": result.run_id, "code": code, "ok": result.ok,
                        "delivery_error":result.delivery_error or publication_note})
        await ctx.events.emit(
            "paper_code",
            f"{subproblem_id} · {result.run_id} · {'成功' if result.ok else '失败'}",
        )
        observation = clip(result.to_observation(), OBSERVATION_CHARS)
        if publication_note: observation += '\n' + publication_note
        if result.failure_kind == 'sandbox_lost':
            return observation
        if result.failure_kind == 'transport_unknown':
            return observation + '\n连接中断，执行是否完成无法确认。保留已收到输出，提交阻碍交回研究层；不要自动重放相同代码。'
        if result.failure_kind == 'delivery':
            return observation
        if not result.ok:
            return observation + "\n[执行失败] 代码未成功完成。请阅读回溯并修复，或在报告中说明未完成部分。"
        return observation

    async def retry_outputs() -> str:
        """查询原后台任务并补读日志和文件，不重新执行代码。"""
        if not hasattr(kernel, 'retry_outputs'):
            return '当前执行器没有待同步的远端文件。'
        run_id = await kernel.retry_outputs()
        if not run_id:
            return '没有待同步文件，直接使用已有输出。'
        files=await publish_outputs(ctx,[run_id])
        return '文件同步完成：' + str(files)

    from functools import wraps
    def make_tool(fn):
        @wraps(fn)
        async def run(*args,**kwargs):
            try:return await fn(*args,**kwargs)
            except (ValueError,KeyError,FileNotFoundError) as exc:
                from ai_system.research.review_cache import error_detail
                raise ToolException(error_detail(exc)) from exc
        return StructuredTool.from_function(coroutine=run,name=fn.__name__,handle_tool_error=True,handle_validation_error=str)
    tools = [make_tool(run_python),make_tool(read_file),make_tool(search_literature),make_tool(retry_outputs)]
    return tools


def save_transcript(ctx: GraphContext, name: str, messages: List) -> None:
    """求解对话落盘（.system/transcripts/），读 trace 时能看到每一步模型写了什么、调用了什么工具"""
    try:
        ctx.write_json(f".system/transcripts/{name}.json", messages_to_dict(messages))
    except Exception as exc:  # 只是排查用的记录，失败不影响求解
        logger.warning("保存 %s 的求解对话失败: %s", name, exc)
