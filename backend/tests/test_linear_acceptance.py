"""Necessary numerical checks run in the calculation, without a metric ledger."""
import asyncio
import pytest
from ai_system.graph.context import GraphContext
from ai_system.modules.experiments.execution import build_tools
pytestmark=pytest.mark.usefixtures('offline_kernel')


@pytest.mark.parametrize('x,y,objective,ok',[(3,2,13,True),(-1,2,1,False),(2.5,2,11.5,False),(3,2,99,False)])
def test_model_checks_execute_against_decisions_and_original_constraints(tmp_path,x,y,objective,ok):
    ctx=GraphContext.create(str(tmp_path),planner_llm=None)
    code=f'''import math
x,y,objective={x!r},{y!r},{objective!r}
assert math.isfinite(x) and math.isfinite(y)
assert x >= 0 and y >= 0
assert x == round(x) and y == round(y)
assert 2*x+y <= 8 and x+2*y <= 8
assert abs(3*x+2*y-objective) < 1e-8
print('constraints checked',x,y,objective)
'''
    runs=[]
    async def run():
        try:return await next(t for t in build_tools(ctx,'Q1',runs) if t.name=='run_python').ainvoke({'code':code})
        finally:await ctx.kernels.close()
    output=asyncio.run(run())
    assert runs[0]['ok']==ok
    assert ('constraints checked' in output) if ok else ('AssertionError' in output)
    assert not ctx.registry.results
