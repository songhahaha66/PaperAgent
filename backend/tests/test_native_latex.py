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
from ai_system.modules.writing import sections as writing
from ai_system.modules.writing.validation import check_sections
from services.file_services.workspace_structure import WorkspaceStructureManager
from services.chat_services.research_session import research_snapshot
from test_paper_graph import FakeModel
from test_research_p2 import store_for, submit, problem, TASK
from research_tools_helpers import answer
from ai_system.research.views import questions


def accepted_store(root):
    store = store_for(root)
    submit(store, 'Q1', {'demand': 8})
    submit(store, 'Q3', {'independent': 3})
    submit(store, 'Q2', {'allocation': 8, 'residual': 0})
    before=store.snapshot()
    assert not store.delivery_ready(before), 'Execution results alone do not answer the user questions'
    results=store.registry_payload()['results']
    for question in questions(before):
        if question.get('retired'):continue
        evidence=[r for r in results if r['subproblem'] in question['experiment_ids']]
        assert evidence, 'An answered fixture question must have actual current results'
        answer(store,question_id=question['id'],
            statement='在本次给定条件下，'+'；'.join(r['name']+'为 {{R:'+r['id']+'}}' for r in evidence)+'。',
            artifact_refs=[r['id'] for r in evidence],coverage='answered',
            stop_reason='对应实验已完成冻结检查，当前问题已得到给定条件下的结果。',
            limitations=['仅限本次输入与实验条件，未验证外推适用性。'])
    after=store.snapshot()
    assert store.delivery_ready(after)
    assert after['executions']==before['executions'] and after['artifacts']==before['artifacts']
    return store


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


def render_native_paper(store, state):
    """Build file-project fixtures through the same registry/citation renderers."""
    from ai_system.research.control import refresh_registry
    from ai_system.research.citations import render_citations
    from ai_system.graph.registry import publication_text, REF_PATTERN
    ctx=GraphContext.create(str(store.root),planner_llm=None,output_mode='latex')
    ctx.research=store
    refresh_registry(ctx)
    from ai_system.export.latex import escape_plain
    body='\n\n'.join(r'\section{'+escape_plain(key)+'}\n'+text for key,text in state['sections'].items())
    source=native_document(state['analysis']['title'],'',body)
    text=publication_text(render_citations(store.root,ctx.registry.render(source,latex=True),True))
    assert not REF_PATTERN.search(text)
    return text


def test_latex_workspace_starts_with_tex_not_markdown(tmp_path):
    WorkspaceStructureManager.create_workspace_structure(tmp_path, 'native', output_mode='latex')
    assert (tmp_path / 'paper.tex').exists()
    assert not (tmp_path / 'paper.md').exists()


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


def write_native_source(ctx,monkeypatch,source):
    """Controlled model, real Store entry, source edits and file versions."""
    from ai_system.modules.writing import latex
    from ai_system.modules.registry import invoke
    prompts=[]
    async def reply(llm,schema,messages,**kwargs):
        prompts.extend(messages)
        answer=schema(edits=[{'target':'paper.tex','replacement':source}])
        kwargs['validate'](answer)
        return answer
    monkeypatch.setattr(latex,'call_structured',reply)
    asyncio.run(invoke(ctx,'writing.write'))
    return prompts


def test_resume_of_already_repaired_source_does_not_call_writer(tmp_path, monkeypatch):
    if not shutil.which('xelatex'): pytest.skip('requires xelatex')
    from ai_system.modules.writing import latex
    from ai_system.modules.registry import invoke
    from services.chat_services import paper_project as project
    ctx = GraphContext.create(str(tmp_path), planner_llm=None, output_mode='latex')
    ctx.research = accepted_store(tmp_path)
    project.initialize(ctx.research)
    (tmp_path/'paper.tex').write_text(native_document('已修复报告', '已有结果。', r'\section{结果}保留已完成的计算。'))
    async def unexpected(*args, **kwargs): raise AssertionError('valid current source must not be rewritten')
    monkeypatch.setattr(latex, 'call_structured', unexpected)
    async def run():
        result = await project.compile_project(ctx.research)
        assert result['compile']['success']
        patch = await invoke(ctx, 'writing.repair')
        assert patch['repair_input'] and not patch.get('research_blocked')
    asyncio.run(run())


def test_native_source_writer_requests_latex_and_compiler_reports_undefined_labels(tmp_path,monkeypatch):
    from ai_system.research.control import refresh_registry
    ctx=GraphContext.create(str(tmp_path),planner_llm=None,output_mode='latex');ctx.research=accepted_store(tmp_path)
    refresh_registry(ctx)
    source=native_document('原生源码','摘要',r'\section{符号}\begin{tabular}{ll} $x$ & 决策 \\ \end{tabular}')
    prompts=write_native_source(ctx,monkeypatch,source)
    prompt='\n'.join(m.content for m in prompts)
    assert '原生 LaTeX' in prompt and 'SourceEdits' in prompt and 'paper.tex' in prompt
    assert (tmp_path/'paper.tex').read_text()==source and not (tmp_path/'.system/paper_raw.tex').exists()
    assert fragment_issues('### 符号说明\n| 符号 | 含义 |')
    assert fragment_issues(r'\begin{tabular}{ll} $x$ & 决策 \\ \end{tabular}') == []
    assert fragment_issues(r'\begin{align} x=y \end{equation}')
    assert fragment_issues(r'\textbf{缺少结束括号')
    assert fragment_issues(r'\input{/private/file}')
    assert fragment_issues(r'\begin{figure}{{F:F1}}\caption{重复图题}\end{figure}')
    if shutil.which('xelatex'):
        from services.chat_services import paper_project as project
        asyncio.run(project.compile_project(ctx.research))
        assert project.describe(ctx.research)['compile']['state']=='current'
        assert (tmp_path/'paper.pdf').read_bytes().startswith(b'%PDF')
        (tmp_path/'paper.tex').write_text(native_document('引用检查','摘要',r'见图\ref{fig:invented}。'))
        result=asyncio.run(compile_pdf(tmp_path))
        assert result.pdf_path is None and '未定义的交叉引用' in result.error


