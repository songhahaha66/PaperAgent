"""Native section writing keeps the evidence and publication protocol intact."""
from services.file_services.workspace_layout import workspace_path

from helpers.paper import accepted_store
import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.export.latex import compile_pdf
from helpers.native_document import native_document
from ai_system.graph.context import GraphContext
from ai_system.graph.registry import Result
from ai_system.modules.writing import sections as writing
from services.chat_services.research_session import research_snapshot
from helpers.graph import FakeModel


pytestmark = pytest.mark.usefixtures("offline_kernel", "offline_compiler")

def validate_render_current_native(store):
    """Use actual current-source validation/render; never fabricate its version."""
    from ai_system.modules.writing.export import export
    from ai_system.research.control import graph_view,refresh_registry
    ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex');ctx.research=store
    refresh_registry(ctx)
    async def perform():
        state=graph_view(store.snapshot())
        return await export(state,SimpleNamespace(context=ctx))
    rendered=asyncio.run(perform())
    assert not rendered.get('research_blocked') and not rendered.get('issues'), rendered
    assert rendered['paper_path']=='paper.tex'
    return ctx


def test_native_draft_is_separate_and_advisory_findings_do_not_hide_current_exports(tmp_path, monkeypatch):
    from services.chat_services import paper_project as project
    from ai_system.research.models import IssueReport
    import ai_system.export.latex as latex
    async def compile_fixture(tex_name, cwd, **kwargs):
        assert tex_name == 'paper.tex'
        (cwd / '.build/latex/paper.pdf').write_bytes(b'%PDF-verified-fixture')
        (cwd / '.build/latex/paper.log').write_text('Output written on paper.pdf (1 page).')
        (cwd / '.build/latex/compile.log').write_text('xelatex pass 1, exit 0\nxelatex pass 2, exit 0')
        return 0, json.dumps({'failed_stage':None,'timed_out':False,'log_path':'.build/latex/paper.log'}), ''
    monkeypatch.setattr(latex, 'run_latex', compile_fixture)
    store=accepted_store(tmp_path)
    with store._transaction() as db:
        state=store._read(db);state['spec']['output_mode']='latex';store._save(db,state,'fixture_native_output',{})
    store.save_sections({'abstract':'已保存但尚未正式交付的正文。'},[])
    (workspace_path(tmp_path, '.system/graph_thread.json')).write_text(json.dumps({'thread_id':store.research_id,'engine_version':'research-v3'}))
    (tmp_path/'paper.tex').write_text(r'\documentclass{article}\begin{document}Draft.\end{document}')
    (tmp_path/'paper.pdf').write_bytes(b'previous unregistered PDF')
    doc=project.describe(store)
    assert doc['main']=='paper.tex' and doc['files']
    assert not research_snapshot(tmp_path,'work','paused').artifacts
    # Native render uses project files, without the legacy publish_paper hash.
    assert not store.snapshot().get('paper_hash')
    validate_render_current_native(store)
    assert store.snapshot()['delivery_stages']['export']['status']=='completed'
    assert project.is_latex(store)
    assert store.snapshot()['export_source_version']==project.snapshot(tmp_path)['version']
    assert set(research_snapshot(tmp_path,'work','completed').artifacts)=={'paper.tex','paper.pdf'}
    store.register_exports(['paper.tex','paper.pdf'])
    assert set(research_snapshot(tmp_path,'work','completed').artifacts)=={'paper.tex','paper.pdf'}
    assert project.describe(store)['pdf_available'], 'old PDF preview remains, but registration alone does not validate its source version'
    store.report_issues([IssueReport(task_id='Q1',category='model',message='还需核验科学结论')],'local_review')
    assert set(research_snapshot(tmp_path,'work','paused').artifacts)=={'paper.tex','paper.pdf'}
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
    assert 'Overfull' not in (tmp_path / '.build/latex/paper.log').read_text()
    assert (tmp_path / 'paper.pdf').read_bytes().startswith(b'%PDF')
    assert not (tmp_path / 'paper.md').exists()


def test_compile_failure_never_publishes_partial_or_stale_pdf(tmp_path, monkeypatch):
    import ai_system.export.latex as latex
    (tmp_path / 'paper.tex').write_text(r'\documentclass{article}\begin{document}\undefinedcommand\end{document}')
    (tmp_path / 'paper.pdf').write_bytes(b'%PDF-old')
    calls=[]
    async def fail(tex_name, cwd, **kwargs):
        calls.append(tex_name)
        (cwd / '.build/latex/paper.pdf').write_bytes(b'%PDF-partial')
        (cwd / '.build/latex/paper.log').write_text('! Undefined control sequence.')
        (cwd / '.build/latex/compile.log').write_text('xelatex pass 1, exit 1\n! Undefined control sequence.')
        return 1, json.dumps({'failed_stage':'xelatex','timed_out':False,'log_path':'.build/latex/paper.log'}), ''
    monkeypatch.setattr(latex, 'run_latex', fail)
    result = asyncio.run(compile_pdf(tmp_path))
    assert len(calls)==1 and result.exit_code==1
    assert result.pdf_path is None and result.error
    assert (tmp_path / 'paper.pdf').read_bytes() == b'%PDF-old'


