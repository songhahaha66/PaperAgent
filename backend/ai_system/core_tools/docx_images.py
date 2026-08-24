"""
Word image inventory, extraction, and insertion helpers.

These functions stay independent of the LLM so template initialization and
WriterAgent tools can share the same layout-aware image map.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

NSMAP = {"w": W_NS, "a": A_NS, "r": R_NS}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
PART_RELS = (
    ("document", "word/document.xml", "word/_rels/document.xml.rels"),
    ("header", "word/header1.xml", "word/_rels/header1.xml.rels"),
    ("header", "word/header2.xml", "word/_rels/header2.xml.rels"),
    ("footer", "word/footer1.xml", "word/_rels/footer1.xml.rels"),
    ("footer", "word/footer2.xml", "word/_rels/footer2.xml.rels"),
)


@dataclass
class DocxImageInfo:
    index: int
    r_id: str
    part: str
    filename: str
    zip_path: str
    size_bytes: int
    width: Optional[int]
    height: Optional[int]
    nearby_text: str
    extracted_path: str = ""

    def summary_line(self) -> str:
        size = f"{self.size_bytes / 1024:.1f}KB"
        dims = f"{self.width}x{self.height}" if self.width and self.height else "尺寸未知"
        nearby = self.nearby_text or "（附近无文字）"
        return (
            f"- [{self.index}] {self.filename} ({dims}, {size}, {self.part}) "
            f"附近文字: {nearby}"
        )


def inventory_docx_images(docx_path: Path) -> List[DocxImageInfo]:
    docx_path = Path(docx_path)
    if not docx_path.exists():
        return []

    images: List[DocxImageInfo] = []
    try:
        with zipfile.ZipFile(docx_path) as archive:
            names = set(archive.namelist())
            seen_zip_paths = set()
            for part_name, xml_path, rels_path in PART_RELS:
                if xml_path not in names or rels_path not in names:
                    continue
                rels = _read_image_rels(archive.read(rels_path))
                nearby_by_rid = _nearby_text_by_rid(archive.read(xml_path))
                for r_id, target in rels.items():
                    zip_path = _resolve_zip_target(xml_path, target)
                    if zip_path in seen_zip_paths or zip_path not in names:
                        continue
                    seen_zip_paths.add(zip_path)
                    payload = archive.read(zip_path)
                    width, height = _image_size(payload)
                    images.append(
                        DocxImageInfo(
                            index=len(images) + 1,
                            r_id=r_id,
                            part=part_name,
                            filename=Path(zip_path).name,
                            zip_path=zip_path,
                            size_bytes=len(payload),
                            width=width,
                            height=height,
                            nearby_text=nearby_by_rid.get(r_id, ""),
                        )
                    )

            # Fallback: media files that were not referenced by the scanned parts
            for name in sorted(n for n in names if n.startswith("word/media/")):
                if name in seen_zip_paths:
                    continue
                payload = archive.read(name)
                width, height = _image_size(payload)
                images.append(
                    DocxImageInfo(
                        index=len(images) + 1,
                        r_id="",
                        part="media",
                        filename=Path(name).name,
                        zip_path=name,
                        size_bytes=len(payload),
                        width=width,
                        height=height,
                        nearby_text="",
                    )
                )
    except Exception as exc:
        logger.warning("读取 Word 图片清单失败: %s", exc, exc_info=True)
        return []

    return images


def extract_docx_images(docx_path: Path, output_dir: Path) -> List[DocxImageInfo]:
    docx_path = Path(docx_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    images = inventory_docx_images(docx_path)
    if not images:
        return []

    with zipfile.ZipFile(docx_path) as archive:
        for info in images:
            try:
                payload = archive.read(info.zip_path)
            except KeyError:
                continue
            dest = output_dir / info.filename
            if dest.exists():
                dest = output_dir / f"{info.index}_{info.filename}"
            dest.write_bytes(payload)
            info.extracted_path = str(dest)
    return images


def format_image_inventory(images: Iterable[DocxImageInfo], title: str = "图片骨架") -> str:
    images = list(images)
    if not images:
        return f"## {title}\n- 模板未嵌入图片。\n"
    lines = [f"## {title}", f"- 共 {len(images)} 张嵌入图片，排版时必须保留位置或按图意替换。"]
    lines.extend(info.summary_line() for info in images)
    return "\n".join(lines) + "\n"


def _read_image_rels(rels_xml: bytes) -> dict[str, str]:
    root = ET.fromstring(rels_xml)
    mapping = {}
    for rel in root.findall(f".//{{{REL_NS}}}Relationship"):
        rel_type = rel.attrib.get("Type", "")
        if "image" not in rel_type.lower():
            continue
        r_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if r_id and target:
            mapping[r_id] = target
    return mapping


def _nearby_text_by_rid(document_xml: bytes) -> dict[str, str]:
    root = ET.fromstring(document_xml)
    nearby = {}
    paragraphs = root.findall(".//w:p", NSMAP)
    texts = [_paragraph_text(para) for para in paragraphs]
    for idx, para in enumerate(paragraphs):
        for blip in para.findall(".//a:blip", NSMAP):
            r_id = blip.attrib.get(f"{{{R_NS}}}embed", "")
            if not r_id:
                continue
            around = []
            if texts[idx]:
                around.append(texts[idx])
            for neighbor in (idx - 1, idx + 1):
                if 0 <= neighbor < len(texts) and texts[neighbor]:
                    around.append(texts[neighbor])
            nearby[r_id] = " / ".join(_truncate(item, 40) for item in around[:3])
    return nearby


def _paragraph_text(para: ET.Element) -> str:
    return "".join(node.text or "" for node in para.findall(".//w:t", NSMAP)).strip()


def _resolve_zip_target(xml_path: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = Path(xml_path).parent
    return str((base / target).as_posix()).replace("/./", "/")


def _image_size(payload: bytes) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as img:
            return img.size
    except Exception:
        return None, None


def _truncate(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
