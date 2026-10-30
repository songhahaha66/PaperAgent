"""Historical R/F references retained for reading and rendering existing documents.

New research uses execution records and result files directly. This projection
is not an acceptance mechanism and is never exposed as a model tool.
"""

import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from services.file_services.workspace_fs import WorkspaceFS
from ai_system.text_syntax import math_spans as iter_math_spans

REGISTRY_PATH = ".system/results.json"


def math_body(text: str) -> str:
    return re.sub(r"^\\begin\{[^{}]+\}|\\end\{[^{}]+\}$|^\\[\[(]|\\[\])]$|^\$+|\$+$", "", text).strip()

REF_PATTERN = re.compile(r"\{\{\s*([RF]):\s*([RF]\d+)\s*\}\}")
FIGURE_LINE = re.compile(r"^[ \t]*\{\{\s*F:\s*(F\d+)\s*\}\}[ \t]*$", re.MULTILINE)
# 图名里自带的编号（"图3 遗忘因子……"），渲染时统一重新编号，留着会变成"图2 图3 ……"
FIGURE_NAME_NUMBER = re.compile(r"^\s*(图|Figure|Fig\.?)\s*\d+\s*[:：.、]?\s*", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
SERIES_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
SERIES_BLOCK = re.compile(r"\[[^\[\]]*\]", re.DOTALL)
SERIES_BODY = re.compile(rf"\s*{SERIES_NUMBER}(?:(?:\s*,\s*|\s+){SERIES_NUMBER})*\s*,?\s*")
# 结论里手写的小数或科学计数（整数常是个数、年份，不自动换）
LITERAL_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+(?:[eE][-+]?\d+)?%?|[eE][-+]?\d+%?|%)(?![A-Za-z0-9_.])")


@dataclass
class Result:
    id: str
    kind: str  # number | series | text | figure
    subproblem: str
    name: str
    value: str
    run_id: str
    unit: str = ""
    description: str = ""
    path: str = ""  # 图片的 outputs/ 路径
    meaning: dict | None = None  # declared semantics, not certified by stdout matching

    def display(self, with_unit: bool = True) -> str:
        unit = self.unit.strip()
        if not with_unit or not unit or self.value.endswith(unit):
            return self.value
        return f"{self.value}{unit}" if unit in ("%", "‰") else f"{self.value} {unit}"


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


def numeric_series(value: str) -> Optional[list]:
    """One nonempty finite numeric vector, serialized as JSON."""
    try:
        items = json.loads(value)
    except (ValueError, TypeError):
        return None
    if not isinstance(items, list) or not items:
        return None
    if not all(type(item) in (int, float) and math.isfinite(item) for item in items):
        return None
    return items


