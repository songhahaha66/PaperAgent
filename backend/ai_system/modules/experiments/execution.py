"""Continuous Python execution, saved files and a plain research report.

The host records code, output and artifacts. The model never registers evidence
or satisfies a second result-inventory contract before returning its findings.
"""

from ai_system.graph.commentary import PROGRESS_PROMPT, ProgressMiddleware

import ast
import json
import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, messages_to_dict
from langchain_core.tools import StructuredTool, ToolException
from langgraph.errors import GraphRecursionError
from langgraph.runtime import Runtime

from ai_system.graph.compaction import (
    CompactHistoryMiddleware,
    DropRefusedStepsMiddleware,
    HistoryRecorder,
    ReportOrderingMiddleware,
    StepBudgetMiddleware,
    TruncationGuardMiddleware,
    compact_messages,
    drop_invalid_calls,
)
from ai_system.graph.context import GraphContext, list_data_files
from ai_system.graph.llm_utils import call_structured, clip
from ai_system.modules.methods import describe, skill_policy_prompt, allowed_method_keys
from ai_system.graph.state import BranchInput, SolveReport
from ai_system.graph.tracing import labelled, set_label


logger = logging.getLogger(__name__)

SYSTEM = """你是数学建模研究的编程手，用 Python 实现当前问题并核对结果。
使用 run_python 读取材料、计算和绘图；内核持久，复用已有变量和文件。
每次执行有意义的完整代码，阅读输出或回溯后再继续。打印关键汇总，把完整序列、表格和中间数据保存到 outputs/intermediate/。
图片由执行器自动保存，直接使用工具返回的文件路径，不登记指标或图片、不创建R/F编号。
完成后用 SolveReport 说明实际方法、数值、单位、采用的假设、必要校验、局限和未完成部分。结果必须来自实际运行输出或文件，不编造。
按原题检查数据范围、时间粒度、功率/电量单位、边界约束及预测时可用信息。必要时在代码中用 assert 或独立复算验证；检查失败须修复或如实说明，不能把代码执行成功称为研究正确。
报告效果时给出适用的基线；没有做过统计检验就不声称统计显著。不要为交报告补做题外实验。
当前实验只回答指定问题，整篇论文由后续写作与编译生成；保存计算文件和图即可。
图的标题、坐标轴和图例用中文，使用系统已有中文字体。
继续实验先读取已有代码和结果文件，不假定旧内存仍在，不重放已完成计算。
"""

OBSERVATION_CHARS = 6000
FINAL_NUDGE = "本轮执行预算已用完。根据实际输出和结果文件提交报告，明确未完成部分，不再执行代码。"
REPORT_NUDGE = "已有足够结果或遇到实质阻碍时，调用 SolveReport 说明数值、结果文件、校验与局限。"


# 网关的工具调用解析偶尔把模型的对话模板标记（DeepSeek 的 <｜DSML｜…>、</think>）连同后面的文字
# 一起塞进代码参数，整段执行时 SyntaxError；2024_C 一次运行 30 次执行里有 4 次这样浪费掉
TEMPLATE_TOKEN = re.compile(r"</?｜|｜DSML｜|</think>|<think>")

