"""Native section writing keeps the evidence and publication protocol intact."""
import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.export.latex import compile_pdf, fragment_issues, native_document
from ai_system.graph.context import GraphContext
from ai_system.graph.registry import Result
from ai_system.graph.nodes import write as writing
from ai_system.graph.nodes.validate import assemble_latex, untracked_numbers, check_sections
from services.file_services.workspace_structure import WorkspaceStructureManager
from services.chat_services.research_session import research_snapshot
from test_paper_graph import FakeModel
from test_research_p2 import store_for, submit, problem, TASK


def accepted_store(root):
    store = store_for(root)
    submit(store, 'Q1', {'demand': 8})
    submit(store, 'Q3', {'independent': 3})
    submit(store, 'Q2', {'allocation': 8, 'residual': 0})
    return store


def test_latex_workspace_starts_with_tex_not_markdown(tmp_path):
    WorkspaceStructureManager.create_workspace_structure(tmp_path, 'native', output_mode='latex')
    assert (tmp_path / 'paper.tex').exists()
    assert not (tmp_path / 'paper.md').exists()


def test_native_style_requests_no_markdown_and_repairs_format_errors(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), output_mode='latex')
    state = {'analysis': problem().model_dump(), 'task': TASK, 'sections': {}, 'solutions': {}}
    assert '原生 LaTeX' in writing.writing_style(ctx)
    assert '原生 LaTeX tabular' in writing.section_request('symbols', '符号说明', state, ctx)
    assert r'\textbf{关键词：}' in writing.section_request('abstract', '摘要', state, ctx)
    state['sections']['symbols'] = '### 符号说明\n| 符号 | 含义 |\n' + '这是一段需要修正的正文。' * 10
    assert any(i['section'] == 'symbols' and 'Markdown' in i['message'] for i in check_sections(state, ctx))
    state['sections']['symbols'] = r'见图\ref{fig:invented}' + '不应引用未登记的图表。' * 10
    assert any('未定义的 LaTeX 标签' in i['message'] for i in check_sections(state, ctx))
    assert fragment_issues(r'\begin{tabular}{ll} $x$ & 决策 \\ \end{tabular}') == []
    assert fragment_issues(r'\begin{align} x=y \end{equation}')
    assert fragment_issues(r'\textbf{缺少结束括号')
    assert fragment_issues(r'\input{/private/file}')
    assert fragment_issues(r'\begin{figure}{{F:F1}}\caption{重复图题}\end{figure}')


@pytest.mark.parametrize('text', [r'\[z=4.05\]', r'\(4.05\)', r'\begin{align}z&=4.05\end{align}', r'误差为 4.05\%'])
def test_native_math_and_escaped_percent_cannot_hide_untracked_results(text):
    assert untracked_numbers(text, '', latex=True)
    assert untracked_numbers(text, '4.05%', latex=True) == []


def test_native_references_escape_registered_text_and_use_math_values(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    ctx.registry.results = {
        'R1': Result('R1', 'number', 'Q1', '利润', '12.5', 'run_test', unit='元'),
        'R2': Result('R2', 'text', 'Q1', '说明', 'A&B_50%', 'run_test'),
        'F1': Result('F1', 'figure', 'Q1', '图3 需求&预测', '', 'run_test', path='outputs/figures/F_1.png'),
    }
    text = r'利润为 {{R:R1}}；\[x={{R:R1}}\]；{{R:R2}}。见{{F:F1}}。' + '\n\n{{F:F1}}\n\n{{F:F1}}'
    out = ctx.registry.render(text, latex=True)
    assert r'利润为 12.5 元；\[x=12.5\]' in out
    assert r'A\&B\_50\%' in out
    assert r'图~\ref{fig:F1}' in out
    assert out.count(r'\begin{figure}') == 1
    assert r'\caption{需求\&预测}' in out
    assert r'\detokenize{outputs/figures/F_1.png}' in out
    assert '{{' not in out
    assert ctx.registry.render('如图{{F:F1}}所示', latex=True) == r'如图\ref{fig:F1}所示'
    ctx.registry.results['R3'] = Result('R3', 'series', 'Q1', '需求', '[510.0761904761906,514.3333333333335,518.5904761904764]', 'run_test')
    series = ctx.registry.render('{{R:R3}}', latex=True)
    assert r',\allowbreak ' in series and '510.07619' in series
    assert series.count(r'\(') == 3, 'inline vectors can break between individual values'
    assert '510.0761904761906' in ctx.registry.results['R3'].value


def test_tex_publication_versions_manual_edits_and_snapshot_artifacts(tmp_path):
    store = accepted_store(tmp_path)
    store.save_sections({'solve:Q1': '原生正文'}, [])
    source = native_document('研究', '摘要', r'\section{模型}正文')
    path, conflict = store.publish_paper(source, filename='paper.tex')
    assert path == 'paper.tex' and not conflict
    assert store.snapshot()['paper_version'].endswith('.tex')
    (tmp_path / '.system/graph_thread.json').write_text(json.dumps({'thread_id': 'p2', 'engine_version': 'research-v3', 'status': 'done'}))
    assert research_snapshot(tmp_path, 'work', 'completed').artifacts == ['paper.tex']
    manual = source + '\n% 用户修改'
    (tmp_path / 'paper.tex').write_text(manual)
    path, conflict = store.publish_paper(source + '\n% 自动修订', filename='paper.tex')
    assert conflict and path.endswith('.tex')
    assert (tmp_path / 'paper.tex').read_text() == manual
    assert (tmp_path / path).exists() and not (tmp_path / 'paper.md').exists()


def test_writing_format_is_part_of_every_section_cache(tmp_path, monkeypatch):
    store = accepted_store(tmp_path)
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), output_mode='latex')
    ctx.research = store
    state = {'analysis': problem().model_dump(), 'task': TASK, 'designs': {}, 'solutions': {}}
    old = {key: '旧 Markdown 正文' for key, _, _ in writing.section_plan(state) if key != 'solve_heading'}
    store.save_sections(old, [], {key: 'old-format-context' for key in old})
    state['sections'] = old
    calls = []
    async def write_many(ctx, state, items):
        calls.extend(key for key, _ in items)
        return {key: r'\textbf{原生正文}' + key for key, _ in items}
    monkeypatch.setattr(writing, 'write_many', write_many)
    updated = asyncio.run(writing.write(state, SimpleNamespace(context=ctx)))
    assert set(calls) == set(old)
    store.save_sections(updated['sections'], [], updated['section_contexts'])
    calls.clear()
    asyncio.run(writing.write({**state, 'sections': updated['sections']}, SimpleNamespace(context=ctx)))
    assert calls == [], 'unchanged native sections are reused; computation attempts are untouched'


