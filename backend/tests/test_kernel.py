import asyncio
import json
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_system.sandbox import PythonKernel


def _run(coro):
    return asyncio.run(coro)


def _manifest(ws: Path) -> dict:
    return json.loads((ws / "manifest.json").read_text(encoding="utf-8"))


def test_variables_persist_between_cells(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            first = await kernel.execute("x = 21\nprint('set')")
            second = await kernel.execute("print(x * 2)")
            return first, second

    first, second = _run(go())
    assert first.ok and first.stdout.strip() == "set"
    assert second.ok and second.stdout.strip() == "42"
    runs = _manifest(tmp_path)["runs"]
    assert runs[first.run_id]["status"] == "success"
    assert (tmp_path / "runs" / second.run_id / "input.py").read_text(encoding="utf-8") == "print(x * 2)"
    assert (tmp_path / "runs" / second.run_id / "stdout.log").read_text(encoding="utf-8").strip() == "42"


def test_error_keeps_user_traceback_and_kernel_alive(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            await kernel.execute("y = 1")
            failed = await kernel.execute("print('before')\n1 / 0")
            after = await kernel.execute("print(y)")
            return failed, after

    failed, after = _run(go())
    assert not failed.ok
    assert "before" in failed.stdout
    assert "ZeroDivisionError" in failed.error
    assert "kernel_worker" not in failed.error
    assert after.ok and after.stdout.strip() == "1"
    assert _manifest(tmp_path)["runs"][failed.run_id]["status"] == "error"


def test_figures_are_saved_as_run_artifacts(tmp_path):
    code = "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.title('测试')\nplt.show()"

    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            return await kernel.execute(code)

    result = _run(go())
    assert result.ok, result.error
    assert result.figures == [f"runs/{result.run_id}/artifacts/figure_1.png"]
    assert (tmp_path / result.figures[0]).stat().st_size > 0
    assert _manifest(tmp_path)["runs"][result.run_id]["artifacts"] == result.figures
    assert "已保存图片" in result.to_observation()


def test_intermediate_handoff_directory_exists_in_a_fresh_worker(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            return await kernel.execute("from pathlib import Path\nPath('outputs/intermediate/values.csv').write_text('value\\n42\\n')\nprint('saved')")
    result=_run(go())
    assert result.ok and (tmp_path/'outputs/intermediate/values.csv').read_text()=='value\n42\n'


def test_figure_saving_restores_available_cjk_font_after_model_style_override(tmp_path):
    from matplotlib.font_manager import fontManager
    if not any(any(k in f.name.lower() for k in ('cjk','hei','song','yahei','pingfang','noto sans sc','source han')) for f in fontManager.ttflist):
        pytest.skip('No system CJK font installed')
    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            image=await kernel.execute("import matplotlib,matplotlib.pyplot as plt\nmatplotlib.rcParams['font.sans-serif']=['DejaVu Sans']\nplt.plot([1,2])\nplt.title('中文销量预测')")
            config=await kernel.execute("print(matplotlib.rcParams['font.sans-serif'][0])")
            return image,config
    image,config=_run(go())
    assert image.ok and image.figures
    assert config.stdout.strip()!='DejaVu Sans'


def test_timeout_restarts_kernel(tmp_path):
    async def go():
        async with PythonKernel(str(tmp_path), timeout=1) as kernel:
            await kernel.execute("z = 5")
            slow = await kernel.execute("import time\ntime.sleep(10)")
            fresh = await kernel.execute("print('z' in globals())")
            return slow, fresh

    slow, fresh = _run(go())
    assert slow.timed_out and not slow.ok
    assert "变量都已丢失" in slow.to_observation()
    assert fresh.ok and fresh.stdout.strip() == "False"


def test_low_level_stdout_does_not_break_protocol(tmp_path):
    code = "import os\nos.write(1, b'raw bytes\\n')\nprint('visible')"

    async def go():
        async with PythonKernel(str(tmp_path)) as kernel:
            return await kernel.execute(code)

    result = _run(go())
    assert result.ok, result.error
    assert result.stdout.strip() == "visible"


def test_kernels_sharing_fs_do_not_lose_runs(tmp_path):
    async def go():
        first = PythonKernel(str(tmp_path))
        second = PythonKernel(str(tmp_path), fs=first.fs)
        try:
            results = await asyncio.gather(
                first.execute("print(1)"), second.execute("print(2)"), first.execute("print(3)")
            )
        finally:
            await first.close()
            await second.close()
        return results

    results = _run(go())
    runs = _manifest(tmp_path)["runs"]
    assert {r.run_id for r in results} <= set(runs)
    assert all(runs[r.run_id]["status"] == "success" for r in results)
