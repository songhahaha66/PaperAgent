from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from ai_system.core_tools.docx_tools import DocxTools
from ai_system.eval.golden import build_golden_cases
from ai_system.eval.harness import run_eval_case, summarize
from ai_system.graph.nodes.render_node import render_node
from ai_system.graph.nodes.spec import synthetic_markdown_spec
from ai_system.graph.state import PaperState
from ai_system.render.docx_renderer import render_docx_from_ir
from ai_system.schemas.paper_ir import (
    Artifact,
    Citation,
    Equation,
    FigureRef,
    PaperIR,
    Paragraph,
    Reference,
    SectionContent,
)
from ai_system.schemas.template_spec import Slot, TemplateSpec
from ai_system.template.ooxml_parser import parse_docx
from ai_system.validate.content import content_issues
from ai_system.validate.visual import visual_issues


def _tiny_png(path: Path) -> Path:
    Image.new("RGB", (12, 12), (30, 90, 180)).save(path)
    return path


def test_blank_ir_renders_headings_equation_citation_and_figure(tmp_path: Path):
    figure = _tiny_png(tmp_path / "plot.png")
    spec = TemplateSpec(
        template_id=0,
        version=1,
        slots=[
            Slot(id="slot.h", role="heading", anchor_block="P000", title="方法", section_path=["方法"]),
            Slot(id="slot.body", role="placeholder_fill", anchor_block="P001", title="方法"),
            Slot(id="slot.fig", role="figure_slot", anchor_block="G001", title="图1"),
        ],
    )
    ir = PaperIR(
        work_id="blank-1",
        revision=1,
        sections={
            "slot.body": SectionContent(
                slot_id="slot.body",
                blocks=[
                    Paragraph(text="给出方法说明与公式推导。"),
                    Equation(latex="E=mc^2"),
                    Citation(ref_id="r1"),
                ],
            ),
            "slot.fig": SectionContent(
                slot_id="slot.fig",
                blocks=[FigureRef(artifact_id="fig1", caption="图1 方法示意")],
            ),
        },
        artifacts={"fig1": Artifact(id="fig1", path=str(figure), mime="image/png")},
        references={"r1": Reference(id="r1", text="Smith, J. Example Paper. 2024.")},
    )
    output = tmp_path / "paper.docx"
    render_docx_from_ir(spec, ir, output)
    parsed = parse_docx(output)
    assert "方法" in parsed.text
    assert "给出方法说明" in parsed.text
    assert "Smith, J. Example Paper. 2024." in parsed.text
    assert "参考文献" in parsed.text
    xml = zipfile.ZipFile(output).read("word/document.xml").decode("utf-8")
    assert "oMath" in xml
    assert "E=mc^2" in xml
    assert parsed.media_count >= 1
    issues = visual_issues(output, spec, ir)
    assert all(issue.code != "figure_missing" for issue in issues)
    assert all(issue.code != "figure_not_embedded" for issue in issues)


def test_render_node_writes_word_without_template(tmp_path: Path):
    spec = synthetic_markdown_spec(0, ["摘要", "引言"])
    ir = PaperIR(
        work_id="nt-1",
        sections={
            "slot.0.body.p001": SectionContent(
                slot_id="slot.0.body.p001",
                blocks=[Paragraph(text="摘要正文覆盖最小字数并说明研究问题与贡献。")],
            )
        },
    )
    state = PaperState(
        work_id="nt-1",
        workspace_dir=str(tmp_path),
        output_mode="word",
        spec=spec,
        ir=ir,
    )
    state = render_node(state)
    assert Path(state.rendered_path).exists()
    assert "摘要" in parse_docx(state.rendered_path).text


def test_citation_unresolved_is_reported():
    spec = TemplateSpec(
        template_id=1,
        slots=[Slot(id="slot.body", role="placeholder_fill", anchor_block="P001", title="正文")],
    )
    ir = PaperIR(
        work_id="cite-1",
        sections={
            "slot.body": SectionContent(
                slot_id="slot.body",
                blocks=[Citation(ref_id="missing")],
            )
        },
    )
    issues = content_issues(spec, ir, Path("paper.md"))
    assert any(issue.code == "citation_unresolved" for issue in issues)


def test_docx_tools_render_from_ir(tmp_path: Path):
    spec = TemplateSpec(
        template_id=0,
        slots=[
            Slot(id="slot.h", role="heading", anchor_block="P000", title="结论", section_path=["结论"]),
            Slot(id="slot.body", role="placeholder_fill", anchor_block="P001", title="结论"),
        ],
    )
    ir = PaperIR(
        work_id="tools-1",
        sections={
            "slot.body": SectionContent(
                slot_id="slot.body",
                blocks=[Paragraph(text="结论部分总结实验发现。")],
            )
        },
    )
    tools = DocxTools(str(tmp_path))
    result = tools.render_from_ir(spec, ir)
    assert "IR" in result
    assert (tmp_path / "paper.docx").exists()


def test_golden_set_word_cases_validate_clean(tmp_path: Path):
    results = [run_eval_case(case, tmp_path) for case in build_golden_cases(tmp_path / "golden")]
    summary = summarize(results)
    assert summary["cases"] == 6
    assert summary["clean"] == 6
    assert summary["leftover_examples"] == 0
    assert summary["skeleton_ok_rate"] == 1.0
