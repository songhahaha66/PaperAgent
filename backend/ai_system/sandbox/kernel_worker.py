"""
持久 Python 内核的子进程入口（由 kernel.py 启动，不要在 API 进程里 import）

协议：stdin 每行一个 JSON 请求 {"id", "code", "artifacts_dir"}，
      协议通道每行一个 JSON 响应 {"id", "ok", "stdout", "error", "figures"}。
同一进程内的多次请求共享全局变量，数据只需加载一次。
"""

import contextlib
import io
import json
import os
import sys
import traceback

# 协议通道单独占一个 fd；fd 1 改指向 stderr，防止 C 扩展或子进程的输出混进协议
_proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", buffering=0), encoding="utf-8", write_through=True)

MAX_OUTPUT_CHARS = 20000

os.environ.setdefault("MPLBACKEND", "Agg")


def _setup_matplotlib():
    try:
        import matplotlib
        import matplotlib.pyplot as plt
        from matplotlib.font_manager import fontManager
    except ImportError:
        return
    candidates = [
        "PingFang SC", "Heiti SC", "Microsoft YaHei", "SimHei",
        "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
    ]
    available = {f.name for f in fontManager.ttflist}
    chosen = next((f for f in candidates if f in available), None)
    if chosen is None:
        chosen = next(
            (f.name for f in fontManager.ttflist if any(k in f.name.lower() for k in ("cjk", "hei", "song", "yahei"))),
            None,
        )
    if chosen:
        matplotlib.rcParams["font.sans-serif"] = [chosen] + list(matplotlib.rcParams["font.sans-serif"])
    matplotlib.rcParams["axes.unicode_minus"] = False
    plt.show = lambda *args, **kwargs: None


def _save_figures(artifacts_dir: str, start_index: int):
    """把本次执行后仍打开的图保存为 PNG 并关闭，返回相对 cwd 的路径"""
    if "matplotlib.pyplot" not in sys.modules:
        return []
    import matplotlib.pyplot as plt

    saved = []
    os.makedirs(artifacts_dir, exist_ok=True)
    for offset, num in enumerate(plt.get_fignums()):
        path = os.path.join(artifacts_dir, f"figure_{start_index + offset + 1}.png")
        plt.figure(num).savefig(path, dpi=200, bbox_inches="tight")
        saved.append(path)
    plt.close("all")
    return saved


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n...[输出过长，省略 {len(text) - MAX_OUTPUT_CHARS} 字符]...\n{text[-half:]}"


def main():
    _setup_matplotlib()
    namespace = {"__name__": "__main__"}
    figure_count = 0

    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        buffer = io.StringIO()
        error = None
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            try:
                exec(compile(request["code"], "<cell>", "exec"), namespace)
            except BaseException as exc:
                # 去掉 worker 自身的 exec 帧，只保留用户代码的回溯
                frames = traceback.extract_tb(exc.__traceback__)[1:]
                error = (
                    "Traceback (most recent call last):\n"
                    + "".join(traceback.format_list(frames))
                    + "".join(traceback.format_exception_only(type(exc), exc))
                )
        figures = []
        try:
            figures = _save_figures(request.get("artifacts_dir", "."), figure_count)
            figure_count += len(figures)
        except Exception as exc:
            error = (error or "") + f"\n保存图片失败: {exc}"
        _proto.write(json.dumps({
            "id": request.get("id"),
            "ok": error is None,
            "stdout": _truncate(buffer.getvalue()),
            "error": _truncate(error) if error else None,
            "figures": figures,
        }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
