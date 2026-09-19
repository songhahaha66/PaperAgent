from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import nsmap, qn
from docx.shared import Inches, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph as DocxParagraph

from ..schemas.paper_ir import (
    Citation,
    Code,
    Equation,
    FigureRef,
    ListBlock,
    PaperIR,
    Paragraph,
    TableRows,
)
from ..schemas.template_spec import TemplateSpec

VOLATILE_PARTS = {"docProps/core.xml", "docProps/app.xml"}
RSID_LOCAL_NAMES = {"rsidR", "rsidRDefault", "rsidP", "rsidRPr", "rsidTr", "rsidSect", "docId"}
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
try:
    if "m" not in nsmap:
        nsmap["m"] = MATH_NS
except Exception:
    pass


def render_docx(
    spec: TemplateSpec,
    ir: PaperIR,
    template_path: Path | str | None,
    output_path: Path | str,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if template_path and Path(template_path).exists():
        shutil.copyfile(template_path, output_path)
        document = Document(str(output_path))
        _fill_from_template(document, spec, ir, Path(template_path).parent)
    else:
        document = Document()
        _fill_blank_document(document, spec, ir, output_path.parent)
    _append_references(document, ir)
    document.save(str(output_path))
    return output_path


def render_docx_from_ir(spec: TemplateSpec, ir: PaperIR, output_path: Path | str) -> Path:
    return render_docx(spec, ir, None, output_path)


def normalize_docx_bytes(path: Path | str) -> list[tuple[str, bytes]]:
    """Byte snapshot excluding volatile core properties and revision IDs."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if name not in VOLATILE_PARTS)
        return [(name, _strip_volatile_xml(name, archive.read(name))) for name in names]


def _fill_from_template(document: Document, spec: TemplateSpec, ir: PaperIR, workspace: Path) -> None:
    block_text = {block.id: block.text.strip() for block in spec.blocks if block.text.strip()}
    operations: list[tuple] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        matching = [slot for slot in spec.slots if block_text.get(slot.anchor_block, "") == text]
        if not matching:
            continue
        slot = matching[0]
        if slot.role in {"instruction_delete", "example_delete"}:
            operations.append(("remove", paragraph._element))
            continue
        section = ir.sections.get(slot.id)
        if slot.role in {"placeholder_fill", "caption", "figure_slot"} and section:
            operations.append(("insert", paragraph._element, section.blocks, workspace, ir))

    _fill_tables(document, spec, ir)

    for item in reversed(operations):
        if item[0] == "remove":
            parent = item[1].getparent()
            if parent is not None:
                parent.remove(item[1])
        elif item[0] == "insert":
            _, element, blocks, workspace, paper = item
            anchor = DocxParagraph(element, document._body)
            for block in reversed(blocks):
                _insert_block_after(anchor, block, paper, workspace)


def _fill_blank_document(document: Document, spec: TemplateSpec, ir: PaperIR, workspace: Path) -> None:
    for slot in spec.slots:
        if slot.role in {"instruction_delete", "example_delete"}:
            continue
        if slot.role == "heading":
            title = slot.title or (slot.section_path[-1] if slot.section_path else slot.id)
            document.add_heading(title, level=min(len(slot.section_path) or 1, 3))
            continue
        if slot.role == "fixed_text" and slot.title:
            document.add_paragraph(slot.title)
            continue
        section = ir.sections.get(slot.id)
        if not section:
            continue
        for block in section.blocks:
            _append_block(document, block, ir, workspace)


def _append_references(document: Document, ir: PaperIR) -> None:
    if not ir.references:
        return
    existing = "\n".join(paragraph.text for paragraph in document.paragraphs)
    if "参考文献" in existing:
        return
    document.add_heading("参考文献", level=1)
    for index, reference in enumerate(ir.references.values(), start=1):
        document.add_paragraph(f"[{index}] {reference.text}")


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


def _insert_block_after(anchor: DocxParagraph, block, ir: PaperIR, workspace: Path) -> DocxParagraph:
    added = _insert_after(anchor, "")
    _write_block_into(added, block, ir, workspace)
    return added


def _append_block(document: Document, block, ir: PaperIR, workspace: Path) -> None:
    paragraph = document.add_paragraph()
    _write_block_into(paragraph, block, ir, workspace)


def _write_block_into(paragraph: DocxParagraph, block, ir: PaperIR, workspace: Path) -> None:
    if isinstance(block, Paragraph):
        paragraph.text = block.text
    elif isinstance(block, Code):
        run = paragraph.add_run(block.text)
        run.font.name = "Consolas"
        run.font.size = Pt(10)
    elif isinstance(block, ListBlock):
        paragraph.text = ("1. " if block.ordered else "• ") + "；".join(block.items)
    elif isinstance(block, FigureRef):
        _write_figure(paragraph, block, ir, workspace)
    elif isinstance(block, Equation):
        _write_equation(paragraph, block.latex)
    elif isinstance(block, Citation):
        reference = ir.references.get(block.ref_id)
        paragraph.text = reference.text if reference else f"[{block.ref_id}]"
    elif isinstance(block, TableRows):
        paragraph.text = "\n".join("\t".join(row) for row in block.rows)


def _write_figure(paragraph: DocxParagraph, block: FigureRef, ir: PaperIR, workspace: Path) -> None:
    artifact = ir.artifacts.get(block.artifact_id)
    path = Path(artifact.path) if artifact else None
    if path and not path.is_absolute():
        path = workspace / path
        if not path.exists():
            path = workspace.parent / artifact.path if artifact else path
    if path and path.exists():
        run = paragraph.add_run()
        try:
            run.add_picture(str(path), width=Inches(4.8))
        except Exception:
            paragraph.text = block.caption or block.artifact_id
            return
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if block.caption:
            caption = _insert_after(paragraph, block.caption)
            caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        paragraph.text = block.caption or f"[图:{block.artifact_id}]"


def _write_equation(paragraph: DocxParagraph, latex: str) -> None:
    omath = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = latex
    run.append(text)
    omath.append(run)
    paragraph._element.append(omath)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _insert_after(paragraph: DocxParagraph, text: str) -> DocxParagraph:
    new_p = paragraph._element.makeelement(qn("w:p"), {})
    paragraph._element.addnext(new_p)
    added = DocxParagraph(new_p, paragraph._parent)
    if paragraph.style is not None:
        added.style = paragraph.style
    if text:
        added.add_run(text)
    return added


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
