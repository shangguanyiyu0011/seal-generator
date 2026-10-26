import hashlib
from functools import lru_cache
from math import cos, radians, sin
from pathlib import Path
import random

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

from seal_generator.glyphs import glyph_alpha_tile
from seal_generator.layout import SealLayout, layout_name
from seal_generator.resources import load_supported_codepoints


SCALE = 4
STAMP_RED = (230, 0, 18, 255)

_ELLIPSE_COUNT = (140, 160)
_STROKE_COUNT = (18, 22)
_TARGET_WEAR = (0.0556, 0.0564)
_WEAR_BOUNDS = (0.055, 0.057)

__all__ = ["render_seal", "wear_ratio"]


def _stable_seed(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")


def _star_points(cx: float, cy: float, outer: float, inner: float):
    return [
        (
            cx
            + (outer if index % 2 == 0 else inner)
            * cos(radians(-90 + index * 36)),
            cy
            + (outer if index % 2 == 0 else inner)
            * sin(radians(-90 + index * 36)),
        )
        for index in range(10)
    ]


@lru_cache(maxsize=64)
def _scaled_font(font_file: Path, font_size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_file), font_size * SCALE)


def _draw_glyph(
    layer: Image.Image,
    char: str,
    center: tuple[float, float],
    angle_deg: float,
    font_file: Path,
    font_size: int,
    horizontal_scale: float = 1.0,
) -> None:
    tile = glyph_alpha_tile(font_file, font_size, char, horizontal_scale, SCALE)
    rotated_alpha = tile.rotate(
        angle_deg - 90,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )
    rotated = Image.new("RGBA", rotated_alpha.size, STAMP_RED)
    rotated.putalpha(rotated_alpha)
    destination = (
        round(center[0] * SCALE - rotated.width / 2),
        round(center[1] * SCALE - rotated.height / 2),
    )
    layer.alpha_composite(rotated, dest=destination)


