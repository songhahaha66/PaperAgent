"""Native section writing keeps the evidence and publication protocol intact."""

from helpers.paper import accepted_store
import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.export.latex import compile_pdf, native_document
from ai_system.graph.context import GraphContext
from ai_system.graph.registry import Result
from ai_system.modules.writing import sections as writing
from services.chat_services.research_session import research_snapshot
from helpers.graph import FakeModel


def validate_render_current_native(store):
    """Use actual current-source validation/render; never fabricate its version."""
    from ai_system.modules.writing import validation
    from ai_system.research.control import graph_view,refresh_registry
    ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex');ctx.research=store
    refresh_registry(ctx)
    async def perform():
        state=graph_view(store.snapshot())
        checked=await validation.validate(state,SimpleNamespace(context=ctx))
        assert not checked['issues'] and not checked['research_blocked']
        return await validation.render(state,SimpleNamespace(context=ctx))
    rendered=asyncio.run(perform())
    assert rendered['paper_path']=='paper.tex'
    return ctx


def test_native_draft_is_separate_and_advisory_findings_do_not_hide_current_exports(tmp_path):
    from services.chat_services import paper_project as project
    from ai_system.research.models import IssueReport
    store=accepted_store(tmp_path)
    with store._transaction() as db:
        state=store._read(db);state['spec']['output_mode']='latex';store._save(db,state,'fixture_native_output',{})
    store.save_sections({'abstract':'已保存但尚未正式交付的正文。'},[])
    (tmp_path/'.system/graph_thread.json').write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3'}))
    (tmp_path/'paper.tex').write_text('论文草稿。')
    (tmp_path/'paper.pdf').write_bytes(b'previous unregistered PDF')
    doc=project.describe(store)
    assert doc['main']=='paper.tex' and doc['files']
    assert not research_snapshot(tmp_path,'work','paused').artifacts
    # Native render uses project files, without the legacy publish_paper hash.
    assert not store.snapshot().get('paper_hash')
    validate_render_current_native(store)
    assert store.snapshot()['delivery_stages']['render']['status']=='completed'
    assert project.is_latex(store)
    assert json.loads((tmp_path/'.system/validation.json').read_text())['source_version']==project.snapshot(tmp_path)['version']
    assert research_snapshot(tmp_path,'work','completed').artifacts==['paper.tex']
    store.register_exports(['paper.tex','paper.pdf'])
    assert research_snapshot(tmp_path,'work','completed').artifacts==['paper.tex']
    assert project.describe(store)['pdf_available'], 'old PDF preview remains, but registration alone does not validate its source version'
    store.report_issues([IssueReport(task_id='Q1',category='model',message='还需核验科学结论')],'local_review')
    assert research_snapshot(tmp_path,'work','paused').artifacts==['paper.tex']
    assert store.delivery_ready(store.snapshot()), 'advisory findings cannot revoke recorded question answers'
    assert project.describe(store)['files'], 'an advisory finding must not hide the editable draft'


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
    assert (tmp_path / 'paper.pdf').read_bytes() == b'%PDF-old'


def test_supplied_bibliography_is_preserved_without_invented_metadata(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), output_mode='latex')
    state = {'task': '研究任务\n参考文献：\n作者甲. 用户提供的题名. 2024. https://example.test/a?x=1&y=2\n# 其他要求\n不要把这行当参考文献'}
    text = writing.reference_section(state, ctx)
    assert '用户提供的题名' in text and r'\url{https://example.test/a?x=1&y=2}' in text
    assert '不要把这行' not in text and '尚未独立核验' in text
    assert '尚未登记' not in text
    attachment = tmp_path / 'attachment'
    attachment.mkdir()
    (attachment / 'sources.bib').write_text('@article{source, title={Provided title}}')
    text = writing.reference_section({'task': '任务'}, ctx)
    assert 'Provided title' in text and '未补造' in text
