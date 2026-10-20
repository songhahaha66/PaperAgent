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
from ai_system.graph.registry import REF_PATTERN, MATH_PATTERN, publication_text
from ai_system.modules.writing.sections import section_plan
from ai_system.modules.writing.validation import assemble, assemble_latex, place_figures
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


def empty_document(research_id=None, commit_seq=0):
    return {'research_id': research_id, 'commit_seq': commit_seq, 'source_hash': None,
            'sections': [], 'outline': [], 'feedback': [], 'history': [], 'experiments': [], 'patch_attempts': [],
            'verified_methods': [], 'verified_formula_blocks': {}, 'artifacts': []}


async def _native_preview(text):
    # New GFM writers need tex_math_gfm disabled for markdown-it-katex. Older
    # distro Pandoc releases do not know that extension and already emit $ math.
    for target in ('gfm+tex_math_dollars-tex_math_gfm','gfm+tex_math_dollars'):
        process=await asyncio.create_subprocess_exec('pandoc','--sandbox','-f','latex','-t',target,'--wrap=none',
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            out,error=await asyncio.wait_for(process.communicate(text.encode()),timeout=15)
        except TimeoutError:
            process.kill()
            await process.wait()
            return text
        if process.returncode==0:
            # Display math must be a separate block for markdown-it-katex.
            return re.sub(r'(?<!\\)\$\$(.*?)(?<!\\)\$\$',
                          lambda m:'\n\n$$\n'+m.group(1).strip()+'\n$$\n\n',out.decode(),flags=re.S)
        error=error.lower()
        if b'tex_math_gfm' not in error or not (b'unknown extension' in error or b'not supported' in error):
            break
    return text


async def document(store):
    snapshot = store.snapshot()
    if not snapshot or not any(s.get('text', '').strip() for s in snapshot['sections'].values()):
        return empty_document(store.research_id, (snapshot or {}).get('commit_seq', 0))
    state = await paper_state(store)
    plan = section_plan(state)
    titles = {k: title for k, title, _ in plan}
    levels = {k: level for k, _, level in plan}
    # Writing completion and JSON storage order are not document order. Use the
    # same outline as assembly/export, retaining unmatched historical sections.
    keys = [key for key, _, _ in plan if key in snapshot['sections']]
    keys.extend(key for key in snapshot['sections'] if key not in titles)
    ctx = GraphContext.create(str(store.root), planner_llm=None)
    ctx.research = store
    refresh_registry(ctx)
    readable = {}
    native = (store.root/'paper.tex').is_file() or str(snapshot.get('paper_version', '')).endswith('.tex')
    for key, section in snapshot['sections'].items():
        text = section['text']
        # An empty paper.tex may accompany legacy Markdown. Converting that
        # draft as TeX escapes headings, tables and images into literal text.
        markdown = re.search(r'(?m)^ {0,3}#{1,6}\s|^\s*\|.+\|\s*$', text)
        fragment_native = native and not markdown and bool(re.search(r'\\(?:[A-Za-z]+|[%&#_])', MATH_PATTERN.sub('', text)))
        if key.startswith('solve:'):
            text = place_figures(text)
        text = publication_text(ctx.registry.render(text, latex=fragment_native, links=not fragment_native))
        if fragment_native:
            text = CITATION.sub(lambda m:'PAPERAGENTCITE'+m.group(1),text)
        if fragment_native and shutil.which('pandoc'):
            text = await _native_preview(text)
        readable[key] = render_citations(store.root,re.sub(r'PAPERAGENTCITE(C[1-9]\d*)',lambda m:'{{C:'+m.group(1)+'}}',text))
    with store._connect() as db:
        history = [json.loads(row[0]) for row in db.execute(
            "SELECT response FROM commands WHERE research_id=? AND id LIKE 'paper-edit:%' ORDER BY rowid DESC LIMIT 30",
            (store.research_id,))]
        patch_attempts=([json.loads(r[0]) for r in db.execute('SELECT payload FROM paper_patch_attempts WHERE research_id=? ORDER BY rowid DESC LIMIT 20',(store.research_id,))]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_patch_attempts'").fetchone() else [])
    from ai_system.research.method_facts import verified_methods,canonical_method_block
    from types import SimpleNamespace
    methods=verified_methods(store)
    blocks={}
    for task in {f['task_id'] for f in methods}:
        try:blocks['solve:'+task]=canonical_method_block(methods,task)
        except ValueError:pass
    source = 'paper.tex' if native else 'paper.md'
    source_path = store.root / source
    try:
        source_current = bool(snapshot.get('paper_hash') and source_path.is_file()
                              and digest(source_path.read_text(encoding='utf-8')) == snapshot['paper_hash'])
    except (OSError, UnicodeError):
        source_current = False
    published = bool(source_current and not snapshot.get('paper_conflict') and store.delivery_ready(snapshot)
                     and all(section['validity'] == 'current' for section in snapshot['sections'].values()))
    available = {source, *snapshot.get('exports', [])} if published else set()
    if (store.root/'paper.tex').is_file():
        available.update({'paper.tex','paper.pdf'})
    return {'title': state['analysis'].get('title', ''), 'research_id': store.research_id, 'commit_seq': snapshot['commit_seq'],'source_hash':snapshot.get('paper_hash'),'verified_methods':methods,'verified_formula_blocks':blocks,'patch_attempts':patch_attempts,
            'publication_notice': '源文件有人工修改，须先合并；当前目录展示登记正文，旧排版文件不作为当前版本。' if snapshot.get('paper_hash') and (not source_current or snapshot.get('paper_conflict')) else '',
            'artifacts': [p for p in ('paper.md', 'paper.tex', 'paper.pdf', 'paper.docx') if p in available and (store.root / p).is_file() and (store.root / p).stat().st_size],
            'outline': [{'key': key, 'title': title, 'level': level,
                         'available': bool(snapshot['sections'].get(key, {}).get('text', '').strip())}
                        for key, title, level in plan],
            'sections': [{'key': k, 'title': titles.get(k, '未归类章节'), 'level': levels.get(k, 2), 'readable': readable[k], **snapshot['sections'][k]}
                         for k in keys if snapshot['sections'][k].get('text', '').strip()],
            'feedback': [{field: item.get(field) for field in ('id', 'message', 'category', 'section', 'task_id', 'status')}
                         for item in snapshot['feedback'] if item['status'] not in {'resolved', 'dismissed', 'superseded'}],
            'history': history, 'experiments': [{'id': k, 'title': v['objective'], 'dependencies': v['dependencies']}
                                        for k, v in snapshot['experiments'].items() if not v['retired']]}


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


async def propose(store, key, instruction, content=None, llm=None,*,edit_kind='wording',section_keys=None,section_contents=None,fragment_scope=None,recovery_of=None,source_issue=None):
    if (store.root / 'paper.tex').is_file():
        raise ResearchConflict('LaTeX 文稿请使用源码选区评论与文件补丁；旧章节提案已停用以保护原稿')
    if source_issue:
        from .paper_corrections import propose_correction
        return await propose_correction(store,section_keys or [key],instruction,llm,section_contents or ({key:content} if content is not None else None),
            fragment_scope=fragment_scope,recovery_of=recovery_of,wording=edit_kind=='wording',source_issue=source_issue)
    if edit_kind=='evidence':
        if content is not None:raise ValueError('依据修正须通过带证据审查的候选生成')
        from .paper_corrections import propose_correction
        return await propose_correction(store,section_keys or [key],instruction,llm,section_contents)
    if section_keys or section_contents:raise ValueError('多节修正需要选择按已核验证据修正模式')
    if content is None:
        if not fragment_scope:raise ValueError('请明确选择一个原文短片段，自动润色不再生成整节正文')
        from .paper_corrections import propose_correction
        return await propose_correction(store,[key],instruction,llm,fragment_scope=fragment_scope,recovery_of=recovery_of,wording=True)
    snapshot = store.refresh_inputs()
    filename, source = source_info(store, snapshot)
    section = snapshot['sections'].get(key)
    if not section or section['validity'] != 'current' or not store.delivery_ready(snapshot):
        raise ResearchConflict('章节或研究证据已失效，请完成局部重算后再修改')
    before = section['text']
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
    if apply and (store.root / 'paper.tex').is_file():
        raise ResearchConflict('该历史章节提案保留供参考；请在当前源码上重新评论，避免覆盖 LaTeX 项目')
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
    if proposal.get('source_issue'):
        from .issue_edits import require_binding,ready
        require_binding(store,proposal['source_issue'])
        if not ready(store):raise ResearchConflict('研究或服务问题尚未通过核查，不能以文稿修改绕过')
    state = await paper_state(store)
    changes=proposal.get('after_sections') or {proposal['section']:proposal['after']}
    if proposal.get('generation_contract')=='scoped-anchored-edits-v2':
        from .paper_corrections import require_scope
        require_scope(store,proposal,snapshot,source)
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
        from ai_system.modules.writing.sections import reference_section
        reference_update = reference_section(state,ctx)
        state['sections']['references'] = reference_update
    reviewed=bool(proposal.get('source_issue')) or proposal.get('edit_kind')=='evidence' or proposal.get('generation_contract')=='scoped-anchored-edits-v2'
    if reviewed:
        from .paper_corrections import require_review,guard_correction
        from ai_system.research.method_facts import canonical_method_block
        if proposal.get('review_issues'):raise ValueError('候选尚有审查问题，不能应用')
        for key,text in changes.items():
            block=canonical_method_block(proposal['method_facts'],key.split(':',1)[1]) if key.startswith('solve:') and proposal.get('edit_kind')=='evidence' else None
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
                if reviewed:
                    from ai_system.research.method_facts import verified_methods
                    if digest(verified_methods(store))!=proposal['method_facts_hash']:
                        raise ResearchConflict('编译期间已核验方法改变，未覆盖当前论文')
                issue_proof=None
                if proposal.get('source_issue'):
                    from .issue_edits import require_review as require_issue_review
                    issue_proof=require_issue_review(store,proposal,state)
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
                if issue_proof:
                    from .issue_edits import resolve
                    resolve(current,proposal,issue_proof)
                store._save(db, current, 'section_edit_applied', {'proposal': proposal, 'versions': versions})
                db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',
                           (json.dumps(proposal, ensure_ascii=False), store.research_id, 'paper-edit:' + proposal_id))
        except BaseException:
            recover_publication(store)
            raise
        recover_publication(store)
    return proposal
