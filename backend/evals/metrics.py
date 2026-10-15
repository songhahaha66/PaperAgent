"""
确定性指标：不调用任何模型，直接读工作空间

- code_runs：代码执行次数与成功率（来自 manifest.json 的 runs 记录）
- section_coverage：数模论文标准章节是否齐全
- number_traceability：论文中的小数/百分数有多少能在代码输出里找到（防编造数字）
"""

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

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
_ANY_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

MAX_DECIMALS = 6
MAX_CORPUS_NUMBERS = 200_000
TEXT_SUFFIXES = {".txt", ".log", ".csv", ".json", ".md", ".tsv"}


def code_runs(workspace: Path) -> Dict:
    manifest_path = Path(workspace) / "manifest.json"
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


def _decimals(token: str) -> int:
    mantissa = re.split(r"[eE]", token.rstrip("%"))[0]
    return len(mantissa.split(".")[1]) if "." in mantissa else 0


def _corpus_files(workspace: Path) -> Iterable[Path]:
    workspace = Path(workspace)
    for root in (workspace / "runs", workspace / "outputs"):
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                    yield path


def _rounded_set(numbers: Iterable[float]) -> Set[Tuple[int, float]]:
    """把每个数在 1..MAX_DECIMALS 位小数下的取值（及 ×100 后的百分数形式）放进集合"""
    rounded = set()
    for value in numbers:
        for d in range(1, MAX_DECIMALS + 1):
            rounded.add((d, round(value, d)))
            rounded.add((d, round(value * 100, d)))
    return rounded


def number_traceability(paper: str, workspace: Path, problem_text: str = "") -> Dict:
    """
    论文中的小数能否在代码输出（runs/ 与 outputs/ 下的文本文件）中找到。
    题目原文里已有的数字是已知条件，不计入分母。
    """
    given = set(_DECIMAL.findall(problem_text))
    tokens = [t for t in _paper_numbers(paper) if t not in given]
    if not tokens:
        return {"checked": 0, "traced": 0, "rate": None, "untraced_examples": []}

    corpus: List[float] = []
    for path in _corpus_files(workspace):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in _ANY_NUMBER.findall(text):
            try:
                corpus.append(float(token))
            except ValueError:
                continue
            if len(corpus) >= MAX_CORPUS_NUMBERS:
                break
        if len(corpus) >= MAX_CORPUS_NUMBERS:
            break
    known = _rounded_set(corpus)

    traced, untraced = 0, []
    for token in tokens:
        d = min(_decimals(token), MAX_DECIMALS)
        value = float(token.rstrip("%"))
        if d and (d, round(value, d)) in known:
            traced += 1
        else:
            untraced.append(token)
    return {
        "checked": len(tokens),
        "traced": traced,
        "rate": round(traced / len(tokens), 3),
        "untraced_examples": untraced[:20],
    }
