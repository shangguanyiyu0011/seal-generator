from dataclasses import dataclass
from functools import lru_cache
from math import asin, ceil, cos, degrees, hypot, radians, sin, sqrt
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from unicodedata import category, east_asian_width

from PIL import Image, ImageChops, ImageFont

from seal_generator.glyphs import glyph_alpha_tile


CANVAS_SIZE = 1024
GLYPH_CLEARANCE = 4.0
RING_CLEARANCE = 8.0
TANGENT_ROTATION_LIMIT = 135.0
_CENTER = (512.0, 512.0)
_OUTER_RADIUS = 426
_RING_WIDTH = 13
_MIN_BASELINE_RADIUS = 250
_GEOMETRY_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class LayoutPreset:
    font_size: int
    min_font_size: int
    sweep_deg: float
    baseline_radius: float = 355.0
    horizontal_scale: float = 1.0


PRESETS: Mapping[int, LayoutPreset] = MappingProxyType(
    {
        8: LayoutPreset(136, 132, 214, 329, 0.86),
        9: LayoutPreset(136, 132, 218, 329, 0.84),
        10: LayoutPreset(136, 132, 224, 329, 0.75),
        11: LayoutPreset(136, 132, 226, 329, 0.75),
        12: LayoutPreset(136, 132, 236, 329, 0.71),
        13: LayoutPreset(136, 132, 240, 329, 0.66),
        14: LayoutPreset(136, 132, 242, 329, 0.60),
        15: LayoutPreset(136, 132, 244, 329, 0.59),
        16: LayoutPreset(136, 132, 254, 329, 0.52),
    }
)


@dataclass(frozen=True, slots=True)
class GlyphPlacement:
    char: str
    x: float
    y: float
    angle_deg: float
    font_size: int
    horizontal_scale: float = 1.0


@dataclass(frozen=True, slots=True)
class SealLayout:
    name: str
    size: int
    center: tuple[float, float]
    outer_radius: int
    ring_width: int
    star_outer_radius: int
    star_inner_radius: int
    glyphs: tuple[GlyphPlacement, ...]


@dataclass(frozen=True, slots=True)
class _GlyphBounds:
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class _OrientedBox:
    center: tuple[float, float]
    tangent: tuple[float, float]
    normal: tuple[float, float]
    half_width: float
    half_height: float


@lru_cache(maxsize=256)
def _font(font_file: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_file), size)


@lru_cache(maxsize=4096)
def _glyph_bounds(font_file: Path, size: int, char: str) -> _GlyphBounds:
    left, top, right, bottom = _font(font_file, size).getbbox(char)
    return _GlyphBounds(right - left, bottom - top)


def _bounds(name: str, font_file: Path, size: int) -> tuple[_GlyphBounds, ...]:
    return tuple(_glyph_bounds(font_file, size, char) for char in name)


@lru_cache(maxsize=4096)
def _tangent_ink_width(font_file: Path, size: int, char: str, horizontal_scale: float, collision_scale: int = 2) -> float:
    bbox = glyph_alpha_tile(font_file, size, char, horizontal_scale, collision_scale).getbbox()
    return 0.0 if bbox is None else (bbox[2] - bbox[0]) / collision_scale


