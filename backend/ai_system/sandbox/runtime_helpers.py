"""Copied into the sandbox. No PaperAgent imports or credentials here."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path("/workspace")


def choose_font(available):
    # Debian's Noto collection exposes its first face as CJK JP in Matplotlib;
    # it still contains Chinese glyphs. Match all regional CJK faces.
    preferred = ("Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC", "WenQuanYi Zen Hei")
    return next((name for name in preferred if name in available),
                next((name for name in sorted(available) if name.startswith("Noto Sans CJK ")), "DejaVu Sans"))


def setup():
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
    directory = ROOT / "runs" / run_id / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    for index, number in enumerate(plt.get_fignums(), 1):
        plt.figure(number).savefig(directory / f"figure_{index}.png", dpi=200, bbox_inches="tight")
    plt.close("all")


def inventory(max_bytes):
    result = []
    for folder in ("outputs", "code", "runs"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError("输出目录不允许符号链接")
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if folder == "runs" and "artifacts" not in path.parts:
                continue
            size = path.stat().st_size
            if size > max_bytes:
                raise ValueError(f"文件超过传输上限: {path.relative_to(ROOT)}")
            with path.open("rb") as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                checksum = digest.hexdigest()
            result.append({"path": str(path.relative_to(ROOT)), "size": size, "sha256": checksum})
            if len(result) > 1000:
                raise ValueError("输出文件数超过 1000")
    print(json.dumps(result))
