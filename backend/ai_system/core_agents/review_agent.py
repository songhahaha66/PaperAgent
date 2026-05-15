import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """\
你是一个论文写作任务审查员（ReviewAgent）。你的职责是根据 plan.md 和论文产物的当前状态，
判断 MainAgent 的写作任务是否已完成，并在未完成时给出精确的续写指令。

## 输出格式（严格 JSON）
你必须输出一个合法的 JSON 对象，不要输出任何其他内容：
```json
{
  "complete": true/false,
  "reason": "判断理由（简短）",
  "continuation_prompt": "如果 complete=false，给 MainAgent 的续写指令；complete=true 时为空字符串"
}
```

## 判断标准
1. 读取 plan.md 中的章节列表，统计「✅ 已完成」vs「⬜ 待写」/「⏳ 进行中」/「❌ 阻塞」数量
2. 检查论文文件（paper.md / paper.docx）是否存在且有实质内容
3. 如果 plan.md 中所有章节都标记为 ✅ 且论文文件有内容、没有阻塞项 → complete=true
4. 否则 complete=false，在 continuation_prompt 中列出具体缺失的章节和下一步操作

## continuation_prompt 要求（当 complete=false 时）
- 必须具体指出哪些章节还未完成
- 必须给出明确的下一步工具调用指令（不要笼统地说"继续写"）
- 必须包含"不要回复文字说明，直接调用工具"这样的强制指令
- 使用中文\
"""


@dataclass
class ReviewResult:
    complete: bool
    reason: str
    continuation_prompt: str


