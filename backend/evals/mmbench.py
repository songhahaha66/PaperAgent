"""
MM-Bench 题目加载

MM-Bench（https://github.com/usail-hkust/LLM-MM-Agent，CC BY-NC 4.0）不随本仓库分发，
评测时用 --bench 指向本地克隆的 MMBench 目录：
    MMBench/problem/<id>.json
    MMBench/dataset/<id>/<file>
"""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

# 默认子集：7 道带数据的题（C/D）+ 3 道纯建模题，覆盖 2020–2025
# 不含 2021_C：数据约 700MB 图片，且题目 JSON 里的 PDF 文件名与实际文件不一致
DEFAULT_SUBSET = [
    "2025_C",
    "2024_C",
    "2023_C",
    "2022_C",
    "2020_C",
    "2020_D",
    "2021_D",
    "2025_A",
    "2024_A",
    "2023_B",
]


@dataclass
class Problem:
    id: str
    background: str
    requirement: str
    dataset_files: List[Path] = field(default_factory=list)
    dataset_description: Dict[str, str] = field(default_factory=dict)
    variable_description: Dict[str, str] = field(default_factory=dict)
    addendum: str = ""


def load_problem(bench_dir: Path, problem_id: str) -> Problem:
    bench_dir = Path(bench_dir)
    data = json.loads((bench_dir / "problem" / f"{problem_id}.json").read_text(encoding="utf-8"))
    dataset_dir = bench_dir / "dataset" / problem_id
    files = [dataset_dir / name for name in data.get("dataset_path") or []]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"{problem_id} 缺少数据文件: {missing}")
    return Problem(
        id=problem_id,
        background=data.get("background", ""),
        requirement=data.get("problem_requirement", ""),
        dataset_files=files,
        dataset_description=data.get("dataset_description") or {},
        variable_description=data.get("variable_description") or {},
        addendum=data.get("addendum") or "",
    )


def stage_datasets(problem: Problem, workspace: Path) -> List[str]:
    """把题目数据复制到工作空间 data/ 下，返回相对路径"""
    data_dir = Path(workspace) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for src in problem.dataset_files:
        if src.is_dir():
            shutil.copytree(src, data_dir / src.name, dirs_exist_ok=True)
        else:
            shutil.copy2(src, data_dir / src.name)
        staged.append(f"data/{src.name}")
    return staged


def build_task_prompt(problem: Problem, staged_files: List[str]) -> str:
    """把题目拼成发给 agent 的单条用户消息，模拟真实用户的一次提问"""
    parts = [
        "请完成下面这道数学建模竞赛题，写出一篇完整的建模论文（paper.md）。",
        "论文应包含摘要、问题重述、问题分析、模型假设、符号说明、模型建立与求解、灵敏度分析、模型评价。",
        "需要计算的地方请编写并运行 Python 代码，论文中的数值结果必须来自代码的实际输出。",
        f"\n## 背景\n{problem.background}",
        f"\n## 任务要求\n{problem.requirement}",
    ]
    if problem.addendum:
        parts.append(f"\n## 补充说明\n{problem.addendum}")
    if staged_files:
        parts.append("\n## 数据文件（已放在工作空间中）")
        parts.extend(f"- {path}" for path in staged_files)
        if problem.dataset_description:
            parts.append("\n数据集说明：")
            parts.extend(f"- {k}: {v}" for k, v in problem.dataset_description.items())
        if problem.variable_description:
            parts.append("\n字段说明：")
            parts.extend(f"- {k}: {v}" for k, v in problem.variable_description.items())
    return "\n".join(parts)
