"""Copied into the sandbox. No PaperAgent imports or credentials here."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path("/workspace")
_SETUP = False


def choose_font(available):
    # Debian's Noto collection exposes its first face as CJK JP in Matplotlib;
    # it still contains Chinese glyphs. Match all regional CJK faces.
    preferred = ("Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC", "WenQuanYi Zen Hei",
                 "PingFang SC", "Heiti SC", "Microsoft YaHei", "SimHei", "Noto Sans SC", "Source Han Sans SC")
    return next((name for name in preferred if name in available),
                next((name for name in sorted(available) if name.startswith("Noto Sans CJK ")), "DejaVu Sans"))


def setup(root="/workspace"):
    global ROOT, _SETUP
    if _SETUP and ROOT == Path(root):
        return
    ROOT = Path(root)
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    import sys
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "code"))
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import fontManager
    available = {font.name for font in fontManager.ttflist}
    font = choose_font(available)
    matplotlib.rcParams["font.sans-serif"] = [font]
    matplotlib.rcParams["axes.unicode_minus"] = False
    plt.show = lambda *args, **kwargs: None
    _SETUP = True


def run_cell(code, run_id, namespace, root="/workspace"):
    """Execute and flush figures in one interpreter request, including on errors."""
    import ast
    import linecache
    setup(root)
    filename = f"<paperagent-{run_id}>"
    linecache.cache[filename] = (len(code), None, code.splitlines(keepends=True), filename)
    try:
        tree = ast.parse(code, filename=filename)
        last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        exec(compile(tree, filename, "exec"), namespace, namespace)
        if last is not None:
            return eval(compile(ast.Expression(last.value), filename, "eval"), namespace, namespace)
    finally:
        save_figures(run_id)


def save_figures(run_id):
    import matplotlib.pyplot as plt
    directory = ROOT / "outputs" / "figures" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    for index, number in enumerate(plt.get_fignums(), 1):
        plt.figure(number).savefig(directory / f"figure_{index}.png", dpi=200, bbox_inches="tight")
    plt.close("all")
