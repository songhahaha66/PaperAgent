"""
阶段 1：数据概览（确定性代码）+ 题目分析（结构化输出）
"""

import json
import re
from typing import Dict, List

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


# 文书类交付物的标题特征；模型有时在整体思路里说了"写作类"，字段却仍填 compute
WRITING_TITLE = re.compile(r"备忘录|建议信|公开信|致.{0,12}的?信|信函|memo|letter", re.IGNORECASE)


def normalize_plan(subproblems: List[Dict]) -> List[Dict]:
    """用确定性规则补齐模型漏填的子问题类型和依赖，避免文书类子问题在没有结论时就开写、
    或后一问在前一问结果出来之前就开跑。只补不删：模型给出的合法依赖全部保留。"""
    ids = [sp["id"] for sp in subproblems]
    fixed = []
    for index, sp in enumerate(subproblems):
        sp = dict(sp)
        earlier = ids[:index]
        if sp.get("kind") != "writing" and WRITING_TITLE.search(sp.get("title", "")):
            sp["kind"] = "writing"
        deps = [d for d in sp.get("depends_on") or [] if d in ids and d != sp["id"]]
        # 问题描述里提到的前序子问题（如"用 Q1 建立的指标"）就是依赖
        mentioned = [i for i in earlier if re.search(rf"(?<![A-Za-z0-9]){re.escape(i)}(?!\d)", sp.get("question", ""))]
        if sp["kind"] == "writing" and not deps and not mentioned:
            mentioned = [s["id"] for s in fixed if s.get("kind") != "writing"]
        sp["depends_on"] = deps + [d for d in mentioned if d not in deps]
        fixed.append(sp)
    return fixed


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

    plan = analysis.model_dump()
    plan["subproblems"] = normalize_plan(plan["subproblems"])
    ids = [sp.id for sp in analysis.subproblems]
    await ctx.set_stage("analyze", "completed", f"{len(ids)} 个子问题：{'、'.join(ids)}")
    if not ctx.confirm_models:
        await ctx.set_stage("confirm", "completed", "自动通过")
    await ctx.events.emit(
        "paper_analysis",
        "\n".join([f"**{analysis.title}**", ""] + [f"- {sp.id} {sp.title}" for sp in analysis.subproblems]),
    )
    return {
        "data_profile": data_profile,
        "analysis": plan,
        # 需要人工确认方案时才提前把所有子问题设计完；否则按需设计，
        # 先就绪的子问题不用等其他子问题的设计，后面的子问题还能参考前序结论
        "plan_ahead": bool(ctx.confirm_models),
        "design_round": 0,
        "repair_round": 0,
        "log": [f"analyze: {len(ids)} subproblems"],
    }
