"""Reviewable section edits over the authoritative research store.

Candidates do not mutate research. Publication is a compare-and-swap against the
whole research commit and active source, including changes made outside this UI.
"""
import asyncio
import json
import re
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from ai_system.graph.build import compile_graph
from ai_system.graph.context import GraphContext
from ai_system.graph.registry import REF_PATTERN, publication_text
from ai_system.graph.llm_utils import as_messages, call_text
from ai_system.graph.nodes.write import section_plan, writing_style
from ai_system.graph.nodes.validate import assemble, assemble_latex
from ai_system.research.store import ResearchStore, digest
from ai_system.research.citations import audit_citations, citation_issues, render_citations
from ai_system.research.literature import CITATION
from ai_system.research.models import ResearchConflict
from ai_system.research.control import refresh_registry
from ai_system.export.latex import compile_pdf, fragment_issues
from ai_system.export import export_docx


PUBLICATION_MARKER = '.system/paper-publication.json'


def recover_publication(store):
    """Resolve an interrupted file publication against the committed decision.

    BEGIN IMMEDIATE waits for an in-flight publisher. A committed applied decision
    keeps its files; a rolled-back decision restores every previous artifact.
    Backups survive failed recovery so a later request can retry safely.
    """
    marker = store.root / PUBLICATION_MARKER
    if not marker.exists():
        return
    with store._transaction() as db:
        if not marker.exists():
            return
        record = json.loads(marker.read_text(encoding='utf-8'))
        proposal_id = record['proposal_id']
        if not re.fullmatch(r'[a-f0-9]{32}', proposal_id):
            raise ValueError('论文发布恢复记录无效')
        folder = marker.parent / 'paper-publication' / proposal_id
        row = db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',
                         (record['research_id'], 'paper-edit:' + proposal_id)).fetchone()
        applied = row and json.loads(row[0])['status'] == 'applied'
        if not applied:
            for name, existed in record['files'].items():
                if name not in {'paper.md', 'paper.tex', 'paper.pdf', 'paper.docx'}:
                    raise ValueError('论文发布恢复路径无效')
                target = store.root / name
                if existed:
                    temporary = target.with_suffix(target.suffix + '.restore-tmp')
                    shutil.copyfile(folder / (name + '.before'), temporary)
                    temporary.replace(target)
                else:
                    target.unlink(missing_ok=True)
        marker.unlink()
        shutil.rmtree(folder, ignore_errors=True)


def prepare_publication(store, proposal_id, staging, names):
    """Called under the SQLite writer lock, before replacing any active file."""
    marker = store.root / PUBLICATION_MARKER
    if marker.exists():
        raise ResearchConflict('尚有待恢复的论文发布，请刷新后重试')
    folder = marker.parent / 'paper-publication' / proposal_id
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in names:
        target = store.root / name
        files[name] = target.exists()
        if target.exists():
            shutil.copyfile(target, folder / (name + '.before'))
        shutil.copyfile(staging / name, target.with_suffix(target.suffix + '.edit-tmp'))
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({'proposal_id': proposal_id, 'research_id': store.research_id,
                                     'files': files}), encoding='utf-8')
    temporary.replace(marker)
    for name in names:
        target = store.root / name
        target.with_suffix(target.suffix + '.edit-tmp').replace(target)


def current_store(root):
    thread = json.loads((Path(root) / '.system/graph_thread.json').read_text())
    if thread.get('engine_version') != 'research-v3':
        raise ValueError('当前研究不支持章节修改')
    store = ResearchStore(root, thread['thread_id'])
    recover_publication(store)
    return store


async def paper_state(store):
    async with AsyncSqliteSaver.from_conn_string(str(store.root / '.system/graph.sqlite')) as saver:
        saved = await compile_graph(saver).aget_state({'configurable': {'thread_id': store.research_id}})
    if not saved.values.get('analysis'):
        raise ValueError('尚无可编辑的论文记录')
    return {**saved.values, 'sections': {k: v['text'] for k, v in store.snapshot()['sections'].items()}}


