"""Execution tools used by the single research controller."""
from types import SimpleNamespace
from ai_system.modules.registry import CapabilityProduct, BranchInputs


async def compare(ctx, inputs):
    from .supervised import compare
    return CapabilityProduct("experiment", await compare(ctx, inputs["branch"]))


async def execute(ctx, inputs):
    from ai_system.research.control import execute
    return CapabilityProduct("research_state", await execute(inputs["branch"], SimpleNamespace(context=ctx)))


async def inspect_materials(ctx, inputs):
    from .analysis import profile_data
    result={'inputs':ctx.research.file_inputs(),'profile':await profile_data(ctx)}
    with ctx.research._transaction() as db:
        state=ctx.research._read(db)
        if state['materials']!=result:
            state['materials']=result;state['revision']+=1
            ctx.research._save(db,state,'materials_profiled',result)
    return CapabilityProduct('research_state',{**result,'revision':state['revision']})


def register(registry):
    registry.register('experiments','experiments.inspect','research_state',inspect_materials,
                      model_callable=True, purpose='用确定性代码查看当前已提供材料，返回inputs与profile；概览不是本研究已验收数值成果。')
    for name, product, handler in (("compare", "experiment", compare), ("execute", "research_state", execute)):
        registry.register("experiments", f"experiments.{name}", product, handler, BranchInputs)