def test_export_reuses_archived_pdf_and_invalidates_source_and_asset_changes(tmp_path, monkeypatch):
    from ai_system.export.latex import LatexResult
    from ai_system.modules.writing.export import _latex
    from services.chat_services import paper_project as project
    from helpers.research import make_store
    store=make_store(tmp_path)
    (tmp_path/'chart.png').write_bytes(b'chart-v1')
    (tmp_path/'paper.tex').write_text(r'\documentclass{article}\begin{document}\includegraphics{chart.png}Text\end{document}')
    ctx=GraphContext.create(str(tmp_path),planner_llm=None,output_mode='latex');ctx.research=store
    builds=[]
    async def compile_fixture(staging, main, **kwargs):
        builds.append(project.snapshot(tmp_path)['version'])
        await asyncio.sleep(0)
        if len(builds)==4:return LatexResult(main,error='controlled compile error')
        (staging/'paper.pdf').write_bytes(f'PDF-{len(builds)}'.encode())
        return LatexResult(main,pdf_path='paper.pdf',pages=1)
    monkeypatch.setattr('ai_system.export.latex.compile_pdf',compile_fixture)
    async def run():
        first,concurrent=await asyncio.gather(project.compile_project(store),_latex(ctx))
        assert first['compiled'] is True and concurrent['compiled'] is False and len(builds)==1
        (tmp_path/'paper.pdf').write_bytes(b'changed public copy')
        same=await _latex(ctx)
        assert same['compiled'] is False and len(builds)==1
        assert (tmp_path/'paper.pdf').read_bytes()==b'PDF-1'
        (tmp_path/'chart.png').write_bytes(b'chart-v2')
        assert (await _latex(ctx))['compiled'] is True and len(builds)==2
        path=tmp_path/'paper.tex';path.write_text(path.read_text().replace('Text','Revised'))
        assert (await _latex(ctx))['compiled'] is True and len(builds)==3
        path.write_text(path.read_text().replace('Revised','Broken'))
        assert (await _latex(ctx)).get('issues') and len(builds)==4
        assert (tmp_path/'paper.pdf').read_bytes()==b'PDF-3'
        assert project.meta(tmp_path)['compile']['state']=='failed'
    asyncio.run(run())


def test_batch_worker_runs_bibliography_and_stops_on_failed_step(tmp_path, monkeypatch):
    from ai_system.export import latex_worker
    monkeypatch.chdir(tmp_path)
    calls=[]
    fail_bibliography=False
    def command(argv, **kwargs):
        calls.append(argv)
        build=tmp_path/'.build/latex'
        if argv[0]=='bibtex':
            assert argv==['bibtex','paper'] and kwargs['cwd']==Path('.build/latex')
            assert kwargs['env']['BIBINPUTS'].startswith(str(tmp_path))
            (build/'paper.blg').write_text('bad bibliography' if fail_bibliography else 'bibliography completed')
            return SimpleNamespace(returncode=2 if fail_bibliography else 0,stdout=b'',stderr=b'')
        assert '-no-shell-escape' in argv and '-halt-on-error' in argv
        count=sum(c[0]=='xelatex' for c in calls)
        (build/'paper.aux').write_text(r'\bibdata{refs}')
        (build/'paper.log').write_text('Label(s) may have changed' if count==2 else 'Output written on paper.pdf (1 page).')
        return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
    monkeypatch.setattr(latex_worker.subprocess,'run',command)
    result=latex_worker.compile_latex('paper.tex',180)
    assert [c[0] for c in calls]==['xelatex','bibtex','xelatex','xelatex']
    assert result['exit_code']==0 and len(result['steps'])==4
    calls.clear();fail_bibliography=True
    result=latex_worker.compile_latex('paper.tex',180)
    assert [c[0] for c in calls]==['xelatex','bibtex']
    assert result['exit_code']==2 and result['failed_stage']=='bibtex'
    assert 'bibtex, exit 2' in (tmp_path/'.build/latex/compile.log').read_text()


def test_supplied_bibliography_is_preserved_without_invented_metadata(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), output_mode='latex')
    state = {'task': '研究任务\n参考文献：\n作者甲. 用户提供的题名. 2024. https://example.test/a?x=1&y=2\n# 其他要求\n不要把这行当参考文献'}
    text = writing.reference_section(state, ctx)
    assert '用户提供的题名' in text and r'\url{https://example.test/a?x=1&y=2}' in text
    assert '不要把这行' not in text and '尚未独立核验' in text
    assert '尚未登记' not in text
    attachment = tmp_path / 'attachment'
    attachment.mkdir(exist_ok=True)
    (attachment / 'sources.bib').write_text('@article{source, title={Provided title}}')
    text = writing.reference_section({'task': '任务'}, ctx)
    assert 'Provided title' in text and '未补造' in text