@pytest.mark.parametrize('text', [r'\[z=4.05\]', r'\(4.05\)', r'\begin{align}z&=4.05\end{align}', r'误差为 4.05\%'])
def test_native_literal_numbers_do_not_create_a_scientific_gate(tmp_path,monkeypatch,text):
    from ai_system.research.control import refresh_registry
    from services.chat_services import paper_project as project
    ctx=GraphContext.create(str(tmp_path),planner_llm=None,output_mode='latex');ctx.research=accepted_store(tmp_path)
    refresh_registry(ctx)
    write_native_source(ctx,monkeypatch,native_document('数字不证明科学结论','摘要',text))
    assert text in (tmp_path/'paper.tex').read_text()
    assert check_sections(project.source_state(ctx.research,{}),ctx)==[]


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
    validate_render_current_native(store)
    assert research_snapshot(tmp_path, 'work', 'completed').artifacts == ['paper.tex']
    manual = source + '\n% 用户修改'
    (tmp_path / 'paper.tex').write_text(manual)
    assert not research_snapshot(tmp_path,'work','completed').artifacts, 'an edited source must invalidate the prior validation version'
    path, conflict = store.publish_paper(source + '\n% 自动修订', filename='paper.tex')
    assert conflict and path.endswith('.tex')
    assert (tmp_path / 'paper.tex').read_text() == manual
    assert (tmp_path / path).exists() and not (tmp_path / 'paper.md').exists()


def test_word_writing_style_cache_survives_missing_graph_state(tmp_path, monkeypatch):
    store = accepted_store(tmp_path)
    ctx = GraphContext.create(str(tmp_path), planner_llm=FakeModel(), output_mode='word')
    ctx.research = store
    from ai_system.research.control import graph_view
    state=graph_view(store.snapshot())
    old = {key: '旧 Markdown 正文' for key, _, _ in writing.section_plan(state) if key != 'solve_heading'}
    store.save_sections(old, [], {key: 'old-format-context' for key in old})
    state['sections'] = old
    calls = []
    async def write_many(ctx, state, items):
        calls.extend(key for key, _ in items)
        return {key: '**Word 正文**' + key for key, _ in items}
    monkeypatch.setattr(writing, 'write_many', write_many)
    updated = asyncio.run(writing.write(state, SimpleNamespace(context=ctx)))
    assert set(calls) == set(old)
    store.save_sections(updated['sections'], [], updated['section_contexts'])
    calls.clear()
    asyncio.run(writing.write({**state, 'sections': updated['sections']}, SimpleNamespace(context=ctx)))
    assert calls == [], 'unchanged Word sections are reused; computation attempts are untouched'
    asyncio.run(writing.write({**state,'sections':{}},SimpleNamespace(context=ctx)))
    assert calls == [], 'saved sections also survive a failed node with no graph-state return'


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


def test_native_tables_cannot_invent_indexed_series_references(tmp_path,monkeypatch):
    from ai_system.research.control import refresh_registry
    from ai_system.research.models import WorkerResult
    from dataclasses import asdict
    from research_tools_helpers import continue_experiment
    store=accepted_store(tmp_path);ctx=GraphContext.create(str(tmp_path),planner_llm=None,output_mode='latex');ctx.research=store
    refresh_registry(ctx)
    run=ctx.fs.create_run();ctx.fs.save_run_stdout(run,'[1, 2, 3]');ctx.fs.finish_run(run,'success')
    item=ctx.registry.record_value('Q1','预测','[1,2,3]',run)
    assert item.kind=='series'
    continue_experiment(store,'Q1','登记已生成的完整预测序列')
    store.commit(WorkerResult(order=store.claim('Q1'),execution_status='succeeded',acceptance='accepted',
        report={'status':'completed','summary':'完整预测序列'},results=[asdict(item)]))
    with pytest.raises(ValueError,match='序列引用不支持'):
        write_native_source(ctx,monkeypatch,native_document('序列','摘要','{{R:'+item.id+'}}[0]'))


def test_publication_removes_prompt_annotations_without_losing_prose():
    from ai_system.graph.registry import publication_text
    assert publication_text('结果为 8⟨R1⟩，结论⟨C2⟩。') == '结果为 8，结论。'
    assert publication_text('正常数学符号 ⟨x,y⟩') == '正常数学符号 ⟨x,y⟩'


def test_native_source_preserves_honest_reference_gap(tmp_path,monkeypatch):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None,output_mode='latex');ctx.research=accepted_store(tmp_path)
    from ai_system.research.control import graph_view,refresh_registry
    refresh_registry(ctx)
    gap=writing.reference_section(graph_view(ctx.research.snapshot()),ctx)
    assert '来源缺口' in gap
    source=native_document('研究','摘要',r'\section{参考文献}'+gap)
    write_native_source(ctx,monkeypatch,source)
    assert r'\section{参考文献}' in (tmp_path/'paper.tex').read_text() and gap in (tmp_path/'paper.tex').read_text()


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