class ReviewAgent:

    def __init__(self, llm: BaseLanguageModel, workspace_dir: str,
                 output_mode: str = "markdown"):
        self.llm = llm
        self.workspace_dir = workspace_dir
        self.output_mode = output_mode

    def _read_plan(self) -> str:
        plan_path = os.path.join(self.workspace_dir, "plan.md")
        if not os.path.exists(plan_path):
            return "plan.md 不存在"
        with open(plan_path, "r", encoding="utf-8") as f:
            return f.read().strip() or "plan.md 为空"

    def _read_paper_status(self) -> str:
        target = "paper.docx" if self.output_mode == "word" else "paper.md"
        paper_path = os.path.join(self.workspace_dir, target)

        if not os.path.exists(paper_path):
            return f"{target} 不存在"

        file_size = os.path.getsize(paper_path)
        if file_size == 0:
            return f"{target} 存在但为空（0 字节）"

        if self.output_mode != "markdown":
            return self._read_word_status(Path(paper_path), file_size)

        with open(paper_path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")
        sections = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped.lstrip("#").strip()
                sections.append(f"{'  ' * (level - 1)}{'#' * level} {title}")

        total_chars = len(content)
        non_header_lines = [
            l for l in lines if l.strip() and not l.strip().startswith("#")
        ]

        summary = f"{target}: {total_chars} 字符, {len(non_header_lines)} 行正文内容\n"
        if sections:
            summary += "章节结构:\n" + "\n".join(sections)
        else:
            summary += "（未检测到章节标题）"

        return summary

    @staticmethod
    def _docx_outline(docx_path: Path) -> dict:
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

        with zipfile.ZipFile(docx_path) as archive:
            document_root = ET.fromstring(archive.read("word/document.xml"))
            style_names = {}
            if "word/styles.xml" in archive.namelist():
                styles_root = ET.fromstring(archive.read("word/styles.xml"))
                for style in styles_root.findall(".//w:style", ns):
                    style_id = style.attrib.get(f"{{{ns['w']}}}styleId", "")
                    name_el = style.find("w:name", ns)
                    style_names[style_id] = (
                        name_el.attrib.get(f"{{{ns['w']}}}val", style_id)
                        if name_el is not None else style_id
                    )
            media_count = sum(1 for name in archive.namelist() if name.startswith("word/media/"))

        paragraphs = []
        headings = []
        for para in document_root.findall(".//w:p", ns):
            text = "".join(t.text or "" for t in para.findall(".//w:t", ns)).strip()
            style_el = para.find("./w:pPr/w:pStyle", ns)
            style_id = style_el.attrib.get(f"{{{ns['w']}}}val", "") if style_el is not None else ""
            style_name = style_names.get(style_id, style_id)
            paragraphs.append({"text": text, "style": style_name})
            if style_name.lower().startswith("heading") and text:
                headings.append((style_name.lower(), text))

        text = "\n".join(p["text"] for p in paragraphs if p["text"])
        table_count = len(document_root.findall(".//w:tbl", ns))
        return {
            "paragraphs": paragraphs,
            "headings": headings,
            "text": text,
            "table_count": table_count,
            "media_count": media_count,
        }

    def _read_word_status(self, paper_path: Path, file_size: int) -> str:
        try:
            paper = self._docx_outline(paper_path)
        except Exception as exc:
            return f"paper.docx 存在，大小 {file_size} 字节；无法解析 Word 结构: {exc}"

        issues = []
        template_path = Path(self.workspace_dir) / ".system" / "_template_original.docx"
        if template_path.exists():
            try:
                template = self._docx_outline(template_path)
                if paper["table_count"] != template["table_count"]:
                    issues.append(
                        f"表格数量不一致：模板 {template['table_count']} 个，当前 {paper['table_count']} 个"
                    )
                if paper["headings"] != template["headings"]:
                    issues.append("标题层级/顺序/文本与模板不一致")
                    for idx, (expected, actual) in enumerate(zip(template["headings"], paper["headings"]), 1):
                        if expected != actual:
                            issues.append(f"第 {idx} 个标题应为 {expected}，当前为 {actual}")
                            break
                    if len(paper["headings"]) != len(template["headings"]):
                        issues.append(
                            f"标题数量不一致：模板 {len(template['headings'])} 个，当前 {len(paper['headings'])} 个"
                        )
            except Exception as exc:
                issues.append(f"无法解析模板结构: {exc}")

        markdown_heading_leaks = [
            text for _, text in paper["headings"]
            if re.search(r"(^[*#`]+|[*#`]+$)", text)
        ]
        if markdown_heading_leaks:
            issues.append(f"标题中残留 Markdown 标记: {markdown_heading_leaks[:3]}")

        placeholder_count = sum(
            paper["text"].count(token)
            for token in ["待写", "TODO", "占位"]
        )
        if placeholder_count:
            issues.append(f"仍存在 {placeholder_count} 个未完成占位标记")

        summary = (
            f"paper.docx: {file_size} 字节, "
            f"{len(paper['paragraphs'])} 段, {len(paper['headings'])} 个标题, "
            f"{paper['table_count']} 个表格, {paper['media_count']} 个媒体文件, "
            f"{len(paper['text'])} 字符"
        )
        if paper["headings"]:
            preview = "\n".join(f"- {style}: {text}" for style, text in paper["headings"][:30])
            summary += "\n标题结构预览:\n" + preview
        if issues:
            summary += "\nWord结构验收问题:\n" + "\n".join(f"- {issue}" for issue in issues)
        else:
            summary += "\nWord结构验收: 未发现模板结构问题"
        return summary

    def _deterministic_blockers(self, plan_content: str, paper_status: str) -> list[str]:
        blockers = []
        if plan_content in {"plan.md 不存在", "plan.md 为空"}:
            blockers.append(plan_content)
        if "不存在" in paper_status or "存在但为空" in paper_status:
            blockers.append(paper_status.split("\n", 1)[0])
        if self.output_mode == "word" and "Word结构验收问题:" in paper_status:
            blockers.append("Word模板结构验收未通过")

        statuses = self._extract_plan_statuses(plan_content)
        if not statuses and plan_content not in {"plan.md 不存在", "plan.md 为空"}:
            blockers.append("plan.md 未包含可解析的计划状态表")
        if any(status in {"pending", "in_progress"} for status in statuses):
            blockers.append("plan.md 仍包含待写或进行中条目")
        if "blocked" in statuses:
            blockers.append("plan.md 仍包含阻塞条目")
        return blockers

    @staticmethod
    def _extract_plan_statuses(plan_content: str) -> list[str]:
        plan_body = re.split(r"<!--\s*template-constraints:start\s*-->", plan_content, maxsplit=1)[0]
        rows = []
        for line in plan_body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or "---" in stripped:
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 3:
                rows.append(cells)

        if not rows:
            return []

        headers = [cell.lower() for cell in rows[0]]
        status_col = next(
            (idx for idx, header in enumerate(headers) if "状态" in header or "status" in header),
            2,
        )
        statuses = []
        for row in rows[1:]:
            raw = row[status_col] if status_col < len(row) else ""
            text = raw.strip().lower()
            if any(token in text for token in ["❌", "阻塞", "blocked", "失败", "failed"]):
                statuses.append("blocked")
            elif any(token in text for token in ["⬜", "待写", "pending", "todo"]):
                statuses.append("pending")
            elif any(token in text for token in ["⏳", "进行", "progress", "doing", "current"]):
                statuses.append("in_progress")
            elif any(token in text for token in ["✅", "完成", "done", "completed", "complete"]):
                statuses.append("completed")
        return statuses

    async def review(self, user_input: str) -> ReviewResult:
        plan_content = self._read_plan()
        paper_status = self._read_paper_status()
        blockers = self._deterministic_blockers(plan_content, paper_status)
        if blockers:
            return ReviewResult(
                complete=False,
                reason="；".join(blockers),
                continuation_prompt=(
                    "当前任务未通过自动验收。请先修复以下问题："
                    + "；".join(blockers)
                    + "。Word模板模式下必须保留模板标题层级、章节顺序、表格数量和占位栏位；"
                    "如果提示标题层级/顺序/文本与模板不一致，先调用 repair_template_structure()；"
                    "然后继续补齐内容并重新验收。不要回复文字说明，直接调用工具。"
                ),
            )

        user_msg = (
            f"## 用户原始需求\n{user_input}\n\n"
            f"## plan.md 内容\n{plan_content}\n\n"
            f"## 论文产物状态\n{paper_status}\n\n"
            "请根据以上信息判断任务是否完成，输出 JSON。"
        )

        messages = [
            SystemMessage(content=REVIEW_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ]

        try:
            response = await self.llm.ainvoke(messages)
            raw = response.content if hasattr(response, "content") else str(response)
            return self._parse_response(raw)
        except Exception as e:
            logger.error(f"ReviewAgent LLM 调用失败: {e}", exc_info=True)
            return ReviewResult(
                complete=False,
                reason=f"ReviewAgent 调用异常: {e}",
                continuation_prompt=(
                    "⚠️ 审查失败，请调用 get_paper_status 检查当前状态，"
                    "然后按 plan.md 继续逐章节写作。不要回复文字说明，直接调用工具！"
                ),
            )

    @staticmethod
    def _parse_response(raw: str) -> ReviewResult:
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
            return ReviewResult(
                complete=bool(data.get("complete", False)),
                reason=data.get("reason", ""),
                continuation_prompt=data.get("continuation_prompt", ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"ReviewAgent 返回解析失败: {e}, raw={raw[:200]}")
            return ReviewResult(
                complete=False,
                reason=f"JSON 解析失败: {e}",
                continuation_prompt=(
                    "⚠️ 审查结果无法解析。请调用 get_paper_status 查看当前进度，"
                    "然后继续按 plan.md 逐章节写作。不要回复文字说明，直接调用工具！"
                ),
            )
