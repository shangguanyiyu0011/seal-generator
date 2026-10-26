from functools import lru_cache
from pathlib import Path
import sys

from fontTools.ttLib import TTFont


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "resources"
    return Path(__file__).resolve().parents[2] / "resources"


def resource_path(relative: str) -> Path:
    return _resource_root() / Path(relative)


def font_path() -> Path:
    path = resource_path("fonts/STZHONGS.TTF")
    if not path.is_file():
        raise FileNotFoundError(f"\u56fa\u5b9a\u5b57\u4f53\u4e0d\u5b58\u5728\uff1a{path}")
    return path


@lru_cache(maxsize=4)
def load_supported_codepoints(path: Path) -> frozenset[int]:
    font = None
    try:
        font = TTFont(str(path), lazy=True)
        cmap = font.getBestCmap()
        if not cmap:
            raise RuntimeError("字体不包含可用字符映射")
        return frozenset(cmap.keys())
    except Exception as exc:
        raise RuntimeError(f"固定字体无法加载或已损坏：{path}") from exc
    finally:
        if font is not None:
            font.close()
