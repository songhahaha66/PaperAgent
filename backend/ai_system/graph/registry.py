"""
结果登记表：论文里出现的每个数字和图都必须先在这里登记

求解阶段，模型调用 record_result / record_figure 把代码算出的结果登记下来，
登记时会核对该值确实出现在对应 run 的输出里（不在就拒绝）。
写作阶段，正文只能写 {{R:R3}} / {{F:F1}} 这样的引用，渲染时替换成真实数值和图片，
这样论文中的数字天然可以追溯到某次代码执行。

持久化在 .system/results.json，断点续跑和前端都能读取。
"""

import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from services.file_services.workspace_fs import WorkspaceFS

REGISTRY_PATH = ".system/results.json"
REF_PATTERN = re.compile(r"\{\{\s*([RF]):\s*([RF]\d+)\s*\}\}")
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


class GroundingError(ValueError):
    """登记的值在 run 输出中找不到"""


@dataclass
class Result:
    id: str
    kind: str  # number | text | figure
    subproblem: str
    name: str
    value: str
    run_id: str
    unit: str = ""
    description: str = ""
    path: str = ""  # 图片的 outputs/ 路径

    def display(self) -> str:
        return f"{self.value}{self.unit}" if self.unit in ("%", "‰") else f"{self.value} {self.unit}".strip()


def _parse_number(text: str) -> Optional[float]:
    cleaned = text.strip().replace(",", "").rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _decimals(text: str) -> int:
    cleaned = text.strip().replace(",", "").rstrip("%")
    mantissa = re.split(r"[eE]", cleaned)[0]
    return len(mantissa.split(".")[1]) if "." in mantissa else 0


def _significant_digits(text: str) -> int:
    mantissa = re.split(r"[eE]", text.strip().replace(",", "").rstrip("%"))[0]
    return len(mantissa.replace("-", "").replace(".", "").lstrip("0"))


def value_in_output(value: str, output: str) -> bool:
    """value 是否能由 output 中的某个数（四舍五入到同样位数，或换算成百分数）得到"""
    target = _parse_number(value)
    if target is None:
        return value.strip() != "" and value.strip() in output
    places = _decimals(value)
    # 只有至少两位有效数字时才允许四舍五入匹配，否则 0.9 这类值几乎能对上任何输出
    can_round = 0 < places < 15 and _significant_digits(value) >= 2
    is_percent = value.strip().endswith("%")
    for match in NUMBER_PATTERN.finditer(output.replace(",", "")):
        number = float(match.group())
        candidates = [number, number * 100] if is_percent else [number]
        for candidate in candidates:
            if math.isclose(candidate, target, rel_tol=1e-9, abs_tol=1e-12):
                return True
            if can_round and round(candidate, places) == round(target, places):
                return True
    return False


