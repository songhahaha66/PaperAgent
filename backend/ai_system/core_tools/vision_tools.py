"""
Vision tools for Word typesetting.

The agent can see template images, generated charts, and uploaded figures
instead of treating a complex .docx as text-only.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path
from typing import Optional

import requests

from ..config.api_settings import load_env_api_settings
from .docx_images import extract_docx_images, format_image_inventory

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_VISION_IMAGES = 8
MAX_VISION_EDGE = 1280
DEFAULT_QUESTION = (
    "你是学术论文排版助手。请用中文分析这张图："
    "1) 它更像封面/校徽/页眉装饰、正文插图、表格截图、流程图还是签名章；"
    "2) 图中有哪些关键文字、坐标轴或图例；"
    "3) 排版时应如何处理（原样保留、替换为新图、居中并配图题、还是只作页眉装饰）。"
    "回答控制在 120 字以内。"
)


class VisionTools:
    def __init__(self, workspace_dir: str, llm=None, stream_manager=None):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.llm = llm
        self.stream_manager = stream_manager

    def _resolve(self, file_path: str) -> Path:
        path = Path(file_path)
        if not path.is_absolute():
            path = self.workspace_dir / file_path
        resolved = path.resolve()
        if not str(resolved).startswith(str(self.workspace_dir)):
            raise ValueError(f"路径超出工作空间: {file_path}")
        return resolved

    async def analyze_image(self, image_path: str, question: str = "") -> str:
        """识别工作区中的一张图片，用于理解模板图或生成图该如何排版。"""
        try:
            path = self._resolve(image_path)
        except ValueError as exc:
            return f"Error: {exc}"
        if not path.exists() or not path.is_file():
            return f"Error: 图片不存在: {image_path}"
        if path.suffix.lower() not in IMAGE_EXTS:
            return f"Error: 不支持的图片类型: {path.suffix}"

        meta = self._image_meta(path)
        prompt = question.strip() or DEFAULT_QUESTION
        analysis = await self._describe_image(path, prompt)
        return (
            f"图片: {path.relative_to(self.workspace_dir)}\n"
            f"{meta}\n"
            f"识别结果: {analysis}"
        )

    async def analyze_docx_layout(self, filename: str = "paper.docx", question: str = "") -> str:
        """
        提取 Word 中的嵌入图片并识别它们在排版中的角色。

        复杂模板里的校徽、封面图、页眉、实验截图只靠纯文本是看不见的。
        """
        file_path = self.workspace_dir / filename
        if not file_path.exists():
            return f"Error: 文件不存在: {filename}"

        output_dir = self.workspace_dir / ".system" / "docx_images" / Path(filename).stem
        images = extract_docx_images(file_path, output_dir)
        if not images:
            return (
                f"{filename} 中没有嵌入图片。\n"
                "如果用户另外上传了图片，请用 analyze_image 识别 outputs/ 或 attachment/ 中的文件。"
            )

        lines = [
            f"✅ 已从 {filename} 提取 {len(images)} 张图片到 {output_dir.relative_to(self.workspace_dir)}",
            format_image_inventory(images, title="模板图片清单").rstrip(),
            "",
            "## 视觉识别",
        ]

        prompt = question.strip() or DEFAULT_QUESTION
        for info in images[:MAX_VISION_IMAGES]:
            extracted = Path(info.extracted_path) if info.extracted_path else None
            if not extracted or not extracted.exists():
                lines.append(f"- [{info.index}] {info.filename}: 提取失败")
                continue
            analysis = await self._describe_image(extracted, prompt)
            rel = extracted.relative_to(self.workspace_dir)
            lines.append(
                f"- [{info.index}] {rel} | {info.part} | 附近: {info.nearby_text or '无'}\n"
                f"  {analysis}"
            )

        if len(images) > MAX_VISION_IMAGES:
            lines.append(f"- 其余 {len(images) - MAX_VISION_IMAGES} 张图已提取，但未全部送识别以控制费用。")

        return "\n".join(lines)

    async def _describe_image(self, path: Path, question: str) -> str:
        payload, mime = self._prepare_image(path)
        if self.llm is not None:
            try:
                return await self._describe_with_llm(payload, mime, question)
            except Exception as exc:
                logger.warning("LLM 视觉调用失败，回退到环境 API: %s", exc)

        settings = load_env_api_settings()
        if not settings:
            return "未配置视觉模型，仅提供文件元数据。请设置 PAPERAGENT_API_KEY/BASE 或在界面配置支持识图的模型。"

        try:
            return await asyncio.to_thread(
                self._describe_with_http, payload, mime, question, settings
            )
        except Exception as exc:
            logger.warning("环境 API 视觉调用失败: %s", exc)
            return f"视觉识别失败: {exc}"

    async def _describe_with_llm(self, payload: bytes, mime: str, question: str) -> str:
        from langchain_core.messages import HumanMessage

        message = HumanMessage(
            content=[
                {"type": "text", "text": question},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{base64.b64encode(payload).decode()}"},
                },
            ]
        )
        response = await self.llm.ainvoke([message])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif isinstance(item, str):
                    texts.append(item)
            content = "".join(texts)
        text = str(content or "").strip()
        if not text:
            raise ValueError("视觉模型返回空内容")
        return text

    @staticmethod
    def _describe_with_http(payload: bytes, mime: str, question: str, settings) -> str:
        body = {
            "model": settings.model_id,
            "max_tokens": 400,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{base64.b64encode(payload).decode()}"
                            },
                        },
                    ],
                }
            ],
        }
        response = requests.post(
            f"{settings.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=90,
        )
        if response.status_code >= 400:
            detail = response.text[:300]
            raise ValueError(f"视觉接口 HTTP {response.status_code}: {detail}")
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        text = str(content or "").strip()
        if not text:
            raise ValueError(f"视觉接口返回空内容: {data}")
        return text

    @staticmethod
    def _prepare_image(path: Path) -> tuple[bytes, str]:
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "image/png")
        raw = path.read_bytes()
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as img:
                img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
                width, height = img.size
                longest = max(width, height)
                if longest > MAX_VISION_EDGE:
                    scale = MAX_VISION_EDGE / longest
                    img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))))
                buffer = io.BytesIO()
                fmt = "PNG" if mime == "image/png" else "JPEG"
                img.save(buffer, format=fmt)
                return buffer.getvalue(), "image/png" if fmt == "PNG" else "image/jpeg"
        except Exception:
            return raw, mime

    @staticmethod
    def _image_meta(path: Path) -> str:
        size_kb = path.stat().st_size / 1024
        try:
            from PIL import Image

            with Image.open(path) as img:
                return f"尺寸: {img.size[0]}x{img.size[1]}, 大小: {size_kb:.1f}KB"
        except Exception:
            return f"大小: {size_kb:.1f}KB"
