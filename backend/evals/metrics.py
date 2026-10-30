"""
确定性指标：不调用任何模型，直接读工作空间

- code_runs：代码执行次数与成功率（来自 manifest.json 的 runs 记录）
- section_coverage：数模论文标准章节是否齐全
- artifact_files：实际发布结果文件是否存在、非空并可按格式打开
"""
from services.file_services.workspace_layout import workspace_path

import json
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple


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


def artifact_files(workspace: Path) -> Dict:
    """Check actual published files. Readability is not mathematical correctness."""
    from services.file_services.workspace_fs import WorkspaceFS
    fs = WorkspaceFS(str(workspace))
    rows = [(path, meta) for path, meta in fs._manifest.get('files', {}).items()
            if meta.get('source_run') and meta.get('visibility') == 'user']
    failures = []
    for path, meta in rows:
        try:
            target = fs.canonical_artifact(path)
            if not target.is_file() or not target.stat().st_size:raise ValueError('文件为空或不存在')
            if target.suffix == '.xlsx':
                import openpyxl
                book = openpyxl.load_workbook(target, read_only=True, data_only=True)
                try:
                    if not book.sheetnames:raise ValueError('工作簿没有工作表')
                finally:book.close()
            elif target.suffix == '.csv':
                import csv
                with target.open(encoding='utf-8-sig', newline='') as stream:
                    if next(csv.reader(stream), None) is None:raise ValueError('CSV为空')
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            failures.append({'path': path, 'error': str(exc)})
    return {'checked': len(rows), 'readable': len(rows)-len(failures), 'failures': failures,
            'rate': round((len(rows)-len(failures))/len(rows),3) if rows else None}
