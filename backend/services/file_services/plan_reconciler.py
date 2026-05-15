"""
Workspace plan reconciliation.

The model can write a plan, but the workspace is the source of truth for
whether writing, charts, and final artifacts actually exist.
"""

from __future__ import annotations

import json
import re
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .template_contract import CONTRACT_PATH


PLAN_PHASES = [
    {"id": "requirements", "title": "需求澄清", "description": "明确用户目标、输出格式和约束"},
    {"id": "design", "title": "方案设计", "description": "确定章节结构、数据/实验和产物形式"},
    {"id": "tasks", "title": "任务拆解", "description": "形成可执行、可追踪的章节和产物任务"},
    {"id": "implement", "title": "执行生成", "description": "逐项生成内容、代码、图表和最终文档"},
    {"id": "verify", "title": "验收检查", "description": "核对计划、正文、附件和最终产物"},
]

CONSTRAINTS_START = "<!-- template-constraints:start -->"
CONSTRAINTS_END = "<!-- template-constraints:end -->"


@dataclass
class PlanEvidence:
    document_text: str
    document_paths: List[str]
    document_char_count: int
    document_has_headings: bool
    docx_image_count: int
    output_images: List[str]
    generated_files: List[str]

    @property
    def has_document(self) -> bool:
        return self.document_char_count > 200

    @property
    def has_output_image(self) -> bool:
        return bool(self.output_images) or self.docx_image_count > 0

    def to_summary(self) -> Dict[str, Any]:
        return {
            "document_paths": self.document_paths,
            "document_char_count": self.document_char_count,
            "document_has_headings": self.document_has_headings,
            "docx_image_count": self.docx_image_count,
            "output_images": self.output_images,
            "generated_files": self.generated_files[:30],
        }


