"""Write the active TeX files through the existing versioned change mechanism."""
import json
from pydantic import BaseModel, Field
from ai_system.graph.llm_utils import as_messages, call_structured
from ai_system.graph.registry import publication_text
from ai_system.modules.writing.sections import STYLE
from ai_system.research.models import ResearchConflict
from services.chat_services import paper_project as project


class SourceEdit(BaseModel):
    target: str = Field(description='系统提供的源码片段或文件标记')
    replacement: str


class SourceEdits(BaseModel):
    edits: list[SourceEdit] = Field(default_factory=list, max_length=24)
    research_gap: str = Field(default='', description='所需结果不存在、取值或适用范围不明确时说明具体缺项，禁止猜值；措辞/排版/编译自行修复')


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
    from ai_system.research.views import questions,question_context
    snapshot=store.snapshot()
    research_questions=question_context(questions(snapshot))
    from ai_system.research.views import execution_context
    records=execution_context(store)
    evidence=json.dumps(records,ensure_ascii=False)
    prompt = ('直接更新当前 LaTeX 项目。返回 edits，每项 target、replacement；'
              'target 只选下方提供的片段标记，replacement 是该片段修改后的完整内容，无需复制 old 或计算位置。'
              '优先修改片段；需调整整体结构时可选择文件标记。同次修改的范围不能重复或重叠。'
              '只改实际受影响部分，保留人工编辑、宏、环境和与相邻片段的换行分隔。'
              '正文直接使用原生 LaTeX。可按研究成果与用户模板组织、增删或调整结构；旧数据库章节不是约束。'
              '使用当前导言区已定义的命令；交叉引用默认用原生 \\ref、\\eqref，不使用未加载宏包的 \\cref 等命令。'
              '依据实际输出和结果文件直接写数值、单位和适用条件，不创建R/F编号。'
              '插图用已提供的结果文件路径与原生 \\includegraphics、\\caption、\\label，交叉引用用 \\ref。'
              '文献使用已核实 {{C:Cn}}；系统在写入前解析。'
              '数值按数据精度和用途合理取舍，通常保留2至4位有效数字；完整精度保留在结果文件，不把长浮点数直接堆入正文。'
              '区分用户给定样本与实测数据，未提供采集过程时不得声称已实测。过程日志、等待秒数等验收细节不写入学术正文。'
              '不得编造结果、来源、引用键或实验。没有需要修改时返回空 edits。\n'
              '问题判断为空时如实说明尚无判断；实验结果可用不等于研究问题已回答。'
              '保留问题答案的局限和重要未知，主要请求已有可靠答案即可交付，不要求软意见归零。'
              '可讨论历史负结果和撤回观点，但须明确未支持/已撤回及理由，不能当当前事实。'
              '所需结果缺失或含义、适用范围不能明确时，返回research_gap说明具体结果和缺项且edits为空，等待补充，不得猜值。'
              '文字排版和编译问题自行处理；缺文档结尾可能意味着正文截断，须核对全文补足缺失内容，不得只补结束标记。'
              '替换文件或最后一个片段时保留完整文档结束标记；文件名中的下划线用\\_或\\detokenize保护。'
              '首次成稿应填充或删除空白占位；不能留下只有标题、没有内容的摘要页。\n'
              '# 原要求\n'+state.get('task', '')+'\n# 用户模板\n'+(ctx.template_requirements or '')+
              '\n# 研究大纲（可按表达需要调整）\n'+json.dumps(state.get('outline', []), ensure_ascii=False)+
              '\n# 研究问题、已有判断与未解决部分\n'+json.dumps(research_questions, ensure_ascii=False)+
              '\n# 实验记录与结果（每份报告只列一次；历史提供定位）\n'+evidence+
              '\n# 当前项目源码片段（唯一当前正文）\n'+json.dumps(project.source_prompt(state), ensure_ascii=False))
    from ai_system.research.literature import literature_context
    prompt += literature_context(store.root, state.get('task','')+'\n'+evidence)
    if ctx.compact_paper:
        prompt += '\n# 篇幅要求\n用户选择简短报告：全文约600至1000字，覆盖问题、假设与方法、主要结果和局限，表格可合并重复指标。沿用已有结果，不添加实验；无需单独摘要封面，避免空白页和重复标题。'
    if repairing:
        prompt += '\n# 本版本待修复意见\n'+json.dumps(state.get('issues') or [], ensure_ascii=False)
        compilation=project.meta(store.root).get('compile',{})
        if compilation.get('source_version')==state['source_version'] and not compilation.get('success'):
            prompt += '\n# 当前版本编译反馈\n'+json.dumps(compilation,ensure_ascii=False)
    await ctx.set_stage('validate' if repairing else 'write', 'in_progress', '修改当前 LaTeX 源码')
    from ai_system.research.store import digest
    signature = digest({'source':state['source_version'], 'issues':state.get('issues') or []})
    compilation = project.meta(store.root).get('compile', {})
    if repairing and not state.get('issues') and compilation.get('success') and compilation.get('source_version') == state['source_version']:
        # A source edit may already have repaired the failure while the research
        # was paused. Resume validation instead of asking a model to rewrite it.
        return {'repair_input': signature, 'repair_round': state.get('repair_round', 0),
                'sections': {}, 'log': ['当前源码已编译通过，无待修复意见，继续交付检查']}
    publication=store.snapshot().get('publication') or {}
    history=publication.get('repair_inputs',[])
    if repairing and len(history[publication.get('repair_resume_offset',0):]) >= 2:
        reason='；'.join(i['message'] for i in state.get('issues') or [])
        message='论文修复未完成：已尝试两次自动修复。'+reason+'。源码、修改历史和编译日志已保留；继续将从写作恢复。'
        await ctx.set_stage('validate','blocked',message)
        return {'research_blocked':True, 'message':message, 'log':[message]}

    def patches_for(answer):
        if len({edit.target for edit in answer.edits}) != len(answer.edits):
            raise ValueError('一次修改不能重复选择同一片段')
        patches = []
        for edit in answer.edits:
            from ai_system.export.latex import result_reference_issues
            if problems:=result_reference_issues(edit.replacement):
                raise ValueError('；'.join(problems))
            if edit.target not in targets:
                raise ValueError('target 不属于当前源码版本，请从提供的片段或文件标记中选择')
            selected = targets[edit.target]
            if not edit.replacement.strip() and edit.target == selected['file']:
                raise ValueError('不能清空论文文件')
            patches.append({**selected, 'replacement':edit.replacement})
        if patches:project.validate_patches(store.root, patches)
        return patches
    def validate(answer):
        if answer.research_gap and answer.edits:raise ValueError('报告依据缺项时不得同时改写正文')
        patches_for(answer)
    answer = await call_structured(ctx.writer_llm, SourceEdits, as_messages(STYLE+'\n输出 SourceEdits JSON；replacement 使用原生 LaTeX，可修改章节结构、导言区和宏，保持现有项目与用户模板一致。', prompt), validate=validate)
    if answer.research_gap:
        message='论文写作缺少可用依据：'+answer.research_gap+'。源码和已有实验结果已保留，补充后从写作继续。'
        return {'research_blocked':True,'message':message,'log':[message]}
    patches = []
    from ai_system.research.citations import render_citations
    for edit,patch in zip(answer.edits,patches_for(answer)):
        rendered = publication_text(render_citations(store.root, ctx.registry.render(patch['replacement'], latex=True), True))
        patches.append({**patch, 'replacement':rendered})
    if project.snapshot(store.root)['version'] != state['source_version']:
        raise ResearchConflict('写作期间项目已更新，未覆盖新源码；请基于当前版本继续')
    change = project.propose_change(store, None, None, patches) if patches else None
    if change:
        from ai_system.research.views import activity
        with activity(ctx,'writing.save_source') as operation:
            await project.decide(store, change['id'], 'apply', compile_after=False, expected_version=state['source_version'])
            operation['output']={'files':sorted({p['file'] for p in patches})}
    await ctx.events.file_changed('paper.tex')
    await ctx.set_stage('write' if not repairing else 'validate', 'completed' if not repairing else 'in_progress', '源码已更新，等待引用与编译检查')
    result = {'log':['LaTeX 项目源码已更新'], 'sections':{}}
    if repairing:result.update(repair_input=signature, repair_round=state.get('repair_round', 0)+1)
    return result


async def write(state, runtime):
    return await _update(state, runtime)


async def repair(state, runtime):
    return await _update(state, runtime, repairing=True)