# v2h/v2k 的返工阶段各有 30~40 次 print('x') / print('ok') 这种空执行：每次占一步预算、一次模型往返
# 前面带几行 import 的也算（v3 的 2022_C Q3："import pandas as pd, numpy as np\nprint('q3')" 执行了十几次）
PLACEHOLDER_REPLY = (
    "这段代码只包含占位语句或固定字符串，没有执行。不需要用 run_python 占位或确认："
    "已有结果直接提交 SolveReport，无需再次执行或登记。"
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
    """把本问成功执行过的代码按顺序存成一个文件，供依赖它的子问题复用

    每个子问题有自己的内核，后一问看不到前一问的函数和变量。2022_C 的 Q3 要"运行 Q2 选定的策略"，
    只拿到 Q2 的结论和数值，只好从读数据开始重写回测，日期解析又踩了一遍坑，171 条对话后交了空报告。
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
            frame = re.search(r'File "<cell>", line (\d+), in <module>', trace)
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


def _intermediate_files(ctx: GraphContext) -> List[str]:
    folder = ctx.workspace / "outputs" / "intermediate"
    if not folder.exists():
        return []
    return sorted(str(p.relative_to(ctx.workspace)) for p in folder.rglob("*") if p.is_file())


def _dependency_text(dependencies: Dict[str, Dict], ctx: GraphContext) -> str:
    if not dependencies:
        return ""
    parts = [
        "# 前序子问题的结论（可直接使用其结果文件、中间数据和代码）\n"
        "结果值、对象/范围、实验条件、来源与版本须一并沿用。范围不同可并存，缺少语义不能凭名字或同值判为相同口径；先核查筛选与行关系，再比较。前序子问题在各自的内核里运行，它们的变量和函数在你的内核里不存在。需要沿用前序的数据处理或模型时，"
        "先读它的代码文件、复制需要的部分，或读取它保存的中间数据，不要从头重写。"
    ]
    from ai_system.research.views import experiment_context
    store=getattr(ctx,'research',None)
    records={e['task_id']:e for e in experiment_context(store.snapshot(),set(dependencies))} if store else {}
    for dep_id, report in dependencies.items():
        parts.append(json.dumps(records[dep_id],ensure_ascii=False) if dep_id in records else f"## {dep_id}\n{report.get('summary', '')}")
        if ctx.fs.exists(solution_code_path(dep_id)):
            parts.append(f"代码：{solution_code_path(dep_id)}")
    files = _intermediate_files(ctx)
    if files:
        parts.append("中间数据文件：\n" + "\n".join(f"- {f}" for f in files))
    return "\n\n".join(parts)


def build_tools(
    ctx: GraphContext,
    subproblem_id: str,
    run_log: Optional[List[Dict]] = None,
    kernel_key: Optional[str] = None,
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
        """在持久 Python 内核中执行代码，返回输出、错误回溯和自动保存的图片文件名。

        title: 给用户看的简短操作名称，如“计算线性趋势预测”或“保存预测结果表”。说明代码实际用途，不含内部编号。可省略。
        """
        code = strip_template_tokens(code)
        if is_placeholder(code):
            return PLACEHOLDER_REPLY
        from ai_system.research.views import activity
        from ai_system.sandbox.artifacts import capture_run_files
        publication_note = ''
        with activity(ctx,'run_python',subproblem_id,inputs={'code':code},display_label=' '.join(title.split())[:60]) as operation:
            import time
            from ai_system.research.views import activity_progress
            last_push = 0.0
            async def progress(event):
                nonlocal last_push
                operation['run_id'] = event['run_id']
                output = operation.setdefault('output', {})
                if event['type'] == 'output':
                    output['output_tail'] = (output.get('output_tail', '') + event['text'])[-6000:]
                now = time.monotonic()
                if event['type'] != 'output' or now - last_push >= 1:
                    activity_progress(ctx, operation)
                    last_push = now
            result = await kernel.execute(code, on_event=progress)
            # Publish the run before its terminal timeline event resolves file links.
            if getattr(ctx, 'delivery_fs', None) is not None:
                try:
                    for path in capture_run_files(ctx.fs, ctx.delivery_fs, run_ids=[result.run_id]):
                        await ctx.delivery_events.file_changed(path)
                except (OSError, ValueError) as exc:
                    publication_note = f'文件交付未完成：{exc}。计算结果已保留，不要重算，提交时会再次同步。'
            operation.update(run_id=result.run_id,status='completed' if result.ok else 'failed',
                output={**operation.get('output', {}),'ok':result.ok,'run_id':result.run_id, 'files':result.files, 'delivery_error': result.delivery_error, 'failure_kind': result.failure_kind})
            if not result.ok:
                operation.update(error_type='TimeoutError' if result.timed_out else 'PythonExecutionError',
                                 message='执行超时，内核已重启。' if result.timed_out else result.error)
        run_log.append({"run_id": result.run_id, "code": code, "ok": result.ok})
        await ctx.events.emit(
            "paper_code",
            f"{subproblem_id} · {result.run_id} · {'成功' if result.ok else '失败'}",
        )
        observation = clip(result.to_observation(), OBSERVATION_CHARS)
        if publication_note: observation += '\n' + publication_note
        if result.failure_kind == 'transport_unknown':
            return observation + '\n连接中断，执行是否完成无法确认。保留已收到输出，提交阻碍交回研究层；不要自动重放相同代码。'
        if result.failure_kind == 'delivery':
            return observation
        if not result.ok:
            return observation + "\n[执行失败] 代码未成功完成。请阅读回溯并修复，或在报告中说明未完成部分。"
        return observation

    async def retry_outputs() -> str:
        """仅重试上次计算的文件同步，不重新执行代码。"""
        if not hasattr(kernel, 'retry_outputs'):
            return '当前执行器没有待同步的远端文件。'
        run_id = await kernel.retry_outputs()
        if not run_id:
            return '没有待同步文件，直接使用已有输出。'
        from ai_system.sandbox.artifacts import capture_run_files
        from ai_system.sandbox.kernel import CellResult
        info = ctx.fs.get_run_info(run_id)
        files = info.get('artifacts', [])
        if getattr(ctx, 'delivery_fs', None) is not None:
            for path in capture_run_files(ctx.fs, ctx.delivery_fs, run_ids=[run_id]):
                await ctx.delivery_events.file_changed(path)
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


def build_task(
    branch: BranchInput,
    ctx: GraphContext,
    candidate: Optional[Dict] = None,
    extra: str = "",
    ending: str = "开始求解。",
) -> str:
    subproblem = branch["subproblem"]
    chosen = candidate or branch["design"]["chosen"]
    if not set(chosen.get('method_keys') or []) <= allowed_method_keys(ctx.skill_policy):
        raise ValueError('已有方案使用了当前禁用的技能，请重新设计')
    files = "\n".join(f"- {f}" for f in list_data_files(ctx.workspace)) or "（无）"
    from ai_system.research.experience import prompt_context
    from ai_system.research.citations import literature_root
    from ai_system.research.literature import literature_context
    return "\n\n".join(filter(None, [
        f"# 原题（含题内数据；不得用模拟数据替代）\n{branch['source']}",
        f"# 子问题 {subproblem['id']}：{subproblem['title']}\n{subproblem['question']}\n交付物：{'；'.join(subproblem.get('deliverables') or [])}",
        f"# 数据文件\n{files}\n\n# 数据概览\n{branch['data_profile']}",
        '# 用户对必要信息的回复（保留来源；不是计算成果，不得借此放宽冻结契约）\n'+json.dumps(branch['clarifications'],ensure_ascii=False) if branch.get('clarifications') else '',
        f"# 选定的建模方案：{chosen['name']}\n思路：{chosen['idea']}\n数学表述：{chosen['formulation']}\n"
        f"步骤：\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(chosen["steps"]))
        + f"\n检验：{chosen['validation']}",
        literature_context(literature_root(ctx), json.dumps(subproblem, ensure_ascii=False)+'\n'+json.dumps(chosen, ensure_ascii=False)),
        '# 本次冻结实验（方法、参数、数据范围与评价条件不得静默更换）\n'+json.dumps(branch['experiment'],ensure_ascii=False) if branch.get('experiment') else '',
        f"# 用户技能配置\n{skill_policy_prompt(ctx.skill_policy)}",
        f"# 方法参考\n{describe(chosen.get('method_keys') or [], ctx.skill_policy)}",
        prompt_context(ctx,branch,chosen.get('method_keys') or [],'solve') if chosen.get('method_keys') else '',
        _dependency_text(branch.get("dependencies") or {}, ctx),
        "# 原问题要求与建模前提（按题目在代码中实施必要校验）\n" + json.dumps(branch["research_contract"], ensure_ascii=False) if branch.get("research_contract") else "",
        "# 已有核查疑点（按信息需求核实；错误意见可驳回）\n" + json.dumps(branch["review_findings"], ensure_ascii=False) if branch.get("review_findings") else "",
        ('# 同一实验继续行动（方法和条件不变）\n'+json.dumps(branch['continuation'],ensure_ascii=False)
         +'\n此前记录：'+json.dumps(branch.get('previous_report'),ensure_ascii=False)
         +'\n复用已有结果和中间文件，只处理明确的信息需求，不重放已完成计算。恢复后内核可能是新建的，先读取已保存文件，不假定旧变量一定存在。') if branch.get('continuation') else '',
        extra,
        ending,
    ]))


class ReportFallback(NamedTuple):
    """步数用完时直接要报告：用哪个模型、什么报告格式、什么系统提示，对话从哪份记录取"""

    llm: Any
    schema: Any
    system: str
    recorder: HistoryRecorder


async def run_agent(agent, messages: List, recursion_limit: int, nudge: str = REPORT_NUDGE,
                    fallback: Optional[ReportFallback] = None):
    """跑一轮循环；模型用文字收尾、没交报告时提醒一次

    步数用完（GraphRecursionError）时，有 fallback 就从记录里取回对话，不给任何执行工具、直接用结构化输出要报告：
    官方 API 的 deepseek-flash 步子很碎，最后一步只给报告工具它也照样调 run_python，v2l 的 Q3 跑满 40 步后整段作废。
    """
    try:
        result = await agent.ainvoke({"messages": messages}, config={"recursion_limit": recursion_limit})
        if result.get("structured_response") is None:
            history = [drop_invalid_calls(m) if isinstance(m, AIMessage) else m for m in result["messages"]]
            result = await agent.ainvoke(
                {"messages": history + [HumanMessage(content=nudge)]},
                config={"recursion_limit": 11},
            )
        return result
    except GraphRecursionError:
        if fallback is None or not fallback.recorder.messages:
            raise
        history = list(fallback.recorder.messages)
        logger.warning("步数用完，用 %d 条对话直接要报告", len(history))
        prompt = [SystemMessage(content=fallback.system), *compact_messages(history), HumanMessage(content=FINAL_NUDGE)]
        report = await call_structured(fallback.llm, fallback.schema, prompt)
        return {"messages": history + [HumanMessage(content=FINAL_NUDGE)], "structured_response": report}


async def solve(branch: BranchInput, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    subproblem_id = branch["subproblem"]["id"]
    set_label(subproblem_id)
    await ctx.set_stage("solve", "in_progress")
    run_log: List[Dict] = []
    if branch.get('continuation'):
        for run_id, info in ctx.fs._manifest.get('runs', {}).items():
            if info.get('status') == 'success' and info.get('evidence_archived'):
                run_log.append({'run_id': run_id, 'ok': True, 'code': '', 'inherited': True})
    if branch.get('experiment'):
        from .planning import experiment_design
        branch={**branch,'design':experiment_design(branch['experiment'])}
        branch['design']['clarification_refs']=[r['ref'] for r in branch.get('clarifications',[])]
    if branch.get("design") is None:
        if branch["subproblem"].get("kind") == "verify":
            chosen = {"name": "独立复算", "method_keys": [], "idea": "从冻结输入独立复算争议检查，不引入新的建模前提。",
                "formulation": "", "steps": ["读取冻结输入", "独立复算并保存结果"], "assumptions": [], "validation": "核对问题中的实际约束"}
            record = {"methods": [], "candidates": [chosen], "scores": [{"total": None}], "chosen": chosen,
                "chosen_index": 0, "critic": None, "critic_error": None}
        else:
            chosen={'name':'执行当前研究问题','method_keys':sorted(allowed_method_keys(ctx.skill_policy)) if ctx.skill_policy and ctx.skill_policy['mode']=='selected' else [],
                    'idea':branch.get('feedback') or '根据已有材料和当前问题落实方法，如实说明选择、证据和局限。',
                    'formulation':'','steps':['读取相关材料并选择适用方法','完成本问所需计算与检验，保存结果文件'],
                    'assumptions':[],'validation':'依据原题实施必要校验，不虚构数据或阈值'}
            record={'methods':chosen['method_keys'],'candidates':[chosen],'chosen':chosen,'chosen_index':0,'scores':[],
                    'critic':None,'critic_error':None,'direct_execution':True,
                    'clarification_refs':[r['ref'] for r in branch.get('clarifications',[])]}
        branch = {**branch, "design": record}
    design = branch["design"]
    try:
        if (branch.get('experiment') or {}).get('csv') and (not branch.get('continuation') or branch.get('resume_comparison')):
            from ai_system.modules.registry import invoke
            from .handoff import publish_comparison
            record=await invoke(ctx,'experiments.compare',branch=branch)
            report=await publish_comparison(ctx,branch,record,run_log)
            return {'report':report,'design':design}
        await ctx.events.stage(f"{subproblem_id}：开始编码求解「{design['chosen']['name']}」")
        status, error, report, reviews = await _solve_loop(
            ctx, branch, run_log, [HumanMessage(content=build_task(branch, ctx))]
        )
    finally:
        await ctx.kernels.release(subproblem_id)
        export_solution_code(ctx, subproblem_id, run_log)

    if not report:
        report = SolveReport(summary="本轮未正常结束，执行记录和已生成文件已保留。",
                             approach=design["chosen"]["name"], key_findings=[]).model_dump()
    report['runs'] = [{k: r[k] for k in ('run_id', 'ok', 'inherited') if k in r} for r in run_log]
    report['files'] = sorted({path for run in run_log
        for path in (ctx.fs.get_run_info(run['run_id']) or {}).get('artifacts', [])})

    return {'report':{**report,'status':status,'error':error,'reviews':reviews},'design':design}


async def _solve_loop(ctx: GraphContext, branch: BranchInput, run_log: List[Dict], messages: List):
    """实验执行：连续 CodeAct 行动，完成尝试后将发现交回研究层，返回 (status, error, report, reviews)"""
    subproblem_id = branch["subproblem"]["id"]
    recorder = HistoryRecorder()
    schema = SolveReport
    async def check_interpretation(report: SolveReport, information_need: str = "") -> str:
        """针对明确疑点核查代码与解释；意见仅供当前实验者核实，不触发计算或自动改稿。"""
        from .review import review_solution
        from ai_system.research.views import activity
        with activity(ctx,'check_interpretation',subproblem_id) as operation:
            operation['inputs']={'information_need':information_need,'report':report.model_dump()}
            result=await review_solution(ctx,branch,report.model_dump(),run_log)
            operation['output']={'findings':[i.model_dump() for i in result.issues]}
            return json.dumps(operation['output'],ensure_ascii=False)+'\n请据原资料和实际输出采纳、驳回或保留局限，勿因措辞问题重算。'
    tools=build_tools(ctx, subproblem_id, run_log)
    tools.append(StructuredTool.from_function(coroutine=check_interpretation,name='check_interpretation',handle_tool_error=True,handle_validation_error=str))
    agent = create_agent(
        model=ctx.coder_llm,
        tools=tools,
        system_prompt=SYSTEM + PROGRESS_PROMPT,
        response_format=ToolStrategy(schema),
        middleware=[recorder, ProgressMiddleware(ctx.events, scope='experiment:'+subproblem_id), CompactHistoryMiddleware(), StepBudgetMiddleware(ctx.max_solver_steps, 4, REPORT_NUDGE),
                    DropRefusedStepsMiddleware(PLACEHOLDER_REPLY), TruncationGuardMiddleware(),
                    ReportOrderingMiddleware(schema.__name__)],
    )
    limit = ctx.max_solver_steps * 2 + 1
    fallback = ReportFallback(ctx.coder_llm, schema, SYSTEM, recorder)
    status, error = "completed", None
    report: Dict = {}
    reviews: List[Dict] = []
    structured = None
    result = None
    try:
        result = await run_agent(agent, messages, limit, fallback=fallback)
        structured = result.get("structured_response")
        if structured is not None:
            report = structured.model_dump()
        if not report:
            status, error = "incomplete", "本轮没有返回研究报告，执行记录与文件已保留"
        elif run_log and not any(r.get("ok") for r in run_log):
            status, error = "incomplete", "本轮执行均失败，实际错误与文件已保留"
    except GraphRecursionError:
        status, error = "incomplete", f"超过 {ctx.max_solver_steps} 步仍未完成"
    except Exception as exc:
        logger.exception("求解 %s 失败", subproblem_id)
        status, error = "failed", str(exc)
    if result is not None:
        save_transcript(ctx, subproblem_id, result["messages"])
    elif recorder.messages:
        save_transcript(ctx, subproblem_id, recorder.messages)
    return status, error, report, reviews


def save_transcript(ctx: GraphContext, name: str, messages: List) -> None:
    """求解对话落盘（.system/transcripts/），读 trace 时能看到每一步模型写了什么、调用了什么工具"""
    try:
        ctx.write_json(f".system/transcripts/{name}.json", messages_to_dict(messages))
    except Exception as exc:  # 只是排查用的记录，失败不影响求解
        logger.warning("保存 %s 的求解对话失败: %s", name, exc)
