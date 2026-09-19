from .ooxml_parser import ParsedDocument, ParsedBlock, docx_outline, parse_docx
from .spec_builder import SPEC_FILENAME, build_template_spec, persist_template_spec

__all__ = [
    "ParsedBlock",
    "ParsedDocument",
    "SPEC_FILENAME",
    "build_template_spec",
    "docx_outline",
    "parse_docx",
    "persist_template_spec",
]
