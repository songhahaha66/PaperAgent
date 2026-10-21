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
    target: str = Field(description='系统提供的源码片段或文件标记')
    replacement: str


class SourceEdits(BaseModel):
    edits: list[SourceEdit] = Field(default_factory=list, max_length=24)
    research_gap: str = Field(default='', description='只有缺少会改变问题答案的实质证据时填写，措辞/排版/编译自行修复')


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
    targets = project.source_targets(state)
    evidence = '\n\n'.join(subproblem_brief(state, ctx, sp) for sp in state['analysis']['subproblems'])
    from ai_system.research.views import questions, question_context
    research_questions = question_context(questions(store.snapshot()),include_history=True)
    from ai_system.research.planner import research_history_context
    history=research_history_context(store.snapshot(),full=True)
    prompt = ('直接更新当前 LaTeX 项目。返回 edits，每项 target、replacement；'
              'target 只选下方提供的片段标记，replacement 是该片段修改后的完整内容，无需复制 old 或计算位置。'
              '优先修改片段；需调整整体结构时可选择文件标记。同次修改的范围不能重复或重叠。'
              '只改实际受影响部分，保留人工编辑、宏、环境和与相邻片段的换行分隔。'
              '正文直接使用原生 LaTeX。可按研究成果与用户模板组织、增删或调整结构；旧数据库章节不是约束。'
              '使用当前导言区已定义的命令；交叉引用默认用原生 \\ref、\\eqref，不使用未加载宏包的 \\cref 等命令。'
              '数值与图使用给定的 {{R:Rn}} / {{F:Fn}}，文献使用已核实 {{C:Cn}}；系统在写入前解析。'
              '不得编造结果、来源、引用键或实验。没有需要修改时返回空 edits。\n'
              '问题判断为空时如实说明尚无判断；实验结果可用不等于研究问题已回答。'
              '保留问题答案的局限和重要未知，主要请求已有可靠答案即可交付，不要求软意见归零。'
              '可讨论历史负结果和撤回观点，但须明确未支持/已撤回及理由，不能当当前事实。'
              '只有实质证据缺口才返回research_gap且edits为空，交回研究层；文字排版和编译问题自行处理。\n'
              '# 原要求\n'+state.get('task', '')+'\n# 用户模板\n'+(ctx.template_requirements or '')+
              '\n# 研究大纲（可按表达需要调整）\n'+json.dumps(state.get('outline', []), ensure_ascii=False)+
              '\n# 研究问题、已有判断与未解决部分\n'+json.dumps(research_questions, ensure_ascii=False)+
              '\n# 必要研究历史与负结果（单独标注状态，不能混入当前事实）\n'+json.dumps(history,ensure_ascii=False)+
              '\n# 完整实验研究记录\n'+json.dumps(state.get('solutions',{}),ensure_ascii=False)+
              '\n# 实际研究成果\n'+evidence+'\n'+ctx.registry.to_prompt_table()+'\n'+consistency_text(ctx, state.get('consistency') or [])+
              '\n# 当前项目源码片段（唯一当前正文）\n'+json.dumps(project.source_prompt(state), ensure_ascii=False))
    from ai_system.research.literature import literature_context
    prompt += literature_context(store.root, state.get('task','')+'\n'+evidence)
    if repairing:
        prompt += '\n# 本版本待修复意见\n'+json.dumps(state.get('issues') or [], ensure_ascii=False)
    await ctx.set_stage('validate' if repairing else 'write', 'in_progress', '修改当前 LaTeX 源码')
    from ai_system.research.store import digest
    signature = digest({'source':state['source_version'], 'issues':state.get('issues') or []})
    if repairing and (store.snapshot().get('publication') or {}).get('repair_inputs', []).count(signature) >= 3:
        return {'research_blocked':True, 'message':'论文修订未完成：同一版本的意见反复修复未成功，源码与修改历史已保留。',
                'log':['同一源码与意见未能修复，源码与修改历史已保留']}

    def patches_for(answer):
        if len({edit.target for edit in answer.edits}) != len(answer.edits):
            raise ValueError('一次修改不能重复选择同一片段')
        patches = []
        for edit in answer.edits:
            if edit.target not in targets:
                raise ValueError('target 不属于当前源码版本，请从提供的片段或文件标记中选择')
            selected = targets[edit.target]
            if not edit.replacement.strip() and edit.target == selected['file']:
                raise ValueError('不能清空论文文件')
            patches.append({**selected, 'replacement':edit.replacement})
        if patches:project.validate_patches(store.root, patches)
        return patches
    def validate(answer):
        if answer.research_gap and answer.edits:raise ValueError('证据缺口交回研究层时不得同时改写正文')
        patches_for(answer)
    answer = await call_structured(ctx.writer_llm, SourceEdits, as_messages(STYLE+'\n输出 SourceEdits JSON；replacement 使用原生 LaTeX，可修改章节结构、导言区和宏，保持现有项目与用户模板一致。', prompt), validate=validate)
    if answer.research_gap:
        return {'research_restart':True,'research_gap':answer.research_gap,'log':['写作发现实质证据缺口：'+answer.research_gap]}
    patches = []
    from ai_system.research.citations import render_citations
    for patch in patches_for(answer):
        refs = find_refs(patch['replacement'])
        rendered = publication_text(render_citations(store.root, ctx.registry.render(patch['replacement'], latex=True), True))
        if refs:
            rendered += '\n% paperagent:evidence '+','.join(sorted(set(refs)))+'\n'
        patches.append({**patch, 'replacement':rendered})
    if project.snapshot(store.root)['version'] != state['source_version']:
        raise ResearchConflict('写作期间项目已更新，未覆盖新源码；请基于当前版本继续')
    change = project.propose_change(store, None, None, patches) if patches else None
    if change:
        await project.decide(store, change['id'], 'apply', compile_after=False, expected_version=state['source_version'])
    await ctx.events.file_changed('paper.tex')
    await ctx.set_stage('write' if not repairing else 'validate', 'completed' if not repairing else 'in_progress', '源码已更新，等待引用与编译检查')
    result = {'log':['LaTeX 项目源码已更新'], 'sections':{}}
    if repairing:result.update(repair_input=signature, repair_round=state.get('repair_round', 0)+1)
    return result


async def write(state, runtime):
    return await _update(state, runtime)


async def repair(state, runtime):
    return await _update(state, runtime, repairing=True)
