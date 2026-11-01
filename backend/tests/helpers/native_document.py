"""Small native TeX document builder for compiler fixtures."""
import re
from ai_system.export.latex import escape_plain
from pathlib import Path
TEMPLATE_PATH = Path(__file__).with_name("template.tex")

def native_document(title: str, abstract: str, body: str) -> str:
    """Join native section fragments into the built-in ctex document."""
    values = {"TITLE": escape_plain(title), "ABSTRACT": abstract.strip(), "BODY": body.strip()}
    return re.sub(r"<<(TITLE|ABSTRACT|BODY)>>", lambda m: values[m.group(1)], TEMPLATE_PATH.read_text(encoding="utf-8"))

