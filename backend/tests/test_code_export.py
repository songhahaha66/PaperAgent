"""An earlier cell's error must not silently remove imports from delivered code."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_system.graph.context import GraphContext
from ai_system.graph.nodes.solve import export_solution_code
from ai_system.sandbox.kernel import PythonKernel


def test_export_retains_completed_prefix_from_failed_cell_and_replays_in_clean_kernel(tmp_path):
    ctx = GraphContext.create(str(tmp_path / "source"), planner_llm=None)
    async def go():
        original = PythonKernel(str(ctx.workspace), fs=ctx.fs)
        replay = PythonKernel(str(tmp_path / "replay"))
        log = []
        try:
            for code in ["import math\nscale = 9\nraise ValueError('known failed attempt')\nnever_executed = 1",
                         "answer = math.sqrt(scale)\nprint('answer', answer)"]:
                result = await original.execute(code)
                log.append({"run_id": result.run_id, "code": code, "ok": result.ok})
            export_solution_code(ctx, "Q1", log)
            code = ctx.fs.read_text("code/Q1_solution.py")
            assert "import math" in code and "scale = 9" in code
            assert "raise ValueError" not in code and "never_executed" not in code
            repeated = await replay.execute(code)
            assert repeated.ok and "answer 3.0" in repeated.stdout
        finally:
            await original.close()
            await replay.close()
    asyncio.run(go())


def test_export_does_not_extract_inside_a_failed_compound_statement(tmp_path):
    ctx = GraphContext.create(str(tmp_path), planner_llm=None)
    rid = ctx.fs.create_run()
    ctx.fs.save_run_stdout(rid, '\nTraceback (most recent call last):\n  File "<cell>", line 4, in <module>\nValueError: bad')
    ctx.fs.finish_run(rid, "error")
    code = "seed = 1\nfor i in range(3):\n    partial = i\n    raise ValueError('bad')\nunreached = 2"
    export_solution_code(ctx, "Q1", [{"run_id": rid, "code": code, "ok": False}])
    exported = ctx.fs.read_text("code/Q1_solution.py")
    assert "seed = 1" in exported and "partial" not in exported and "unreached" not in exported
