"""Write the active TeX files through the existing versioned change mechanism."""
import json
from pydantic import BaseModel, Field
from ai_system.graph.llm_utils import as_messages, call_structured
from ai_system.graph.registry import find_refs, publication_text
from ai_system.modules.writing.sections import STYLE, subproblem_brief
from ai_system.modules.writing.consistency import consistency_text
from ai_system.research.models import ResearchConflict
from services.chat_services import paper_project as project


class SourceEdit(BaseModel):
    file: str
    old: str
    replacement: str


class SourceEdits(BaseModel):
    edits: list[SourceEdit] = Field(max_length=24)


async def _update(state, runtime, *, repairing=False):
    ctx = runtime.context
    store = ctx.research
    project.initialize(store)
    from services.chat_services.paper_sources import write_bibliography
    write_bibliography(store.root)
    with store._transaction():
        path = store.root/'paper.tex'
        if not path.exists() or not project.read_utf8(path).strip():
            from ai_system.export.latex import native_document
            project.atomic_text(path, native_document(state['analysis']['title'], '', ''))
        data = project.meta(store.root)
        data['main'] = 'paper.tex'
        project.atomic_text(store.root/project.META, json.dumps(data, ensure_ascii=False))
        state = project.source_state(store, state)
    current = {r['path']:r['text'] for r in state['source_files'] if r['text'] is not None}
    evidence = '\n\n'.join(subproblem_brief(state, ctx, sp) for sp in state['analysis']['subproblems'])
    from ai_system.research.views import questions, question_context
    research_questions = question_context(questions(store.snapshot()))
    prompt = ('直接更新当前 LaTeX 项目。返回文件局部修改 edits，每项 file、old、replacement；'
              'old 必须是该文件中唯一出现的逐字原文，不能猜选区。只改实际受影响部分，保留人工编辑、宏和版式。'
              '正文直接使用原生 LaTeX。可按研究成果与用户模板组织、增删或调整结构；旧数据库章节不是约束。'
              '数值与图使用给定的 {{R:Rn}} / {{F:Fn}}，文献使用已核实 {{C:Cn}}；系统在写入前解析。'
              '不得编造结果、来源、引用键或实验。没有需要修改时返回空 edits。\n'
              '问题判断为空时如实说明尚无判断；实验结果可用不等于研究问题已回答。'
              '保留问题判断的局限、未解决意见和后续待研究部分，不把缺失判断补写成肯定结论。\n'
              '# 原要求\n'+state.get('task', '')+'\n# 用户模板\n'+(ctx.template_requirements or '')+
              '\n# 研究大纲（可按表达需要调整）\n'+json.dumps(state.get('outline', []), ensure_ascii=False)+
              '\n# 研究问题、已有判断与未解决部分\n'+json.dumps(research_questions, ensure_ascii=False)+
              '\n# 实际研究成果\n'+evidence+'\n'+ctx.registry.to_prompt_table()+'\n'+consistency_text(ctx, state.get('consistency') or [])+
              '\n# 当前项目文件（唯一当前正文）\n'+json.dumps(current, ensure_ascii=False))
    from ai_system.research.literature import literature_context
    prompt += literature_context(store.root, state.get('task','')+'\n'+evidence)
    if repairing:
        prompt += '\n# 本版本待修复意见\n'+json.dumps(state.get('issues') or [], ensure_ascii=False)
    await ctx.set_stage('validate' if repairing else 'write', 'in_progress', '修改当前 LaTeX 源码')
    from ai_system.research.store import digest
    signature = digest({'source':state['source_version'], 'issues':state.get('issues') or []})
    if repairing and (store.snapshot().get('publication') or {}).get('repair_inputs', []).count(signature) >= 3:
        return {'research_blocked':True, 'log':['同一源码与意见未能修复，源码与修改历史已保留']}

    def validate(answer):
        for edit in answer.edits:
            if edit.file not in current or not edit.file.endswith('.tex'):
                raise ValueError('写作只能修改当前项目的 LaTeX 文件')
            if not edit.old or current[edit.file].count(edit.old) != 1:
                raise ValueError('写作选区必须唯一匹配当前文件原文')
            if not edit.replacement.strip() and edit.old == current[edit.file]:
                raise ValueError('不能清空论文文件')
    answer = await call_structured(ctx.writer_llm, SourceEdits, as_messages(STYLE+'\n输出 SourceEdits JSON；replacement 使用原生 LaTeX，可修改章节结构、导言区和宏，保持现有项目与用户模板一致。', prompt), validate=validate)
    patches = []
    from ai_system.research.citations import render_citations
    for edit in answer.edits:
        text = current[edit.file]
        start = text.index(edit.old)
        refs = find_refs(edit.replacement)
        rendered = publication_text(render_citations(store.root, ctx.registry.render(edit.replacement, latex=True), True))
        if refs:
            rendered += '\n% paperagent:evidence '+','.join(sorted(set(refs)))+'\n'
        patches.append({'file':edit.file, 'version':state['source_version'], 'start':start,
                        'end':start+len(edit.old), 'quote':edit.old, 'replacement':rendered})
    if project.snapshot(store.root)['version'] != state['source_version']:
        raise ResearchConflict('写作期间项目已更新，未覆盖新源码；请基于当前版本继续')
    change = project.propose_change(store, None, None, patches) if patches else None
    if change:
        await project.decide(store, change['id'], 'apply', compile_after=False, expected_version=state['source_version'])
    await ctx.events.file_changed('paper.tex')
    await ctx.set_stage('write' if not repairing else 'validate', 'completed' if not repairing else 'in_progress', '源码已更新，等待内容检查')
    result = {'log':['LaTeX 项目源码已更新'], 'sections':{}}
    if repairing:result.update(repair_input=signature, repair_round=state.get('repair_round', 0)+1)
    return result


async def write(state, runtime):
    return await _update(state, runtime)


async def repair(state, runtime):
    return await _update(state, runtime, repairing=True)
