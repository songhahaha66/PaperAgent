"""
阶段 1：数据概览（确定性代码）+ 题目分析（结构化输出）
"""

import json

from langgraph.runtime import Runtime

from ..context import GraphContext, list_data_files
from ..llm_utils import as_messages, call_structured, clip
from ..state import PaperState, ProblemAnalysis

PROFILE_KERNEL = "profile"
MAX_PROFILE_CHARS = 8000
MAX_PROFILE_FILES = 30

# 在内核里跑的固定代码：不经过模型，保证每道题都有一致的数据概览
PROFILE_CODE = """
import os, json
import pandas as pd
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
files = json.loads({files!r})
for path in files:
    size = os.path.getsize(path)
    print(f"=== {{path}} ({{size / 1024:.1f}} KB)")
    lower = path.lower()
    try:
        if lower.endswith((".csv", ".txt", ".tsv")):
            sep = "\\t" if lower.endswith(".tsv") else None
            df = pd.read_csv(path, sep=sep, engine="python", nrows=200000)
            frames = {{"": df}}
        elif lower.endswith((".xlsx", ".xls")):
            frames = pd.read_excel(path, sheet_name=None, nrows=200000)
        else:
            print("（非表格文件）")
            continue
        for sheet, df in frames.items():
            if sheet:
                print(f"--- sheet {{sheet}}")
            print(f"形状: {{df.shape[0]}} 行 x {{df.shape[1]}} 列")
            print("列与类型:", ", ".join(f"{{c}}:{{t}}" for c, t in zip(df.columns[:40], df.dtypes[:40].astype(str))))
            missing = df.isna().sum()
            missing = missing[missing > 0]
            if len(missing):
                print("缺失值:", missing.head(15).to_dict())
            print(df.head(3).to_string(max_colwidth=30))
    except Exception as exc:
        print(f"读取失败: {{exc}}")
"""

SYSTEM = (
    "你是数学建模竞赛（如 MCM/ICM、全国大学生数学建模竞赛）的资深指导老师。"
    "你的任务是把题目拆解成可以独立建模求解的子问题。"
    "子问题必须与题目的提问一一对应，不要遗漏，也不要编造题目没有问的问题。"
    "写作语言与题目语言无关，统一使用中文。"
)


async def profile_data(ctx: GraphContext) -> str:
    files = list_data_files(ctx.workspace)
    if not files:
        return "（没有提供数据文件）"
    # 表格优先；图片等大量小文件只列前若干个
    files = sorted(files, key=lambda f: not f.lower().endswith((".csv", ".xlsx", ".xls", ".txt", ".tsv")))
    omitted = len(files) - MAX_PROFILE_FILES
    files = files[:MAX_PROFILE_FILES]
    kernel = ctx.kernels.get(PROFILE_KERNEL)
    try:
        result = await kernel.execute(PROFILE_CODE.format(files=json.dumps(files, ensure_ascii=False)))
    finally:
        await ctx.kernels.release(PROFILE_KERNEL)
    text = result.stdout if result.ok else f"{result.stdout}\n{result.error}"
    if omitted > 0:
        text += f"\n（另有 {omitted} 个文件未列出）"
    return clip(text.strip(), MAX_PROFILE_CHARS)


async def analyze(state: PaperState, runtime: Runtime[GraphContext]) -> dict:
    ctx = runtime.context
    await ctx.set_stage("analyze", "in_progress")
    await ctx.events.stage("正在分析题目并读取数据")

    data_profile = await profile_data(ctx)
    prompt = (
        f"# 题目\n{state['task']}\n\n"
        f"# 数据概览（由程序自动生成）\n{data_profile}\n\n"
        "请给出：论文标题、题目重述、整体分析思路、子问题列表（编号 Q1、Q2…，写清每一问要交付什么、依赖哪一问的结果）、全局假设。"
    )
    analysis = await call_structured(ctx.planner_llm, ProblemAnalysis, as_messages(SYSTEM, prompt))
    if not analysis.subproblems:
        raise RuntimeError("题目分析没有得到任何子问题")

    ids = [sp.id for sp in analysis.subproblems]
    await ctx.set_stage("analyze", "completed", f"{len(ids)} 个子问题：{'、'.join(ids)}")
    await ctx.events.emit(
        "paper_analysis",
        "\n".join([f"**{analysis.title}**", ""] + [f"- {sp.id} {sp.title}" for sp in analysis.subproblems]),
    )
    return {
        "data_profile": data_profile,
        "analysis": analysis.model_dump(),
        "design_round": 0,
        "repair_round": 0,
        "log": [f"analyze: {len(ids)} subproblems"],
    }
