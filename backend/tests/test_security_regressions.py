"""Security boundaries and adversarial input regressions; entirely offline."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from ai_system.text_syntax import math_blocks, math_spans, strip_math, native_citations, has_markdown
from ai_system.modules.experiments.execution import is_placeholder
from ai_system.research.citations import citation_text
from services.file_services.workspace_layout import relative_parts, safe_join, workspace_path
from services.chat_services.chat_history_manager import ChatHistoryManager
from services.chat_services.paper_editor import prepare_publication, guard_wording, PUBLICATION_MARKER
from services.file_services.template_contract import get_template_analysis_dir


@pytest.mark.parametrize('code', ['', 'pass', '"docstring"', "print('ok')", "import numpy as np\nprint('q3')", "from math import pi\nprint('done!'); pass"])
def test_placeholder_statements(code):
    assert is_placeholder(code)


@pytest.mark.parametrize('code', ['import math', 'print(value)', 'print(1 + 1)', 'print(f"{x}")', 'print("a", file=stream)', 'print("a", "b")', 'x = 1\nprint("ok")', 'def f(): pass', 'not valid python', 'print("This is a useful long message")'])
def test_real_or_invalid_code_is_not_discarded(code):
    assert not is_placeholder(code)


def test_math_boundaries_and_wording_guard():
    blocks = ['$x+1$', '$$a\nb$$', r'\(x\)', r'\[y\]', r'\begin{align*}a&=b\end{align*}']
    text = 'lead ' + ' between '.join(blocks) + ' tail'
    assert math_blocks(text) == blocks
    assert [text[a:b] for a, b in math_spans(text)] == blocks
    assert strip_math(text) == 'lead ' + ' between ' * 4 + ' tail'
    assert math_blocks('$$x$') == ['$x$']
    assert math_blocks('$\n$') == []
    assert math_blocks('$$$$') == ['$$$$']
    guard_wording('old ' + text, 'new ' + text, False)
    with pytest.raises(ValueError, match='公式'):
        guard_wording(text, text.replace(r'\(x\)', r'\(y\)'), False)


def test_native_citation_options_and_unbound_keys():
    text = r'A \citep*[see][p. 2]{C12}; B \cite{smith,C3}; C \citet{C4}'
    assert [key for _, _, key in native_citations(text)] == ['C12', 'smith,C3', 'C4']
    assert citation_text(text) == r'A {{C:C12}}; B \cite{smith,C3}; C {{C:C4}}'
    assert citation_text(r'\cite[unclosed \cite{C2}') == r'\cite[unclosed {{C:C2}}'


@pytest.mark.parametrize('text', ['# heading', '  # ', '```python\nx\n```', '| a | b |  ', '![caption](figure.png)'])
def test_markdown_fragments_are_detected(text):
    assert has_markdown(text)


def test_native_fragment_is_not_markdown():
    assert not has_markdown(r'\section{Title} $x|y$ ![unclosed] text (not an image)')


@pytest.mark.timeout(5)
def test_unclosed_delimiters_and_imports_finish_promptly():
    # Each payload formerly caused exponential or quadratic work on the host.
    assert not is_placeholder('  \nimport ' * 30 + '!')
    assert math_blocks(r'\(' * 50000) == []
    assert math_blocks(r'\begin{align}' * 10000) == []
    citations = r'\cite[' * 16000 + '!'
    assert list(native_citations(citations)) == []
    assert citation_text(citations) == citations
    assert not has_markdown('![' * 50000)


@pytest.mark.parametrize('value', ['', '.', '..', '../x', '/etc/passwd', 'a/../x', 'a//x', 'a/./x', 'a\\x', 'C:/x', 'a\x00x'])
def test_path_components_reject_escape(tmp_path, value):
    with pytest.raises(ValueError):
        relative_parts(value)
    with pytest.raises(ValueError):
        safe_join(tmp_path, value)
    with pytest.raises(ValueError):
        ChatHistoryManager(tmp_path).get_work_history(value)


def test_chat_rejects_symlink_to_another_project(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'alias').symlink_to(tmp_path / 'real', target_is_directory=True)
    with pytest.raises(ValueError, match='Symbolic'):
        ChatHistoryManager(tmp_path).get_work_history('alias')
    assert relative_parts('project-id/data/file.csv') == ['project-id', 'data', 'file.csv']


def test_safe_join_rejects_sibling_prefix_and_preserves_nested_paths(tmp_path):
    root = tmp_path / 'project'
    sibling = tmp_path / 'project-other'
    root.mkdir()
    sibling.mkdir()
    (root / 'escape').symlink_to(sibling, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_join(root, 'escape/file')
    assert safe_join(root, 'new/data.csv') == root / 'new/data.csv'


def test_filesystem_ids_are_validated_before_io(tmp_path):
    with pytest.raises(ValueError):
        get_template_analysis_dir('../other')
    with pytest.raises(ValueError, match='编号'):
        prepare_publication(None, '../other', tmp_path, [])


def test_publication_backup_rejects_symlink_even_with_valid_id(tmp_path):
    store = SimpleNamespace(root=tmp_path / 'project', research_id='research')
    marker = workspace_path(store.root, PUBLICATION_MARKER)
    backups = marker.parent / 'paper-publication'
    backups.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (backups / ('a' * 32)).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare_publication(store, 'a' * 32, tmp_path, [])
    assert not marker.exists() and not list(outside.iterdir())