def source_info(store, snapshot):
    filename = 'paper.tex' if str(snapshot.get('paper_version', '')).endswith('.tex') else 'paper.md'
    path = store.root / filename
    if not path.exists() or not snapshot.get('paper_hash'):
        raise ValueError('论文尚未发布，请先完成研究')
    content = path.read_text(encoding='utf-8')
    if digest(content) != snapshot['paper_hash'] or snapshot.get('paper_conflict'):
        raise ResearchConflict('源文件有人工修改。已保留人工稿，请先合并后再提出章节修改。')
    return filename, content


async def document(store):
    state = await paper_state(store)
    snapshot = store.snapshot()
    titles = {k: title for k, title, _ in section_plan(state)}
    ctx = GraphContext.create(str(store.root), planner_llm=None)
    ctx.research = store
    refresh_registry(ctx)
    readable = {}
    native = str(snapshot.get('paper_version', '')).endswith('.tex')
    for key, section in snapshot['sections'].items():
        text = publication_text(ctx.registry.render(section['text'], latex=native))
        if native:
            text = CITATION.sub(lambda m:'PAPERAGENTCITE'+m.group(1),text)
        if native and shutil.which('pandoc'):
            process = await asyncio.create_subprocess_exec('pandoc', '--sandbox', '-f', 'latex', '-t', 'gfm+tex_math_dollars', '--wrap=none', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, _ = await asyncio.wait_for(process.communicate(text.encode()), timeout=15)
                if process.returncode == 0:
                    # markdown-it-katex expects display math on its own block;
                    # Pandoc can place $$...$$ inside a prose paragraph.
                    text = re.sub(r'(?<!\\)\$\$(.*?)(?<!\\)\$\$',
                                  lambda m: '\n\n$$\n' + m.group(1).strip() + '\n$$\n\n',
                                  out.decode(), flags=re.S)
            except TimeoutError:
                process.kill()
                await process.wait()
        readable[key] = render_citations(store.root,re.sub(r'PAPERAGENTCITE(C[1-9]\d*)',lambda m:'{{C:'+m.group(1)+'}}',text))
    with store._connect() as db:
        history = [json.loads(row[0]) for row in db.execute(
            "SELECT response FROM commands WHERE research_id=? AND id LIKE 'paper-edit:%' ORDER BY rowid DESC LIMIT 30",
            (store.research_id,))]
    from ai_system.research.method_facts import verified_methods,canonical_method_block
    methods=verified_methods(store)
    blocks={}
    for task in {f['task_id'] for f in methods}:
        try:blocks['solve:'+task]=canonical_method_block(methods,task)
        except ValueError:pass
    return {'research_id': store.research_id, 'commit_seq': snapshot['commit_seq'],'verified_methods':methods,'verified_formula_blocks':blocks,
            'sections': [{'key': k, 'title': titles.get(k, k), 'readable': readable[k], **v} for k, v in snapshot['sections'].items()],
            'history': history, 'tasks': [{'id': k, 'title': v['objective'], 'dependencies': v['dependencies']}
                                        for k, v in snapshot['tasks'].items() if not v['retired']]}


def guard_wording(before, after, latex):
    if not after.strip() or len(after) > 100000:
        raise ValueError('修改后的章节不能为空或超过 100000 字符')
    # Wording edits cannot silently change evidence, numeric values or equations.
    from ai_system.graph.registry import MATH_PATTERN
    signature = lambda s: (Counter(REF_PATTERN.findall(s)), Counter(re.findall(r'\d+(?:\.\d+)?', CITATION.sub('',s))), MATH_PATTERN.findall(s))
    if signature(before) != signature(after):
        raise ValueError('修改改变了数字、公式或证据引用，请使用“研究修改”进行局部重算')
    if latex and fragment_issues(after):
        raise ValueError('；'.join(fragment_issues(after)))


async def propose(store, key, instruction, content=None, llm=None,*,edit_kind='wording',section_keys=None,section_contents=None):
    if edit_kind=='evidence':
        if content is not None:raise ValueError('依据修正须通过带证据审查的候选生成')
        from .paper_corrections import propose_correction
        return await propose_correction(store,section_keys or [key],instruction,llm,section_contents)
    if section_keys or section_contents:raise ValueError('多节修正需要选择按已核验证据修正模式')
    snapshot = store.refresh_inputs()
    filename, source = source_info(store, snapshot)
    section = snapshot['sections'].get(key)
    if not section or section['validity'] != 'current' or not store.delivery_ready(snapshot):
        raise ResearchConflict('章节或研究证据已失效，请完成局部重算后再修改')
    before = section['text']
    if content is None:
        if llm is None:
            raise ValueError('没有配置写作模型；可以直接编辑候选正文')
        ctx = GraphContext.create(str(store.root), planner_llm=llm, output_mode='latex' if filename.endswith('.tex') else 'markdown')
        from ai_system.research.literature import literature_context
        content = await call_text(llm, as_messages(writing_style(ctx),
            '仅润色下面这个章节，保持全部研究事实、结论强度、数字、公式和引用不变。不得根据指令修改方法或数据。只返回完整章节正文。\n'
            + '修改要求：' + instruction + '\n当前正文：\n' + before + literature_context(store.root,instruction)))
    content = publication_text(content)
    guard_wording(before, content, filename.endswith('.tex'))
    checks = await audit_citations(store.root,[content],llm) if CITATION.search(content) else []
    proposal = {'id': uuid.uuid4().hex, 'section': key, 'instruction': instruction, 'before': before, 'after': content,
                'base_commit': snapshot['commit_seq'], 'source_hash': digest(source), 'filename': filename,
                'status': 'pending', 'section_revision': section['revision'], 'citation_checks':checks,
                'citation_issues':citation_issues(store.root,content)}
    with store._transaction() as db:
        if store._read(db)['commit_seq'] != snapshot['commit_seq']:
            raise ResearchConflict('研究已更新，请刷新后重新提出修改')
        db.execute('INSERT INTO commands VALUES (?, ?, ?, ?)',
                   (store.research_id, 'paper-edit:' + proposal['id'], digest(proposal), json.dumps(proposal, ensure_ascii=False)))
    return proposal


def get_proposal(store, proposal_id):
    with store._connect() as db:
        row = db.execute('SELECT response FROM commands WHERE research_id=? AND id=?',
                         (store.research_id, 'paper-edit:' + proposal_id)).fetchone()
    if not row:
        raise ValueError('修改候选不存在')
    return json.loads(row[0])


async def decide(store, proposal_id, apply, output_mode):
    recover_publication(store)
    proposal = get_proposal(store, proposal_id)
    if proposal['status'] != 'pending':
        return proposal
    if not apply:
        with store._transaction() as db:
            latest = json.loads(db.execute('SELECT response FROM commands WHERE research_id=? AND id=?', (store.research_id, 'paper-edit:' + proposal_id)).fetchone()[0])
            if latest['status'] != 'pending':
                return latest
            proposal['status'] = 'rejected'
            db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',
                       (json.dumps(proposal, ensure_ascii=False), store.research_id, 'paper-edit:' + proposal_id))
        return proposal
    snapshot = store.refresh_inputs()
    filename, source = source_info(store, snapshot)
    if snapshot['commit_seq'] != proposal['base_commit'] or digest(source) != proposal['source_hash']:
        raise ResearchConflict('论文或研究版本已改变。候选和人工稿均已保留，请刷新后重新提出修改。')
    state = await paper_state(store)
    changes=proposal.get('after_sections') or {proposal['section']:proposal['after']}
    problems = [p for text in changes.values() for p in citation_issues(store.root,text)]
    if problems:
        raise ValueError('文献引用未获支持：'+'；'.join(problems))
    state['sections'].update(changes)
    ctx = GraphContext.create(str(store.root), planner_llm=None, output_mode=output_mode)
    ctx.research = store
    refresh_registry(ctx)
    native = filename.endswith('.tex')
    reference_update = None
    if 'references' not in changes and any(CITATION.search(t) for t in changes.values()) and 'references' in state['sections']:
        from ai_system.graph.nodes.write import reference_section
        reference_update = reference_section(state,ctx)
        state['sections']['references'] = reference_update
    if proposal.get('edit_kind')=='evidence':
        from .paper_corrections import require_review,guard_correction
        from ai_system.research.method_facts import canonical_method_block
        if proposal.get('review_issues'):raise ValueError('候选尚有审查问题，不能应用')
        for key,text in changes.items():
            block=canonical_method_block(proposal['method_facts'],key.split(':',1)[1]) if key.startswith('solve:') else None
            guard_correction(snapshot['sections'][key]['text'],text,block,native)
        require_review(store,proposal,state)
    rendered = publication_text(render_citations(store.root,ctx.registry.render(assemble_latex(state) if native else assemble(state), latex=native),native))
    if REF_PATTERN.search(rendered):
        raise ValueError('论文包含未解析的引用，禁止发布')
    # Compile in isolation; a failed export must not replace a good paper/PDF.
    with tempfile.TemporaryDirectory(prefix='paper-edit-') as folder:
        staging = Path(folder)
        (staging / filename).write_text(rendered, encoding='utf-8')
        if (store.root / 'outputs').exists():
            shutil.copytree(store.root / 'outputs', staging / 'outputs')
        exports = []
        if native:
            result = await compile_pdf(staging)
            if not result.pdf_path:
                raise ValueError(result.error or 'PDF 编译失败，候选已保留')
            exports = ['paper.pdf']
        elif output_mode == 'word':
            from ai_system.graph.context import TEMPLATE_DOCX
            template = store.root / TEMPLATE_DOCX
            result = await export_docx(staging, rendered, template_docx=template if template.exists() else None)
            exports = [result.docx_path]
        store.refresh_inputs()
        try:
            with store._transaction() as db:
                latest = json.loads(db.execute('SELECT response FROM commands WHERE research_id=? AND id=?', (store.research_id, 'paper-edit:' + proposal_id)).fetchone()[0])
                if latest['status'] != 'pending':
                    return latest
                current = store._read(db)
                _, active = source_info(store, current)
                if current['commit_seq'] != proposal['base_commit'] or digest(active) != proposal['source_hash']:
                    raise ResearchConflict('编译期间论文发生变化，未覆盖最新版本，请刷新后重试')
                if proposal.get('edit_kind')=='evidence':
                    from ai_system.research.method_facts import verified_methods
                    if digest(verified_methods(store))!=proposal['method_facts_hash']:
                        raise ResearchConflict('编译期间已核验方法改变，未覆盖当前论文')
                # Persist both sides in the existing version journal and paper archive.
                archive = store.root / '.system/research' / store.research_id / 'papers'
                archive.mkdir(parents=True, exist_ok=True)
                old_name = f'{current["commit_seq"]}-before-edit{Path(filename).suffix}'
                (archive / old_name).write_text(source, encoding='utf-8')
                version = archive / f'{current["commit_seq"] + 1}{Path(filename).suffix}'
                version.write_text(rendered, encoding='utf-8')
                versions={}
                for key,text in changes.items():
                    section=current['sections'][key]
                    section.update(text=text,content_hash=digest(text),revision=section['revision']+1)
                    versions[key]=section
                if reference_update is not None and current['sections']['references']['text']!=reference_update:
                    references = current['sections']['references']
                    references.update(text=reference_update,content_hash=digest(reference_update),revision=references['revision']+1)
                    versions['references'] = references
                current.update(paper_hash=digest(rendered), paper_version=version.relative_to(store.root).as_posix(), paper_conflict=False,
                               exports=[filename] + exports)
                prepare_publication(store, proposal_id, staging, [filename] + exports)
                proposal.update(status='applied', applied_revision=current['sections'][proposal['section']]['revision'],
                    applied_revisions={k:v['revision'] for k,v in versions.items()})
                store._save(db, current, 'section_edit_applied', {'proposal': proposal, 'versions': versions})
                db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',
                           (json.dumps(proposal, ensure_ascii=False), store.research_id, 'paper-edit:' + proposal_id))
        except BaseException:
            recover_publication(store)
            raise
        recover_publication(store)
    return proposal
