import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

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
1. 读取 plan.md 中的章节列表，统计「✅ 已完成」vs「⬜ 待写」/「⏳ 进行中」数量
2. 检查论文文件（paper.md / paper.docx）是否存在且有实质内容
3. 如果 plan.md 中所有章节都标记为 ✅ 且论文文件有内容 → complete=true
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
            return f"{target} 存在，大小 {file_size} 字节"

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

    async def review(self, user_input: str) -> ReviewResult:
        plan_content = self._read_plan()
        paper_status = self._read_paper_status()

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