@dataclass
class ResultRegistry:
    fs: WorkspaceFS
    results: Dict[str, Result] = field(default_factory=dict)

    @classmethod
    def load(cls, fs: WorkspaceFS) -> "ResultRegistry":
        registry = cls(fs)
        if fs.exists(REGISTRY_PATH):
            data = json.loads(fs.read_text(REGISTRY_PATH))
            registry.results = {item["id"]: Result(**item) for item in data.get("results", [])}
        return registry

    def save(self) -> None:
        payload = {"results": [asdict(r) for r in self.results.values()]}
        self.fs.write_text(REGISTRY_PATH, json.dumps(payload, ensure_ascii=False, indent=2))

    def reset(self) -> None:
        """新任务开始时清空，避免上一篇论文的结果混进来"""
        self.results = {}
        self.save()

    def _next_id(self, prefix: str) -> str:
        used = [int(rid[1:]) for rid in self.results if rid.startswith(prefix)]
        return f"{prefix}{max(used, default=0) + 1}"

    def _run_output(self, run_id: str) -> str:
        info = self.fs.get_run_info(run_id)
        if info is None:
            raise GroundingError(f"run {run_id} 不存在")
        if info.get("status") != "success":
            raise GroundingError(f"run {run_id} 执行失败，不能从中登记结果")
        if not info.get("stdout"):
            return ""
        return self.fs.read_text(info["stdout"])

    def record_value(
        self,
        subproblem: str,
        name: str,
        value: str,
        run_id: str,
        unit: str = "",
        description: str = "",
    ) -> Result:
        value = str(value).strip()
        output = self._run_output(run_id)
        if not value_in_output(value, output):
            raise GroundingError(
                f"值 {value} 没有出现在 {run_id} 的输出中。请先 print 出这个结果，再用那次执行的 run_id 登记。"
            )
        kind = "number" if _parse_number(value) is not None else "text"
        result = Result(
            id=self._next_id("R"), kind=kind, subproblem=subproblem, name=name.strip(),
            value=value, run_id=run_id, unit=unit.strip(), description=description.strip(),
        )
        self.results[result.id] = result
        self.save()
        return result

    def record_figure(self, subproblem: str, run_id: str, artifact: str, caption: str) -> Result:
        self._run_output(run_id)
        figure_id = self._next_id("F")
        artifact_name = artifact.split("/")[-1]
        suffix = artifact_name.rsplit(".", 1)[-1] if "." in artifact_name else "png"
        path = self.fs.promote_artifact(run_id, artifact_name, f"figures/{figure_id}.{suffix}")
        result = Result(
            id=figure_id, kind="figure", subproblem=subproblem, name=caption.strip(),
            value="", run_id=run_id, path=path,
        )
        self.results[result.id] = result
        self.save()
        return result

    def get(self, result_id: str) -> Optional[Result]:
        return self.results.get(result_id)

    def for_subproblem(self, subproblem: Optional[str]) -> List[Result]:
        return [r for r in self.results.values() if subproblem is None or r.subproblem == subproblem]

    def to_prompt_table(self, subproblem: Optional[str] = None) -> str:
        """给写作模型看的结果表"""
        rows = self.for_subproblem(subproblem)
        if not rows:
            return "（没有登记的结果）"
        lines = ["| 引用 | 子问题 | 名称 | 值 | 说明 |", "|---|---|---|---|---|"]
        for r in rows:
            ref = f"{{{{{r.id[0]}:{r.id}}}}}"
            shown = r.path if r.kind == "figure" else r.display()
            lines.append(f"| {ref} | {r.subproblem} | {r.name} | {shown} | {r.description} |")
        return "\n".join(lines)

    def render(self, text: str) -> str:
        """把正文中的引用替换成真实内容

        - 独占一行的 {{F:F1}} 渲染为图片和图题，按首次出现顺序编号；
        - 行内的 {{F:F1}} 渲染为"图 k"；
        - {{R:R1}} 渲染为数值和单位；
        - 未知引用保留原样，由校验环节报告。
        """
        figure_numbers: Dict[str, int] = {}
        for ref in find_refs(text):
            if ref.startswith("F") and ref in self.results and ref not in figure_numbers:
                figure_numbers[ref] = len(figure_numbers) + 1

        def figure_block(match: re.Match) -> str:
            result = self.results.get(match.group(1))
            if result is None:
                return match.group(0)
            label = f"图{figure_numbers[result.id]} {result.name}"
            return f"![{label}]({result.path})\n\n*{label}*"

        text = re.sub(r"^[ \t]*\{\{\s*F:\s*(F\d+)\s*\}\}[ \t]*$", figure_block, text, flags=re.MULTILINE)

        def inline(match: re.Match) -> str:
            result = self.results.get(match.group(2))
            if result is None or result.id[0] != match.group(1):
                return match.group(0)
            if result.kind == "figure":
                return f"图{figure_numbers[result.id]}"
            return result.display()

        return REF_PATTERN.sub(inline, text)


def find_refs(text: str) -> List[str]:
    return [m.group(2) for m in REF_PATTERN.finditer(text)]