def value_in_output(value: str, output: str) -> bool:
    """value 是否能由 output 中的某个数（四舍五入到同样位数，或换算成百分数）得到"""
    series = numeric_series(value)
    if series is not None:
        # Match one complete printed vector, including NumPy's whitespace format.
        # Values scattered across output or reordered/partial vectors are not evidence.
        for block in SERIES_BLOCK.finditer(output):
            body = block.group()[1:-1]
            if not SERIES_BODY.fullmatch(body):
                continue
            tokens = re.findall(SERIES_NUMBER, body)
            if len(tokens) == len(series) and all(value_in_output(str(item), str(float(token))) for item, token in zip(series, tokens)):
                return True
        return False
    target = _parse_number(value)
    if target is None:
        return value.strip() != "" and value.strip() in output
    places = _decimals(value)
    # 只有至少两位有效数字时才允许四舍五入匹配，否则 0.9 这类值几乎能对上任何输出
    is_percent = value.strip().endswith("%")
    can_round = places < 15 and (places > 0 or is_percent) and _significant_digits(value) >= 2
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
class Claim:
    """一条结论：陈述 + 证据强度 + 依据的登记结果（结论图的节点，证据是它指向结果的边）"""

    id: str
    subproblem: str
    statement: str
    strength: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class ResultRegistry:
    fs: WorkspaceFS
    results: Dict[str, Result] = field(default_factory=dict)
    claims: Dict[str, Claim] = field(default_factory=dict)

    staged_history: List[Result] = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, fs: WorkspaceFS) -> "ResultRegistry":
        registry = cls(fs)
        if fs.exists(REGISTRY_PATH):
            data = json.loads(fs.read_text(REGISTRY_PATH))
            registry.results = {item["id"]: Result(**item) for item in data.get("results", [])}
            registry.claims = {item["id"]: Claim(**item) for item in data.get("claims", [])}
            registry.staged_history = [Result(**item) for item in data.get("staged_history", [])]
        return registry

    def save(self) -> None:
        payload = {
            "results": [asdict(r) for r in self.results.values()],
            "claims": [asdict(c) for c in self.claims.values()],
        }
        if self.staged_history:
            payload["staged_history"] = [asdict(r) for r in self.staged_history]
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        if not self.fs.exists(REGISTRY_PATH) or self.fs.read_text(REGISTRY_PATH) != content:
            self.fs.write_text(REGISTRY_PATH, content)

    def reset(self) -> None:
        """新任务开始时清空，避免上一篇论文的结果混进来"""
        self.results = {}
        self.claims = {}
        self.staged_history = []
        self.save()

    def claims_table(self) -> str:
        """结论表：陈述里的引用替换为"真实数值⟨编号⟩"，供一致性审查和写作使用

        只给数值时，写作模型要从"依据"列的一串编号里猜哪个值对应哪个编号；v2n 的问题分析把区间、z、p
        对应的引用整段写错位（"95% 区间 135……Z 为 172……p 为 1.519"）。每个值后面紧跟编号就不用猜。
        """
        if not self.claims:
            return "（没有登记的结论）"
        lines = ["| 结论 | 子问题 | 证据强度 | 陈述 | 依据 |", "|---|---|---|---|---|"]
        for c in self.claims.values():
            marked = REF_PATTERN.sub(
                lambda m: f"{m.group(0)}⟨{m.group(2)}⟩" if m.group(2) in self.results else m.group(0), c.statement
            )
            statement = self.render(marked).replace("\n", " ").replace("|", "/")
            lines.append(f"| {c.id} | {c.subproblem} | {c.strength} | {statement} | {', '.join(c.evidence) or '无'} |")
        return "\n".join(lines)

    def get(self, result_id: str) -> Optional[Result]:
        return self.results.get(result_id)

    def for_subproblem(self, subproblem: Optional[str]) -> List[Result]:
        return [r for r in self.results.values() if subproblem is None or r.subproblem == subproblem]

    def to_prompt_table(self, subproblem: Optional[str] = None) -> str:
        """给写作模型看的结果表"""
        rows = self.for_subproblem(subproblem)
        if not rows:
            return "（没有登记的结果）"
        lines = [
            "引用渲染时会自动带上单位，正文在引用后不要再写单位。",
            "数值序列引用代表整个序列，不支持在引用后追加 [0] 等索引或算术运算。没有逐项登记时，整组数据作为一个序列展示。",
            "",
            "| 引用 | 子问题 | 名称 | 值（含单位） | 说明 | 对象/范围/口径（登记者声明，须核实） |",
            "|---|---|---|---|---|---|",
        ]
        for r in rows:
            ref = f"{{{{{r.id[0]}:{r.id}}}}}"
            shown = r.path if r.kind == "figure" else r.display()
            lines.append(f"| {ref} | {r.subproblem} | {r.name} | {shown} | {r.description} | {json.dumps(r.meaning, ensure_ascii=False) if r.meaning else '语义未登记'} |")
        return "\n".join(lines)

    def series_index_issue(self, text: str) -> str | None:
        for ref in re.finditer(r"\{\{\s*R:\s*(R\d+)\s*\}\}\s*\[\s*\d+\s*\]", text):
            result=self.get(ref.group(1))
            if result and result.kind=='series':
                return '数值序列引用不支持 [0] 等索引。引用代表完整序列，请整组展示，或使用已经逐项登记的真实引用；不要把同一个序列填到多个单值单元格。'

    def render(self, text: str, *, latex: bool = False, links: bool = False) -> str:
        """把正文中的引用替换成真实内容

        - 独占一行的 {{F:F1}} 渲染为图片和图题，按图片首次出现的顺序编号；同一张图再次独占一行时整行去掉；
        - 行内的 {{F:F1}} 渲染为"图 k"；
        - {{R:R1}} 渲染为数值和单位；
        - 未知引用保留原样，由校验环节报告。
        """
        if issue:=self.series_index_issue(text):raise ValueError(issue)
        from ..export.latex import escape_plain
        encode = escape_plain if latex else str
        figure_numbers: Dict[str, int] = {}
        placed = [m.group(1) for m in FIGURE_LINE.finditer(text)]
        for ref in placed + find_refs(text):
            if ref.startswith("F") and ref in self.results and ref not in figure_numbers:
                figure_numbers[ref] = len(figure_numbers) + 1
        shown: set = set()

        def figure_block(match: re.Match) -> str:
            result = self.results.get(match.group(1))
            if result is None:
                return match.group(0)
            number = figure_numbers[result.id]
            # v2l 的同一张图在摘要、正文、备忘录里各插了一次；重复的整行去掉
            # （写成"见图k"时 v2n 的备忘录末尾连着五行"（见图1）……（见图5）"）
            if result.id in shown:
                return ""
            shown.add(result.id)
            if latex:
                if any(c in result.path for c in "{}%\\\r\n"):
                    raise ValueError("登记图片路径不能用于 LaTeX")
                caption = escape_plain(FIGURE_NAME_NUMBER.sub('', result.name))
                return ("\\begin{figure}[H]\n\\centering\n"
                        + r"\includegraphics[width=0.85\linewidth]{\detokenize{" + result.path + "}}\n"
                        + r"\caption{" + caption + "}\n" + r"\label{fig:" + result.id + "}\n\\end{figure}")
            label = f"图{number} {FIGURE_NAME_NUMBER.sub('', result.name)}"
            image=f"![{label}]({result.path})"
            if links:image=f"[{image}](https://paperagent.local/results/{result.id})"
            return image+f"\n\n*{label}*"

        text = FIGURE_LINE.sub(figure_block, text)

        # 公式里的引用只放数值，单位会破坏 LaTeX
        math_spans = list(iter_math_spans(text))
        parts: List[str] = []
        cursor = 0
        for match in REF_PATTERN.finditer(text):
            if match.start() < cursor:
                continue
            result = self.results.get(match.group(2))
            parts.append(text[cursor:match.start()])
            cursor = match.end()
            if result is None or result.id[0] != match.group(1):
                parts.append(match.group(0))
            elif result.kind == "figure":
                prefix = "" if re.search(r"图\s*$", text[:match.start()]) else "图~"
                parts.append(prefix + r"\ref{fig:" + result.id + "}" if latex else (f"[图{figure_numbers[result.id]}](https://paperagent.local/results/{result.id})" if links else f"图{figure_numbers[result.id]}"))
            elif latex and result.kind == "series":
                values = numeric_series(result.value)
                # Bound presentation precision without changing registered evidence.
                # Long vectors wrap as a single sequence, not a different matrix.
                tokens = []
                for value in values:
                    shown = format(value, ".8g")
                    if "e" in shown:
                        mantissa, exponent = shown.split("e")
                        shown = mantissa + r"\times10^{" + str(int(exponent)) + "}"
                    tokens.append(shown)
                in_math = any(start <= match.start() < end for start, end in math_spans)
                if not in_math:
                    series = "[" + r",\allowbreak ".join(r"\(" + token + r"\)" for token in tokens) + "]"
                    parts.append(series + (" " + encode(result.unit) if result.unit else ""))
                elif sum(map(len, tokens)) <= 40:
                    parts.append("[" + ", ".join(tokens) + "]")
                else:
                    rows = [", ".join(tokens[index:index + 4]) for index in range(0, len(tokens), 4)]
                    parts.append(r"\begin{gathered}[" + r",\\ ".join(rows) + r"]\end{gathered}")
            elif any(start <= match.start() < end for start, end in math_spans):
                parts.append(encode(result.display(with_unit=False)))
            else:
                if links and not latex:
                    import html
                    parts.append(f'<a href="https://paperagent.local/results/{result.id}" title="查看实验来源">{html.escape(result.display())}</a>')
                else:parts.append(encode(result.display()))
                # 写作模型常在引用后再写一遍单位，去掉重复
                unit = result.unit.strip()
                rest = text[cursor:]
                stripped = rest.lstrip(" ")
                if unit and stripped.startswith(unit):
                    cursor += len(rest) - len(stripped) + len(unit)
        parts.append(text[cursor:])
        return "".join(parts)


def publication_text(text: str) -> str:
    """Remove prompt-only provenance annotations, never their surrounding prose."""
    return re.sub(r"⟨[RCF]\d+(?:\s*[,，]\s*[RCF]\d+)*⟩", "", text)


def find_refs(text: str) -> List[str]:
    return [m.group(2) for m in REF_PATTERN.finditer(text)]
