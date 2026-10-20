"""
阶段 1：数据概览（确定性代码）+ 题目分析（结构化输出）
"""

import json
import re
from typing import Dict, List

from langgraph.runtime import Runtime

from ai_system.graph.context import EXPORT_STAGE_TITLES, GraphContext, list_data_files
from ai_system.graph.llm_utils import as_messages, call_structured, clip, cap_output
from ai_system.graph.state import PaperState, ProblemAnalysis

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
    "按可独立验收的研究目标拆分，不把简单数据整理强行拆成独立建模题，也不把同一预测实验的训练、验证、预测机械拆成重复任务。"
    "用户指定的比较方法必须保留；只做所需分析，不添加题目没有要求的预测、显著性检验或复杂模型。"
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


# "撰写完整论文"本身就是流水线的产出，不是一个子问题；2024_C 有一次被拆成了 Q5
PAPER_ITSELF = re.compile(r"(完整|整篇)?(建模)?论文(的)?(撰写|写作|整理|汇总)|撰写.{0,6}论文")


def normalize_plan(subproblems: List[Dict]) -> List[Dict]:
    """用确定性规则补齐模型漏填的子问题类型和依赖，避免文书类子问题在没有结论时就开写、
    或后一问在前一问结果出来之前就开跑。模型给出的合法依赖全部保留；只删掉"撰写论文"这种子问题。"""
    subproblems = [
        sp for sp in subproblems
        if not (PAPER_ITSELF.search(sp.get("title", "")) and not WRITING_TITLE.search(sp.get("title", "")))
    ] or subproblems
    # 同一编号只留第一次出现的：v2k 的分析把 Q1-Q4 列了两遍，Q1 被并行求解了两次
    seen = set()
    subproblems = [sp for sp in subproblems if not (sp["id"] in seen or seen.add(sp["id"]))]
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
    if ctx.output_mode in EXPORT_STAGE_TITLES:
        # 让前端一开始就显示"排版导出"这一步
        await ctx.events.emit("paper_phase", {"stage": "export", "status": "pending", "note": ""})
    await ctx.events.stage("正在分析题目并读取数据")

    data_profile = await profile_data(ctx)
    prompt = (
        f"# 题目\n{state['task']}\n\n"
        f"# 数据概览（由程序自动生成）\n{data_profile}\n\n"
        "请给出：论文标题、题目重述、整体分析思路、子问题列表（编号 Q1、Q2…，写清每一问要交付什么、依赖哪一问的结果）。另给出结构化 requirements 与 assumptions：每项要求包含原题逐字摘录、任务范围；硬约束必须编制可执行检查（如容量、非负性、守恒、误差界，使用明确登记名称和数值阈值）。假设必须写适用任务和可证伪条件。检查引用其他任务结果时，把它声明为依赖；不要添加原题没有的硬约束。"
        "子问题只列原题实际研究内容；题目分析、整篇论文撰写与排版导出已有流水线处理，不另列子问题。"
        "先固定研究子问题编号，再填要求和假设：所有task_ids及检查的task_id必须来自这些编号；"
        "每项检查的被验收任务left.task_id必须在其所属要求/假设的task_ids内，每个子问题至少被一项要求覆盖。"
        "代码、图表、论文结构等交付要求绑定到产生相关内容的研究子问题，不新增虚构任务。"
        "数值检查只验收单个数值：序列长度使用独立的长度指标配合eq，整组非负性使用独立的最小值指标配合ge；"
        "不要对整组预测或数据数组直接设置数值比较。exists只证明结果非空，不能证明数组长度或计算正确。"
        "要求中的数值阈值必须来自该要求的原文摘录；原题只要求比较预测误差时，用finite检查误差，"
        "不得自行添加MAE/MAPE上限或利润比例下限。用户给定的售价、成本等可用eq核对。"
        "仅当原题明确给出线性优化的目标、约束和变量条件时，使用linear_feasible检查，逐字摘录其来源，"
        "按变量顺序填写系数，left选择目标值，每个变量使用独立登记名称；未给定的上下界填null，不得默认非负。"
        "它只复算可行性与目标值，不证明最优。不要把需要自行建模的问题硬转成线性模型。"
        "kind严格区分：数学/领域硬约束为constraint且checks非空；研究目标为goal；报告、语言、格式、引用规则为deliverable，"
        "这些文书要求由后续章节校验处理，不要创建has_report、assumptions_count等自报指标。"
        "预测中的线性趋势、线性回归不等于线性规划。只有销量数据、经济参数而没有明确线性优化目标和约束时，"
        "不得编造linear_model；按具体预测指标、输入参数和交付要求设置检查。"
    )
    from ai_system.research.validation import validate_spec, source_excerpt
    from ai_system.research.store import validate_dependencies

    def validate_analysis(candidate):
        # Freeze original spans, not the model's typography. Unresolvable
        # paraphrases remain invalid and enter the same bounded retry.
        for requirement in candidate.requirements:
            requirement.source_quote = source_excerpt(state["task"], requirement.source_quote) or requirement.source_quote
        for owner in [*candidate.requirements, *candidate.assumptions]:
            for check in owner.checks:
                if check.linear_model:
                    model = check.linear_model
                    model.objective_quote = source_excerpt(state["task"], model.objective_quote) or model.objective_quote
                    for item in [*model.variables, *model.constraints]:
                        item.source_quote = source_excerpt(state["task"], item.source_quote) or item.source_quote
        plan = candidate.model_dump()
        plan["subproblems"] = normalize_plan(plan["subproblems"])
        errors = []
        try:
            validate_spec(state["task"], plan)
        except ValueError as exc:
            errors.append(str(exc))
        try:
            validate_dependencies({sp["id"]: {"dependencies": sp["depends_on"]} for sp in plan["subproblems"]})
        except ValueError as exc:
            errors.append(str(exc))
        if errors:
            raise ValueError("\n".join(errors))

    system = SYSTEM
    if ctx.compact_paper:
        system += ("\n这是紧凑研究。保持全部原题要求，但合并同一任务的交付要求；"
                   "每个来源摘录只复制一段短而连续的原题原文，绝不能拼接不相邻的句子。"
                   "重述与整体分析各不超过80字，问题和检查说明简短，不重复原题。"
                   "用户仅指定一项实验时只列Q1；没有新增关键前提时assumptions为空。")
    analysis = await call_structured(cap_output(ctx.planner_llm, 4800) if ctx.compact_paper else ctx.planner_llm,
                                    ProblemAnalysis, as_messages(system, prompt), validate=validate_analysis)
    plan = analysis.model_dump()
    plan["subproblems"] = normalize_plan(plan["subproblems"])
    ids = [sp["id"] for sp in plan["subproblems"]]
    await ctx.set_stage("analyze", "completed", f"{len(ids)} 个子问题：{'、'.join(ids)}")
    await ctx.events.emit(
        "paper_analysis",
        "\n".join([f"**{analysis.title}**", ""] + [f"- {sp['id']} {sp['title']}" for sp in plan["subproblems"]]),
    )
    return {
        "data_profile": data_profile,
        "analysis": plan,
        "repair_round": 0,
        "log": [f"analyze: {len(ids)} subproblems"],
    }
