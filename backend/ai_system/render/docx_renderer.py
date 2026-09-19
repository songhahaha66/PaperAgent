from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph as DocxParagraph

from ..schemas.paper_ir import Code, ListBlock, PaperIR, Paragraph, TableRows
from ..schemas.template_spec import TemplateSpec

VOLATILE_PARTS = {"docProps/core.xml", "docProps/app.xml"}
RSID_LOCAL_NAMES = {"rsidR", "rsidRDefault", "rsidP", "rsidRPr", "rsidTr", "rsidSect", "docId"}


def render_docx(
    spec: TemplateSpec,
    ir: PaperIR,
    template_path: Path | str,
    output_path: Path | str,
) -> Path:
    template_path = Path(template_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, output_path)
    document = Document(str(output_path))

    block_text = {block.id: block.text.strip() for block in spec.blocks if block.text.strip()}
    operations: list[tuple[str, object, list[str] | None]] = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        matching = [
            slot
            for slot in spec.slots
            if block_text.get(slot.anchor_block, "") == text
        ]
        if not matching:
            continue
        slot = matching[0]
        if slot.role in {"instruction_delete", "example_delete"}:
            operations.append(("remove", paragraph._element, None))
            continue
        section = ir.sections.get(slot.id)
        if slot.role in {"placeholder_fill", "caption", "figure_slot"} and section:
            operations.append(("insert", paragraph._element, _blocks_to_texts(section.blocks)))

    _fill_tables(document, spec, ir)

    for action, element, texts in reversed(operations):
        if action == "remove":
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
        elif action == "insert" and texts:
            anchor = DocxParagraph(element, document._body)
            for text in reversed(texts):
                _insert_after(anchor, text)

    document.save(str(output_path))
    return output_path


def normalize_docx_bytes(path: Path | str) -> list[tuple[str, bytes]]:
    """Byte snapshot excluding volatile core properties and revision IDs."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if name not in VOLATILE_PARTS)
        return [(name, _strip_volatile_xml(name, archive.read(name))) for name in names]


def _strip_volatile_xml(name: str, data: bytes) -> bytes:
    if not name.endswith(".xml"):
        return data
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    for node in list(root.iter()):
        for attr in list(node.attrib):
            local = attr.rsplit("}", 1)[-1]
            if local in RSID_LOCAL_NAMES or local.lower().startswith("rsid"):
                del node.attrib[attr]
    _remove_rsids(root)
    return ET.tostring(root, encoding="utf-8")


def _remove_rsids(root: ET.Element) -> None:
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] == "rsids":
                parent.remove(child)


def _fill_tables(document: Document, spec: TemplateSpec, ir: PaperIR) -> None:
    table_slots = [slot for slot in spec.slots if slot.role == "table"]
    for table, slot in zip(document.tables, table_slots):
        section = ir.sections.get(slot.id)
        if not section:
            continue
        table_block = next((block for block in section.blocks if isinstance(block, TableRows)), None)
        if table_block is None:
            continue
        _write_table(table, table_block)


def _write_table(table: Table, table_block: TableRows) -> None:
    for row_index, row_values in enumerate(table_block.rows):
        if row_index >= len(table.rows):
            break
        for col_index, value in enumerate(row_values):
            if col_index >= len(table.rows[row_index].cells):
                break
            table.rows[row_index].cells[col_index].text = value


def _blocks_to_texts(blocks) -> list[str]:
    texts: list[str] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            texts.append(block.text)
        elif isinstance(block, Code):
            texts.append(block.text)
        elif isinstance(block, ListBlock):
            texts.extend(block.items)
        elif isinstance(block, TableRows):
            continue
        else:
            caption = getattr(block, "caption", None) or getattr(block, "latex", None) or ""
            if caption:
                texts.append(str(caption))
    return [text for text in texts if text]


def _insert_after(paragraph: DocxParagraph, text: str) -> DocxParagraph:
    new_p = paragraph._element.makeelement(qn("w:p"), {})
    paragraph._element.addnext(new_p)
    added = DocxParagraph(new_p, paragraph._parent)
    if paragraph.style is not None:
        added.style = paragraph.style
    added.add_run(text)
    return added