def _glyph_ink_overlaps(
    layout: "SealLayout",
    font_file: Path,
    collision_scale: int = 2,
) -> bool:
    """Return True only when visible glyph ink shares a collision pixel."""
    canvas_size = layout.size * collision_scale
    occupied = Image.new("L", (canvas_size, canvas_size), 0)
    for glyph in layout.glyphs:
        tile = glyph_alpha_tile(font_file, glyph.font_size, glyph.char, glyph.horizontal_scale, collision_scale)
        rotated = tile.rotate(
            glyph.angle_deg - 90,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
        destination = (
            round(glyph.x * collision_scale - rotated.width / 2),
            round(glyph.y * collision_scale - rotated.height / 2),
        )
        left = max(0, destination[0])
        top = max(0, destination[1])
        right = min(canvas_size, destination[0] + rotated.width)
        bottom = min(canvas_size, destination[1] + rotated.height)
        if left >= right or top >= bottom:
            continue
        source = (
            left - destination[0],
            top - destination[1],
            right - destination[0],
            bottom - destination[1],
        )
        ink = rotated.crop(source).point(lambda alpha: 255 if alpha >= 32 else 0)
        occupied_region = occupied.crop((left, top, right, bottom))
        if ImageChops.multiply(occupied_region, ink).getbbox() is not None:
            return True
        occupied.paste(ImageChops.lighter(occupied_region, ink), (left, top))
    return False


def _box(
    angle_deg: float,
    radius: float,
    bounds: _GlyphBounds,
    horizontal_scale: float = 1.0,
) -> _OrientedBox:
    angle = radians(angle_deg)
    rotation = radians(angle_deg - 90)
    return _OrientedBox(
        (
            _CENTER[0] + radius * cos(angle),
            _CENTER[1] - radius * sin(angle),
        ),
        (cos(rotation), -sin(rotation)),
        (sin(rotation), cos(rotation)),
        (ceil(bounds.width * horizontal_scale) + GLYPH_CLEARANCE) / 2,
        (bounds.height + GLYPH_CLEARANCE) / 2,
    )


def _projection_radius(box: _OrientedBox, axis: tuple[float, float]) -> float:
    return box.half_width * abs(box.tangent[0] * axis[0] + box.tangent[1] * axis[1]) + box.half_height * abs(
        box.normal[0] * axis[0] + box.normal[1] * axis[1]
    )


def _separated(first: _OrientedBox, second: _OrientedBox) -> bool:
    delta = (second.center[0] - first.center[0], second.center[1] - first.center[1])
    for axis in (first.tangent, first.normal, second.tangent, second.normal):
        distance = abs(delta[0] * axis[0] + delta[1] * axis[1])
        if distance >= _projection_radius(first, axis) + _projection_radius(second, axis):
            return True
    return False


def _inside_ring(box: _OrientedBox) -> bool:
    safe_radius = _OUTER_RADIUS - _RING_WIDTH / 2 - RING_CLEARANCE
    for x_sign in (-1, 1):
        for y_sign in (-1, 1):
            x = box.center[0] + x_sign * box.half_width * box.tangent[0] + y_sign * box.half_height * box.normal[0]
            y = box.center[1] + x_sign * box.half_width * box.tangent[1] + y_sign * box.half_height * box.normal[1]
            if hypot(x - _CENTER[0], y - _CENTER[1]) > safe_radius:
                return False
    return True


def _minimum_ink_separation_deg(radius: float, first_width: float, second_width: float) -> float:
    required_chord = (first_width + second_width) / 2 + GLYPH_CLEARANCE
    return degrees(2 * asin(min(1.0, required_chord / (2 * radius))))


def _maximum_safe_radius(bounds: tuple[_GlyphBounds, ...], preset: LayoutPreset) -> float | None:
    safe_radius = _OUTER_RADIUS - _RING_WIDTH / 2 - RING_CLEARANCE
    radius = preset.baseline_radius
    for glyph_bounds in bounds:
        half_width = (ceil(glyph_bounds.width * preset.horizontal_scale) + GLYPH_CLEARANCE) / 2
        half_height = (glyph_bounds.height + GLYPH_CLEARANCE) / 2
        if half_width >= safe_radius:
            return None
        glyph_radius = sqrt(safe_radius * safe_radius - half_width * half_width) - half_height
        radius = min(radius, glyph_radius - _GEOMETRY_EPSILON)
    if radius < max(_MIN_BASELINE_RADIUS, preset.baseline_radius):
        return None
    return preset.baseline_radius


def _safe_candidate(
    name: str, font_file: Path, size: int, bounds: tuple[_GlyphBounds, ...], preset: LayoutPreset
) -> tuple[float, tuple[float, ...]] | None:
    radius = _maximum_safe_radius(bounds, preset)
    if radius is None:
        return None
    widths = tuple(_tangent_ink_width(font_file, size, char, preset.horizontal_scale) for char in name)
    gaps = tuple(
        _minimum_ink_separation_deg(radius, first, second)
        for first, second in zip(widths, widths[1:])
    )
    target_sweep = preset.sweep_deg
    minimum_sweep = sum(gaps)
    if minimum_sweep > target_sweep:
        return None
    # The current 8–16 character reference uses evenly advanced Chinese type.
    # Stroke bounds differ per ideograph and must not determine its centre gap.
    if all(east_asian_width(char) in {"W", "F"} and category(char).startswith("L") for char in name):
        step = target_sweep / (len(name) - 1)
        return radius, (step,) * (len(name) - 1)
    surplus_per_gap = (target_sweep - minimum_sweep) / len(gaps)
    target_gaps = tuple(gap + surplus_per_gap for gap in gaps)
    return radius, target_gaps


def layout_name(name: str, font_file: Path) -> SealLayout:
    if len(name) not in PRESETS:
        raise ValueError("\u5370\u7ae0\u540d\u79f0\u987b\u4e3a 8\uff5e16 \u4e2a\u5b57")

    preset = PRESETS[len(name)]
    for size in range(preset.font_size, preset.min_font_size - 1, -1):
        bounds = _bounds(name, font_file, size)
        candidate = _safe_candidate(name, font_file, size, bounds, preset)
        if candidate is None:
            continue
        radius, gaps = candidate
        sweep = sum(gaps)
        angles = [90 + sweep / 2]
        for gap in gaps:
            angles.append(angles[-1] - gap)
        boxes = tuple(
            _box(angle, radius, glyph_bounds, preset.horizontal_scale)
            for angle, glyph_bounds in zip(angles, bounds)
        )
        if not all(_inside_ring(box) for box in boxes):
            continue
        glyphs = tuple(
            GlyphPlacement(char, box.center[0], box.center[1], angle, size, preset.horizontal_scale)
            for char, angle, box in zip(name, angles, boxes)
        )
        layout = SealLayout(name, CANVAS_SIZE, _CENTER, _OUTER_RADIUS, _RING_WIDTH, 120, 48, glyphs)
        if not _glyph_ink_overlaps(layout, font_file):
            return layout

    raise ValueError("\u6587\u5b57\u65e0\u6cd5\u5728\u53c2\u8003\u7248\u5f0f\u5185\u5b89\u5168\u6392\u7248")
