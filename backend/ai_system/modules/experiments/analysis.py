"""
按需查看材料；概览不承担原题分析或科学验收。
"""

import json

from ai_system.graph.context import GraphContext, list_data_files
from ai_system.graph.llm_utils import clip

PROFILE_KERNEL = "profile"
MAX_PROFILE_CHARS = 8000
MAX_PROFILE_FILES = 30

# 仅在控制器选择查看材料时运行现有确定性概览。
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