@pytest.mark.skipif(not shutil.which('xelatex'), reason='xelatex unavailable')
def test_native_table_equations_and_registered_figure_compile(tmp_path):
    from matplotlib.figure import Figure
    folder = tmp_path / 'outputs/figures'
    folder.mkdir(parents=True)
    figure = Figure()
    figure.subplots().plot([1, 2], [3, 4])
    figure.savefig(folder / 'F_1.png')
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel())
    ctx.registry.results = {'F1': Result('F1', 'figure', 'Q1', '需求对比', '', 'run_test', path='outputs/figures/F_1.png')}
    body = r'\section{模型}\begin{align}y_t &= a+bt\end{align}' + '\n' + r'\begin{tabular}{lc}\toprule 方法 & 误差 \\ \midrule 趋势 & 4.05\% \\ \bottomrule\end{tabular}' + '\n\n{{F:F1}}\n见{{F:F1}}。'
    ctx.registry.results['R1'] = Result('R1', 'series', 'Q1', '预测', '[510.0761904761906,514.3333333333335,518.5904761904764,522.8476190476192,527.1047619047621]', 'run_test', unit='份')
    body += '\n\n综上所述，基于线性趋势模型的需求预测结果 {{R:R1}} 能够反映当前数据的增长规律，实际效果仍需继续验证。'
    tex = ctx.registry.render(native_document('原生 LaTeX 验证', '独立验证公式、表格与登记图表。', body), latex=True)
    (tmp_path / 'paper.tex').write_text(tex)
    result = asyncio.run(compile_pdf(tmp_path))
    assert result.pdf_path == 'paper.pdf', result.error
    assert not result.warnings
    assert 'Overfull' not in (tmp_path / '.system/latex/paper.log').read_text()
    assert (tmp_path / 'paper.pdf').read_bytes().startswith(b'%PDF')
    assert not (tmp_path / 'paper.md').exists()


def test_compile_failure_never_publishes_partial_or_stale_pdf(tmp_path, monkeypatch):
    import ai_system.export.latex as latex
    (tmp_path / 'paper.tex').write_text(r'\undefinedcommand')
    (tmp_path / 'paper.pdf').write_bytes(b'%PDF-old')
    monkeypatch.setattr(latex.shutil, 'which', lambda _: '/usr/bin/xelatex')
    async def fail(cmd, cwd, **kwargs):
        assert '-no-shell-escape' in cmd and '-halt-on-error' in cmd
        (cwd / '.system/latex/paper.pdf').write_bytes(b'%PDF-partial')
        return 1, '! Undefined control sequence.', ''
    monkeypatch.setattr(latex, 'run_command', fail)
    result = asyncio.run(compile_pdf(tmp_path))
    assert result.pdf_path is None and result.error
    assert not (tmp_path / 'paper.pdf').exists()


def test_failed_native_export_blocks_completion_and_preserves_source(tmp_path, monkeypatch):
    import ai_system.graph.nodes.export as exporter
    from ai_system.export.latex import LatexResult
    from ai_system.graph import PaperGraphAgent
    from test_paper_graph import _make_workspace, TASK as GRAPH_TASK, FixedJudge
    async def no_pdf(workspace):
        return LatexResult('paper.tex', error='模拟编译错误')
    monkeypatch.setattr(exporter, 'compile_pdf', no_pdf)
    root = _make_workspace(tmp_path)
    agent = PaperGraphAgent(FakeModel(), workspace_dir=str(root), judge=FixedJudge(), output_mode='latex')
    output = asyncio.run(agent.run(GRAPH_TASK))
    assert agent.last_result['research_blocked']
    assert 'PDF 编译未完成' in output and '模拟编译错误' in output
    assert (root / 'paper.tex').exists() and not (root / 'paper.md').exists()
    snapshot = research_snapshot(root, 'work', 'paused')
    assert snapshot.delivery_stages['export']['status'] == 'blocked'
    assert snapshot.artifacts == ['paper.tex']


def test_native_tables_cannot_invent_indexed_series_references(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), output_mode='latex')
    ctx.registry.results['R1'] = Result('R1', 'series', 'Q1', '预测', '[1,2,3]', 'run_test')
    state = {'analysis': problem().model_dump(), 'task': TASK, 'sections': {'symbols': r'{{R:R1}}[0]' + '此处应展示整组序列而不是使用未定义的索引。' * 10}}
    assert any('序列引用不支持' in issue['message'] for issue in check_sections(state, ctx))