class PlanReconciler:
    """Build and refresh structured writing plans from local workspace state."""

    def __init__(self, workspace_path: Path | str):
        self.workspace_path = Path(workspace_path)

    def ensure_plan_json(self, sync_markdown: bool = True) -> Dict[str, Any]:
        plan_md_path = self.workspace_path / "plan.md"
        if plan_md_path.exists():
            plan_content = plan_md_path.read_text(encoding="utf-8")
        else:
            plan_content = "# 写作计划\n\n等待AI分析需求并制定写作计划...\n"

        previous_plan = self._read_existing_plan()
        previous_revision = int(previous_plan.get("revision", 0)) if previous_plan else 0
        structured_plan = self.build_from_markdown(
            plan_content,
            source="workspace_reconciled",
            revision=previous_revision if previous_plan else 1,
        )
        if previous_plan and self._stable_plan(previous_plan) == self._stable_plan(structured_plan):
            return previous_plan

        structured_plan["revision"] = previous_revision + 1 if previous_plan else 1
        structured_plan["updated_at"] = datetime.now().isoformat()
        self.write_plan_json(structured_plan)
        if sync_markdown:
            self.write_plan_markdown(structured_plan)
        return structured_plan

    def build_from_markdown(
        self,
        plan_content: str,
        source: str = "update_plan_markdown",
        revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        evidence = self._collect_evidence()
        items, title = self._parse_markdown_plan(plan_content)
        if self._is_placeholder_plan(items, plan_content) and evidence.has_document:
            items = self._synthesize_items_from_evidence(evidence)
        items = self._reconcile_items(items, evidence)
        structured_plan = self._assemble_plan(title, items, plan_content, source, evidence, revision=revision)
        return structured_plan

    def write_plan_json(self, structured_plan: Dict[str, Any]) -> None:
        plan_json_path = self.workspace_path / "plan.json"
        plan_json_path.write_text(json.dumps(structured_plan, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_plan_markdown(self, structured_plan: Dict[str, Any]) -> None:
        lines = [f"# {structured_plan.get('title') or '写作计划'}", ""]
        lines.extend([
            "| 序号 | 章节/任务 | 状态 | 说明 |",
            "|------|-----------|------|------|",
        ])
        for item in structured_plan.get("items", []):
            status_symbol = {
                "completed": "✅ 已完成",
                "in_progress": "⏳ 进行中",
                "blocked": "❌ 阻塞",
                "pending": "⬜ 待写",
            }.get(item.get("status"), "⬜ 待写")
            description = (item.get("description") or "").replace("\n", " ").strip()
            lines.append(f"| {item.get('order', '')} | {item.get('title', '')} | {status_symbol} | {description} |")

        current = structured_plan.get("current_focus")
        lines.append("")
        if current:
            lines.append(f"**当前阶段**: {current.get('title', '待执行')}")
        else:
            lines.append("**当前阶段**: 已完成")
        lines.append("")
        lines.append(f"**计划进度**: {structured_plan.get('stats', {}).get('progress_percent', 0)}%")
        template_contract = structured_plan.get("constraints", {}).get("template_contract") or self._read_template_contract()
        if template_contract:
            lines.extend(["", self._format_template_constraints(template_contract)])
        (self.workspace_path / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def append_template_constraints(self, plan_content: str) -> str:
        """Attach template hard constraints to plan.md content without duplication."""
        clean_content = self._strip_template_constraints(plan_content).rstrip()
        template_contract = self._read_template_contract()
        if not template_contract:
            return clean_content + "\n"
        return clean_content + "\n\n" + self._format_template_constraints(template_contract) + "\n"

    def _parse_markdown_plan(self, plan_content: str) -> tuple[List[Dict[str, Any]], str]:
        plan_content = self._strip_template_constraints(plan_content)
        title = "写作计划"
        for line in plan_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or title
                break

        rows: List[List[str]] = []
        for line in plan_content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or "---" in stripped:
                continue
            cells = self._split_markdown_table_row(stripped)
            if len(cells) >= 3:
                rows.append(cells)

        headers = rows[0] if rows else []
        data_rows = rows[1:] if rows else []

        def find_col(names: List[str], fallback: int) -> int:
            lowered = [h.lower() for h in headers]
            for idx, header in enumerate(lowered):
                if any(name in header for name in names):
                    return idx
            return fallback

        order_col = find_col(["序号", "order", "编号", "id"], 0)
        title_col = find_col(["章节", "任务", "名称", "title", "section", "step"], 1)
        status_col = find_col(["状态", "status"], 2)
        description_col = find_col(["说明", "描述", "description", "备注"], 3)

        items: List[Dict[str, Any]] = []
        for index, row in enumerate(data_rows, start=1):
            if not row or all(not cell for cell in row):
                continue
            raw_order = row[order_col] if order_col < len(row) else str(index)
            raw_title = row[title_col] if title_col < len(row) else f"任务 {index}"
            raw_status = row[status_col] if status_col < len(row) else ""
            raw_description = row[description_col] if description_col < len(row) else ""
            raw_status, raw_description = self._repair_status_description(raw_status, raw_description)
            order_match = re.search(r"\d+", raw_order)
            order = int(order_match.group()) if order_match else index
            status = self._normalize_plan_status(raw_status)
            items.append({
                "id": f"task-{order}",
                "order": order,
                "title": re.sub(r"^\s*\d+[\.\、]\s*", "", raw_title).strip() or f"任务 {order}",
                "status": status,
                "status_label": self._plan_status_label(status),
                "description": raw_description.strip(),
                "phase": "write",
                "depends_on": [f"task-{order - 1}"] if order > 1 else [],
                "raw_status": raw_status.strip(),
            })

        if not items:
            summary = plan_content.strip()
            if summary:
                items.append({
                    "id": "task-1",
                    "order": 1,
                    "title": summary.splitlines()[0].lstrip("#").strip()[:80] or "等待制定计划",
                    "status": "pending",
                    "status_label": self._plan_status_label("pending"),
                    "description": summary[:300],
                    "phase": "plan",
                    "depends_on": [],
                    "raw_status": "",
                })

        return sorted(items, key=lambda item: item["order"]), title

    def _is_placeholder_plan(self, items: List[Dict[str, Any]], plan_content: str) -> bool:
        if not items:
            return True
        content = plan_content.strip()
        if len(items) == 1 and any(token in content for token in ["等待AI", "等待 AI", "等待制定计划", "尚未制定"]):
            return True
        return False

    def _synthesize_items_from_evidence(self, evidence: PlanEvidence) -> List[Dict[str, Any]]:
        heading_titles = self._extract_document_headings(evidence.document_text)
        titles = ["论文结构"]
        titles.extend(heading_titles[:10])
        if evidence.has_output_image and not any(self._has_any(title.lower(), ["图", "figure", "chart"]) for title in titles):
            titles.append("图表与图片产物")

        items: List[Dict[str, Any]] = []
        for order, title in enumerate(titles, start=1):
            items.append({
                "id": f"task-{order}",
                "order": order,
                "title": title,
                "status": "completed",
                "status_label": self._plan_status_label("completed"),
                "description": "由工作空间实际文档和产物自动识别",
                "phase": "write",
                "depends_on": [f"task-{order - 1}"] if order > 1 else [],
                "raw_status": "workspace_evidence",
                "status_source": "workspace_evidence",
            })
        return items

    def _extract_document_headings(self, document_text: str) -> List[str]:
        headings: List[str] = []
        patterns = [
            r"(?m)^#{1,6}\s+(.+)$",
            r"(?m)^\s*\d+(?:\.\d+)*[\.、]\s*(.{2,80})$",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, document_text):
                title = re.sub(r"\s+", " ", match.group(1)).strip()
                if title and title not in headings:
                    headings.append(title)
        if not headings:
            for keyword in ["摘要", "引言", "方法", "实验结果", "讨论", "结论", "参考文献"]:
                if keyword in document_text and keyword not in headings:
                    headings.append(keyword)
        return headings

    def _repair_status_description(self, raw_status: str, raw_description: str) -> tuple[str, str]:
        if raw_description.strip():
            return raw_status, raw_description
        match = re.match(r"^([✅⬜⏳❌]\s*(?:已完成|完成|待写|进行中|阻塞|失败)?)\s+(.+)$", raw_status.strip())
        if match:
            return match.group(1), match.group(2)
        return raw_status, raw_description

    def _reconcile_items(self, items: List[Dict[str, Any]], evidence: PlanEvidence) -> List[Dict[str, Any]]:
        if not items:
            return items

        reconciled = []
        for item in items:
            new_item = dict(item)
            evidence_status = self._infer_item_status(item, evidence)
            if evidence_status:
                new_item["status"] = evidence_status
                new_item["status_label"] = self._plan_status_label(evidence_status)
                new_item["status_source"] = "workspace_evidence"
            reconciled.append(new_item)

        if evidence.has_document and evidence.document_char_count > 1500:
            for item in reconciled:
                if item["status"] == "in_progress" and self._infer_item_status(item, evidence) == "completed":
                    item["status"] = "completed"
                    item["status_label"] = self._plan_status_label("completed")

        return sorted(reconciled, key=lambda item: item["order"])

    def _infer_item_status(self, item: Dict[str, Any], evidence: PlanEvidence) -> Optional[str]:
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        doc = evidence.document_text.lower()

        if self._has_any(text, ["图", "图片", "曲线", "收敛过程图", "figure", "chart", "plot"]):
            return "completed" if evidence.has_output_image else None

        if self._has_any(text, ["结构", "大纲", "目录", "outline"]):
            return "completed" if evidence.has_document and evidence.document_has_headings else None

        section_rules = [
            (["abstract", "摘要"], ["摘要", "abstract"]),
            (["introduction", "引言", "背景"], ["引言", "introduction"]),
            (["method", "方法", "原理", "算法"], ["方法", "原理", "algorithm", "method", "蒙特卡洛方法原理"]),
            (["圆周率", "π", "pi", "估计"], ["圆周率", "π", "pi", "估计"]),
            (["收敛", "convergence"], ["收敛", "convergence"]),
            (["实验", "结果", "讨论", "results", "discussion"], ["实验", "结果", "讨论", "results", "discussion"]),
            (["结论", "conclusion"], ["结论", "conclusion"]),
            (["参考文献", "references"], ["参考文献", "references"]),
        ]
        for item_keywords, doc_keywords in section_rules:
            if self._has_any(text, item_keywords):
                if self._has_any(doc, doc_keywords):
                    return "completed"
                return None

        normalized_title = self._normalize_text(item.get("title", ""))
        normalized_doc = self._normalize_text(evidence.document_text)
        if len(normalized_title) >= 4 and normalized_title in normalized_doc:
            return "completed"

        return None

    def _assemble_plan(
        self,
        title: str,
        items: List[Dict[str, Any]],
        source_markdown: str,
        source: str,
        evidence: PlanEvidence,
        revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        stats = {
            "total": len(items),
            "completed": sum(1 for item in items if item["status"] == "completed"),
            "in_progress": sum(1 for item in items if item["status"] == "in_progress"),
            "blocked": sum(1 for item in items if item["status"] == "blocked"),
            "pending": sum(1 for item in items if item["status"] == "pending"),
        }
        stats["progress_percent"] = round((stats["completed"] / stats["total"]) * 100) if stats["total"] else 0

        current_focus = next((item for item in items if item["status"] == "in_progress"), None)
        if current_focus is None:
            current_focus = next((item for item in items if item["status"] == "pending"), None)
        next_actions = [
            {
                "id": item["id"],
                "title": item["title"],
                "reason": "等待执行" if item["status"] == "pending" else "需要解除阻塞",
            }
            for item in items
            if item["status"] in {"pending", "blocked"}
        ][:3]

        previous_revision = 0
        plan_json_path = self.workspace_path / "plan.json"
        if plan_json_path.exists():
            try:
                previous_revision = int(json.loads(plan_json_path.read_text(encoding="utf-8")).get("revision", 0))
            except Exception:
                previous_revision = 0

        if revision is None:
            revision = previous_revision + 1

        return {
            "version": 1,
            "revision": revision,
            "title": title,
            "methodology": "spec-driven",
            "planning_mode": "dynamic",
            "phases": PLAN_PHASES,
            "items": items,
            "stats": stats,
            "current_focus": current_focus,
            "next_actions": next_actions,
            "source": source,
            "source_markdown": source_markdown,
            "constraints": {
                "template_contract": self._read_template_contract(),
                "plan_markdown_synced": bool(self._read_template_contract()),
            },
            "evidence": evidence.to_summary(),
            "updated_at": datetime.now().isoformat(),
        }

    def _read_template_contract(self) -> str:
        path = self.workspace_path / CONTRACT_PATH
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _strip_template_constraints(self, plan_content: str) -> str:
        pattern = re.compile(
            rf"\n?\s*{re.escape(CONSTRAINTS_START)}.*?{re.escape(CONSTRAINTS_END)}\s*",
            re.DOTALL,
        )
        return pattern.sub("\n", plan_content).strip() + "\n"

    def _format_template_constraints(self, template_contract: str) -> str:
        return (
            f"{CONSTRAINTS_START}\n"
            "## 模板强制约束\n\n"
            "以下内容由用户上传的模板骨架自动生成，模型写作、续写和验收时必须遵循。\n\n"
            f"{template_contract.strip()}\n"
            f"{CONSTRAINTS_END}"
        )

    def _read_existing_plan(self) -> Optional[Dict[str, Any]]:
        plan_json_path = self.workspace_path / "plan.json"
        if not plan_json_path.exists():
            return None
        try:
            return json.loads(plan_json_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _stable_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        stable = deepcopy(plan)
        stable.pop("revision", None)
        stable.pop("updated_at", None)
        return stable

    def _collect_evidence(self) -> PlanEvidence:
        document_text_parts: List[str] = []
        document_paths: List[str] = []
        docx_image_count = 0

        paper_md = self.workspace_path / "paper.md"
        if paper_md.exists():
            text = paper_md.read_text(encoding="utf-8", errors="ignore")
            document_text_parts.append(text)
            document_paths.append("paper.md")

        paper_docx = self.workspace_path / "paper.docx"
        if paper_docx.exists():
            text, image_count = self._read_docx(paper_docx)
            document_text_parts.append(text)
            document_paths.append("paper.docx")
            docx_image_count += image_count

        document_text = "\n".join(part for part in document_text_parts if part)
        output_image_roots = ["outputs", "artifacts", "runs"]
        output_images = [
            str(path.relative_to(self.workspace_path))
            for root in output_image_roots
            for path in (self.workspace_path / root).rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
        ] if self.workspace_path.exists() else []

        generated_files = [
            str(path.relative_to(self.workspace_path))
            for path in self.workspace_path.rglob("*")
            if path.is_file()
            and not any(part in {".system", "__pycache__"} for part in path.relative_to(self.workspace_path).parts)
        ][:80] if self.workspace_path.exists() else []

        has_markdown_heading = bool(re.search(r"(?m)^#{1,6}\s+\S+", document_text))
        has_numbered_heading = bool(re.search(r"(?m)^\s*\d+(?:\.\d+)*[\.、]\s*\S+", document_text))
        return PlanEvidence(
            document_text=document_text,
            document_paths=document_paths,
            document_char_count=len(document_text.strip()),
            document_has_headings=has_markdown_heading or has_numbered_heading,
            docx_image_count=docx_image_count,
            output_images=sorted(output_images),
            generated_files=sorted(generated_files),
        )

    def _read_docx(self, docx_path: Path) -> tuple[str, int]:
        try:
            from docx import Document

            document = Document(str(docx_path))
            paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
            image_count = len(document.part._rels)
            image_count = sum(1 for rel in document.part._rels.values() if "image" in rel.reltype)
            return "\n".join(paragraphs), image_count
        except Exception:
            return self._read_docx_zip_fallback(docx_path)

    def _read_docx_zip_fallback(self, docx_path: Path) -> tuple[str, int]:
        try:
            with zipfile.ZipFile(docx_path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
                text = re.sub(r"<[^>]+>", "\n", xml)
                text = re.sub(r"\n+", "\n", text)
                media = [name for name in archive.namelist() if name.startswith("word/media/")]
                return text, len(media)
        except Exception:
            return "", 0

    def _normalize_plan_status(self, raw_status: str) -> str:
        text = (raw_status or "").strip().lower()
        if any(token in text for token in ["✅", "完成", "done", "completed", "complete"]):
            return "completed"
        if any(token in text for token in ["⏳", "进行", "progress", "doing", "current"]):
            return "in_progress"
        if any(token in text for token in ["❌", "失败", "blocked", "阻塞", "error", "failed"]):
            return "blocked"
        return "pending"

    def _plan_status_label(self, status: str) -> str:
        return {
            "completed": "已完成",
            "in_progress": "进行中",
            "blocked": "阻塞",
            "pending": "待写",
        }.get(status, "待写")

    def _split_markdown_table_row(self, line: str) -> List[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [cell.strip() for cell in line.split("|")]

    def _has_any(self, text: str, keywords: List[str]) -> bool:
        return any(keyword.lower() in text for keyword in keywords)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text).lower()
