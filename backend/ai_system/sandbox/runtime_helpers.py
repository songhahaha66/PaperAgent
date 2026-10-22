"""Copied into the sandbox. No PaperAgent imports or credentials here."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path("/workspace")


def choose_font(available):
    # Debian's Noto collection exposes its first face as CJK JP in Matplotlib;
    # it still contains Chinese glyphs. Match all regional CJK faces.
    preferred = ("Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC", "WenQuanYi Zen Hei",
                 "PingFang SC", "Heiti SC", "Microsoft YaHei", "SimHei", "Noto Sans SC", "Source Han Sans SC")
    return next((name for name in preferred if name in available),
                next((name for name in sorted(available) if name.startswith("Noto Sans CJK ")), "DejaVu Sans"))


def setup(root="/workspace"):
    global ROOT
    ROOT = Path(root)
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    import sys
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


def save_figures(run_id):
    import matplotlib.pyplot as plt
    directory = ROOT / "outputs" / "figures" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    for index, number in enumerate(plt.get_fignums(), 1):
        plt.figure(number).savefig(directory / f"figure_{index}.png", dpi=200, bbox_inches="tight")
    plt.close("all")


def emit_result(name, value, unit="", description="", meaning=None):
    """Optional structured output. Provenance is attached by the host after success."""
    if hasattr(value, 'tolist'):
        value = value.tolist()
    payload = dict(name=name, value=value, unit=unit, description=description, meaning=meaning)
    print('PAPERAGENT_RESULT ' + json.dumps(payload, ensure_ascii=False, allow_nan=False), flush=True)
