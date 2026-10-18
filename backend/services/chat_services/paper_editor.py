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
from ai_system.research.models import ResearchConflict
from ai_system.research.control import refresh_registry
from ai_system.export.latex import compile_pdf, fragment_issues
from ai_system.export import export_docx


def current_store(root):
    thread = json.loads((Path(root) / '.system/graph_thread.json').read_text())
    if thread.get('engine_version') != 'research-v3':
        raise ValueError('当前研究不支持章节修改')
    return ResearchStore(root, thread['thread_id'])


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
        if native and shutil.which('pandoc'):
            process = await asyncio.create_subprocess_exec('pandoc', '--sandbox', '-f', 'latex', '-t', 'gfm+tex_math_dollars', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, _ = await asyncio.wait_for(process.communicate(text.encode()), timeout=15)
                if process.returncode == 0:
                    text = out.decode()
            except TimeoutError:
                process.kill()
                await process.wait()
        readable[key] = text
    with store._connect() as db:
        history = [json.loads(row[0]) for row in db.execute(
            "SELECT response FROM commands WHERE research_id=? AND id LIKE 'paper-edit:%' ORDER BY rowid DESC LIMIT 30",
            (store.research_id,))]
    return {'research_id': store.research_id, 'commit_seq': snapshot['commit_seq'],
            'sections': [{'key': k, 'title': titles.get(k, k), 'readable': readable[k], **v} for k, v in snapshot['sections'].items()],
            'history': history, 'tasks': [{'id': k, 'title': v['objective'], 'dependencies': v['dependencies']}
                                        for k, v in snapshot['tasks'].items() if not v['retired']]}


def guard_wording(before, after, latex):
    if not after.strip() or len(after) > 100000:
        raise ValueError('修改后的章节不能为空或超过 100000 字符')
    # Wording edits cannot silently change evidence, numeric values or equations.
    from ai_system.graph.registry import MATH_PATTERN
    signature = lambda s: (Counter(REF_PATTERN.findall(s)), Counter(re.findall(r'\d+(?:\.\d+)?', s)), MATH_PATTERN.findall(s))
    if signature(before) != signature(after):
        raise ValueError('修改改变了数字、公式或证据引用，请使用“研究修改”进行局部重算')
    if latex and fragment_issues(after):
        raise ValueError('；'.join(fragment_issues(after)))


async def propose(store, key, instruction, content=None, llm=None):
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
        content = await call_text(llm, as_messages(writing_style(ctx),
            '仅润色下面这个章节，保持全部研究事实、结论强度、数字、公式和引用不变。不得根据指令修改方法或数据。只返回完整章节正文。\n'
            + '修改要求：' + instruction + '\n当前正文：\n' + before))
    content = publication_text(content)
    guard_wording(before, content, filename.endswith('.tex'))
    proposal = {'id': uuid.uuid4().hex, 'section': key, 'instruction': instruction, 'before': before, 'after': content,
                'base_commit': snapshot['commit_seq'], 'source_hash': digest(source), 'filename': filename,
                'status': 'pending', 'section_revision': section['revision']}
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
    state['sections'][proposal['section']] = proposal['after']
    ctx = GraphContext.create(str(store.root), planner_llm=None, output_mode=output_mode)
    ctx.research = store
    refresh_registry(ctx)
    native = filename.endswith('.tex')
    rendered = publication_text(ctx.registry.render(assemble_latex(state) if native else assemble(state), latex=native))
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
        with store._transaction() as db:
            latest = json.loads(db.execute('SELECT response FROM commands WHERE research_id=? AND id=?', (store.research_id, 'paper-edit:' + proposal_id)).fetchone()[0])
            if latest['status'] != 'pending':
                return latest
            current = store._read(db)
            _, active = source_info(store, current)
            if current['commit_seq'] != proposal['base_commit'] or digest(active) != proposal['source_hash']:
                raise ResearchConflict('编译期间论文发生变化，未覆盖最新版本，请刷新后重试')
            # Persist both sides in the existing version journal and paper archive.
            archive = store.root / '.system/research' / store.research_id / 'papers'
            archive.mkdir(parents=True, exist_ok=True)
            old_name = f'{current["commit_seq"]}-before-edit{Path(filename).suffix}'
            (archive / old_name).write_text(source, encoding='utf-8')
            version = archive / f'{current["commit_seq"] + 1}{Path(filename).suffix}'
            version.write_text(rendered, encoding='utf-8')
            section = current['sections'][proposal['section']]
            section.update(text=proposal['after'], content_hash=digest(proposal['after']), revision=section['revision'] + 1)
            current.update(paper_hash=digest(rendered), paper_version=version.relative_to(store.root).as_posix(), paper_conflict=False,
                           exports=[filename] + exports)
            for name in [filename] + exports:
                target = store.root / name
                temporary = target.with_suffix(target.suffix + '.edit-tmp')
                shutil.copyfile(staging / name, temporary)
                temporary.replace(target)
            proposal.update(status='applied', applied_revision=section['revision'])
            store._save(db, current, 'section_edit_applied', {'proposal': proposal, 'versions': {proposal['section']: section}})
            db.execute('UPDATE commands SET response=? WHERE research_id=? AND id=?',
                       (json.dumps(proposal, ensure_ascii=False), store.research_id, 'paper-edit:' + proposal_id))
    return proposal
