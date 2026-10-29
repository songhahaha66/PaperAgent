"""
确定性指标：不调用任何模型，直接读工作空间

- code_runs：代码执行次数与成功率（来自 manifest.json 的 runs 记录）
- section_coverage：数模论文标准章节是否齐全
- number_traceability：论文中的小数/百分数能否追溯到当前已验收、来源执行成功的登记值
"""
from services.file_services.workspace_layout import workspace_path

import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple

from ai_system.graph.registry import Result, value_in_output
from ai_system.research import ENGINE_VERSION
from ai_system.research.store import ResearchStore

# 数模论文的标准章节：(名称, 标题关键词)
REQUIRED_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("摘要", ("摘要", "summary", "abstract")),
    ("问题重述", ("重述", "restatement", "introduction", "问题背景")),
    ("问题分析", ("问题分析", "analysis of the problem", "problem analysis")),
    ("模型假设", ("假设", "assumption")),
    ("符号说明", ("符号", "notation", "nomenclature")),
    ("模型建立与求解", ("模型的建立", "模型建立", "建立", "求解", "model", "solution")),
    ("灵敏度分析", ("灵敏度", "敏感性", "sensitivity", "robustness")),
    ("模型评价", ("评价", "优缺点", "推广", "strength", "weakness", "evaluation")),
)

_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
# 带小数点的数（可带百分号或科学计数法）；纯整数太容易误匹配（年份、编号、计数），不计入
_DECIMAL = re.compile(r"(?<![\w.])-?\d+\.\d+(?:[eE][-+]?\d+)?%?")


def code_runs(workspace: Path) -> Dict:
    manifest_path = workspace_path(Path(workspace), "manifest.json")
    runs = {}
    if manifest_path.exists():
        runs = json.loads(manifest_path.read_text(encoding="utf-8")).get("runs", {})
    statuses = [r.get("status") for r in runs.values()]
    total = len(statuses)
    success = statuses.count("success")
    return {
        "total": total,
        "success": success,
        "error": statuses.count("error"),
        "success_rate": round(success / total, 3) if total else None,
    }


def section_coverage(paper: str) -> Dict:
    headings = [h.strip().lower() for h in _HEADING.findall(paper)]
    found, missing = [], []
    for name, keywords in REQUIRED_SECTIONS:
        if any(k in h for h in headings for k in keywords):
            found.append(name)
        else:
            missing.append(name)
    return {
        "found": found,
        "missing": missing,
        "coverage": round(len(found) / len(REQUIRED_SECTIONS), 3),
    }


def _paper_numbers(paper: str) -> List[str]:
    return _DECIMAL.findall(_CODE_FENCE.sub("", paper))


def _verified_values(workspace: Path) -> list[str]:
    """Use the business store, never historical files or the registry projection."""
    root = Path(workspace).resolve()
    thread = json.loads((workspace_path(root, ".system/graph_thread.json")).read_text())
    if thread.get("engine_version") != ENGINE_VERSION:
        raise ValueError("研究引擎版本不支持证据评测")
    store = ResearchStore(root, thread["thread_id"], read_only=True)
    state = store.snapshot()
    if not state or state["engine_version"] != ENGINE_VERSION:
        raise ValueError("缺少当前研究状态")
    manifest = json.loads((workspace_path(root, "manifest.json")).read_text())
    values = []
    for item in store.registry_payload(state)["results"]:
        result = Result(**item)
        if result.kind != "number" or not re.fullmatch(r"run_[A-Za-z0-9_]+", result.run_id):
            continue
        run = manifest.get("runs", {}).get(result.run_id, {})
        if run.get("status") != "success" or not run.get("stdout"):
            continue
        try:
            output = workspace_path(root, run["stdout"])
        except ValueError:
            continue
        if not output.is_relative_to(workspace_path(root, "runs") / result.run_id) or not output.is_file():
            continue
        if value_in_output(result.value, output.read_text(encoding="utf-8")):
            # Normalize percentages, without letting digits in units become evidence.
            raw = result.value.strip().replace(",", "")
            value = float(raw.rstrip("%")) / (100 if raw.endswith("%") or result.unit == "%" else 1)
            values.append(str(value))
    return values


def number_traceability(paper: str, workspace: Path, problem_text: str = "") -> Dict:
    """
    仅匹配当前有效登记值，并再次核对其成功执行的 stdout。
    这是数值来源指标，不证明正文语义或计算方法正确。原题数字不计入分母。
    """
    given = set(_DECIMAL.findall(problem_text))
    tokens = [t for t in _paper_numbers(paper) if t not in given]
    if not tokens:
        return {"checked": 0, "traced": 0, "rate": None, "untraced_examples": []}

    try:
        values = _verified_values(workspace)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        return {"checked": len(tokens), "traced": 0, "rate": None,
                "untraced_examples": tokens[:20], "error": f"有效证据不可用：{exc}"}

    traced, untraced = 0, []
    for token in tokens:
        # Percentage conversion is allowed only for percentage text, as in registration.
        if any(value_in_output(token, value) for value in values):
            traced += 1
        else:
            untraced.append(token)
    return {
        "checked": len(tokens),
        "traced": traced,
        "rate": round(traced / len(tokens), 3),
        "untraced_examples": untraced[:20],
    }