def _draw_clean_layer(layout: SealLayout, font_file: Path) -> Image.Image:
    layer = Image.new(
        "RGBA",
        (layout.size * SCALE, layout.size * SCALE),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(layer)
    center_x, center_y = (
        layout.center[0] * SCALE,
        layout.center[1] * SCALE,
    )
    outer_radius = layout.outer_radius * SCALE
    draw.ellipse(
        (
            center_x - outer_radius,
            center_y - outer_radius,
            center_x + outer_radius,
            center_y + outer_radius,
        ),
        outline=STAMP_RED,
        width=layout.ring_width * SCALE,
    )
    draw.polygon(
        _star_points(
            center_x,
            center_y,
            layout.star_outer_radius * SCALE,
            layout.star_inner_radius * SCALE,
        ),
        fill=STAMP_RED,
    )
    for glyph in layout.glyphs:
        _draw_glyph(
            layer,
            glyph.char,
            (glyph.x, glyph.y),
            glyph.angle_deg,
            font_file,
            glyph.font_size,
            glyph.horizontal_scale,
        )
    return layer


def _opaque_point(
    alpha: Image.Image,
    bounds: tuple[int, int, int, int],
    rng: random.Random,
) -> tuple[int, int]:
    left, top, right, bottom = bounds
    while True:
        point = (rng.randrange(left, right), rng.randrange(top, bottom))
        if alpha.getpixel(point) >= 128:
            return point


def _wear_masks(
    alpha: Image.Image, rng: random.Random
) -> tuple[Image.Image, Image.Image]:
    mask = Image.new("L", alpha.size, 0)
    core = Image.new("L", alpha.size, 0)
    draw = ImageDraw.Draw(mask)
    core_draw = ImageDraw.Draw(core)
    bounds = alpha.getbbox()
    if bounds is None:
        return mask, core

    for _ in range(rng.randint(*_ELLIPSE_COUNT)):
        center_x, center_y = _opaque_point(alpha, bounds, rng)
        radius_x = rng.randint(3 * SCALE, 9 * SCALE)
        radius_y = rng.randint(2 * SCALE, 6 * SCALE)
        draw.ellipse(
            (
                center_x - radius_x,
                center_y - radius_y,
                center_x + radius_x,
                center_y + radius_y,
            ),
            fill=255,
        )
        core_radius_x = max(1, radius_x // 4)
        core_radius_y = max(1, radius_y // 4)
        core_draw.ellipse(
            (
                center_x - core_radius_x,
                center_y - core_radius_y,
                center_x + core_radius_x,
                center_y + core_radius_y,
            ),
            fill=255,
        )

    for _ in range(rng.randint(*_STROKE_COUNT)):
        center_x, center_y = _opaque_point(alpha, bounds, rng)
        half_length = rng.randint(5 * SCALE, 14 * SCALE)
        angle = rng.uniform(0, 180)
        delta_x = half_length * cos(radians(angle))
        delta_y = half_length * sin(radians(angle))
        line = (
            center_x - delta_x,
            center_y - delta_y,
            center_x + delta_x,
            center_y + delta_y,
        )
        width = rng.randint(1 * SCALE, 3 * SCALE)
        draw.line(
            line,
            fill=255,
            width=width,
        )
        core_draw.line(
            (
                center_x - delta_x / 4,
                center_y - delta_y / 4,
                center_x + delta_x / 4,
                center_y + delta_y / 4,
            ),
            fill=255,
            width=max(1, width // 3),
        )
    return mask, core


def _alpha_sum(alpha: Image.Image) -> float:
    return ImageStat.Stat(alpha).sum[0]


def _apply_wear(layer: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    alpha = layer.getchannel("A")
    clean_sum = _alpha_sum(alpha)
    if clean_sum == 0:
        return layer.copy()

    mask, core = _wear_masks(alpha, rng)
    core_alpha = ImageChops.multiply(alpha, ImageChops.invert(core))
    core_sum = _alpha_sum(core_alpha)
    core_removed_sum = clean_sum - core_sum
    halo = ImageChops.subtract(mask, core)
    fully_worn = ImageChops.multiply(core_alpha, ImageChops.invert(halo))
    halo_removable_sum = core_sum - _alpha_sum(fully_worn)
    target_ratio = rng.uniform(*_TARGET_WEAR)
    remaining_target = max(0.0, clean_sum * target_ratio - core_removed_sum)
    strength = (
        min(255, round(255 * remaining_target / halo_removable_sum))
        if halo_removable_sum
        else 0
    )

    def worn_at(halo_strength: int) -> tuple[Image.Image, float]:
        controlled_halo = halo.point(
            lambda value: round(value * halo_strength / 255)
        )
        controlled_mask = ImageChops.lighter(core, controlled_halo)
        worn_alpha = ImageChops.multiply(alpha, ImageChops.invert(controlled_mask))
        worn_image = layer.copy()
        worn_image.putalpha(worn_alpha)
        ratio = 1 - _alpha_sum(worn_alpha) / clean_sum
        return worn_image, ratio

    worn, actual_ratio = worn_at(strength)
    for _ in range(256):
        if _WEAR_BOUNDS[0] <= actual_ratio <= _WEAR_BOUNDS[1]:
            return worn
        next_strength = strength + (1 if actual_ratio < _WEAR_BOUNDS[0] else -1)
        if not 0 <= next_strength <= 255:
            break
        strength = next_strength
        worn, actual_ratio = worn_at(strength)
    raise ValueError("磨损程度无法在固定范围内安全校准")


def _clear_transparent_rgb(image: Image.Image) -> None:
    transparent = image.getchannel("A").point(lambda value: 255 if value == 0 else 0)
    image.paste((0, 0, 0, 0), mask=transparent)


def render_seal(name: str, font_file: Path) -> Image.Image:
    supported = load_supported_codepoints(font_file)
    missing = "".join(
        dict.fromkeys(character for character in name if ord(character) not in supported)
    )
    if missing:
        raise ValueError(f"固定字体不支持字符：{missing}")
    layout = layout_name(name, font_file)
    layer = _draw_clean_layer(layout, font_file)
    worn = _apply_wear(layer, _stable_seed(name))
    result = worn.resize((1024, 1024), Image.Resampling.LANCZOS)
    _clear_transparent_rgb(result)
    return result


def wear_ratio(name: str, font_file: Path) -> float:
    layout = layout_name(name, font_file)
    clean = _draw_clean_layer(layout, font_file)
    worn = _apply_wear(clean, _stable_seed(name))
    clean_alpha_sum = _alpha_sum(clean.getchannel("A"))
    if clean_alpha_sum == 0:
        return 0.0
    return 1 - _alpha_sum(worn.getchannel("A")) / clean_alpha_sum
