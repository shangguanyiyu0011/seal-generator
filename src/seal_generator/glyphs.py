from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


@lru_cache(maxsize=4096)
def glyph_alpha_tile(
    font_file: Path,
    font_size: int,
    char: str,
    horizontal_scale: float,
    render_scale: int,
) -> Image.Image:
    font = ImageFont.truetype(str(font_file), font_size * render_scale)
    left, top, right, bottom = font.getbbox(char)
    padding = 4 * render_scale
    if right <= left or bottom <= top:
        return Image.new("L", (2 * padding, 2 * padding), 0)
    glyph = Image.new("L", (right - left, bottom - top), 0)
    ImageDraw.Draw(glyph).text((-left, -top), char, font=font, fill=255)
    scaled_width = max(1, round(glyph.width * horizontal_scale))
    glyph = glyph.resize((scaled_width, glyph.height), Image.Resampling.LANCZOS)
    tile = Image.new("L", (glyph.width + 2 * padding, glyph.height + 2 * padding), 0)
    tile.paste(glyph, (padding, padding))
    return tile
