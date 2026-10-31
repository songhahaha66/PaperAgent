"""Execution tools used by the single research controller."""
from ai_system.modules.registry import CapabilityProduct, Inputs
from pydantic import Field


async def inspect_materials(ctx, inputs):
    from .analysis import profile_data
    result={'inputs':ctx.research.file_inputs(),'profile':await profile_data(ctx)}
    with ctx.research._transaction() as db:
        state=ctx.research._read(db)
        if state['materials']!=result:
            state['materials']=result;state['revision']+=1
            ctx.research._save(db,state,'materials_profiled',result)
    return CapabilityProduct('research_state',{**result,'revision':state['revision']})


class CodeInput(Inputs):
    code: str = Field(min_length=1)
    title: str = ''
    question_id: str | None = Field(default=None, description='可选，使用已建立问题的真实编号；公共资料分析可省略。不得借用其他问题编号。')


async def run_code(ctx, inputs):
    """Execute in the main kernel and return its receipt, without experiment commits."""
    from .execution import build_tools, export_solution_code, is_placeholder, strip_template_tokens
    from ai_system.research.runtime import execution_links, execution_scope
    from ai_system.research.runs import direct_runs, run_result
    store = ctx.research
    qid = inputs['question_id']
    if qid and (qid not in store.snapshot()['questions'] or store.snapshot()['questions'][qid].get('retired')):
        raise ValueError('问题编号不存在；先用research_create_question新建问题，或省略question_id做公共计算。不能使用其他问题的编号。')
    code = strip_template_tokens(inputs['code'])
    if is_placeholder(code):
        return CapabilityProduct('research_state', {'observation': '代码没有实际工作；无需占位执行。'},
                                 'failed', '代码仅包含占位语句，未执行计算。')
    for rid, info in direct_runs(store, ctx.fs).items():
        if (info.get('failure_kind') == 'transport_unknown' or info.get('status') in {'starting','running'}) and ctx.fs.read_text(info['input']) == code:
            result = {**run_result(ctx, rid), 'recovered': True,
                      'note': '相同代码已提交但完成状态未知，未再次执行；先核实已有文件和日志。'}
            return CapabilityProduct('research_state', result, 'interrupted', result['note'])
    key = execution_links().get('action_id')
    def bind_run(run_id):
        info = ctx.fs.get_run_info(run_id)
        info.update(research_id=store.research_id, scope='research', question_id=qid,
                    tool_call_id=key, title=inputs['title'])
        ctx.fs._save_manifest()
    runs = []
    try:
        with execution_scope(question_id=qid):
            tool = next(t for t in build_tools(ctx, 'research', runs, kernel_key='research', on_run=bind_run) if t.name == 'run_python')
            observation = await tool.ainvoke({'code': code, 'title': inputs['title']})
    finally:
        legacy={rid for attempt in store.snapshot()['executions'].values() if attempt.get('workspace') == '.'
                for rid in attempt.get('run_ids', [])}
        owned=set(direct_runs(store, ctx.fs)) | legacy
        saved = [{'run_id': rid, 'ok': info.get('status') == 'success', 'code': ctx.fs.read_text(info['input'])}
                 for rid, info in ctx.fs._manifest.get('runs', {}).items()
                 if rid in owned and info.get('input') and ctx.fs.exists(info['input'])]
        export_solution_code(ctx, 'research', saved)
    actual = runs[-1] if runs else None
    if actual is None:
        return CapabilityProduct('research_state', {'observation': observation})
    result = run_result(ctx, actual['run_id'], observation)
    status = 'completed' if result['execution_status'] == 'succeeded' else 'interrupted' if result['execution_status'] == 'cancelled' else result['execution_status']
    return CapabilityProduct('research_state', result, status, actual.get('delivery_error') or result['error'])


def register(registry):
    registry.register('experiments','research.run_python','research_state',run_code,CodeInput,
        model_callable=True,purpose='直接在研究主循环的持久Python内核计算，返回实际输出与结果文件；无需另建计划、子Agent或申请尝试次数。')
    registry.register('experiments','experiments.inspect','research_state',inspect_materials,
                      model_callable=True, purpose='用确定性代码查看当前已提供材料，返回inputs与profile；概览保留原始文件路径与结构，不代替后续计算。')
