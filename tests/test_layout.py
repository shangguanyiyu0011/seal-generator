from dataclasses import FrozenInstanceError
from math import ceil, cos, hypot, radians, sin
from time import perf_counter

from PIL import Image, ImageChops, ImageFont
import pytest

import seal_generator.layout as layout_module
from seal_generator.renderer import _draw_glyph
from seal_generator.layout import (
    CANVAS_SIZE,
    GLYPH_CLEARANCE,
    PRESETS,
    RING_CLEARANCE,
    GlyphPlacement,
    LayoutPreset,
    SealLayout,
    layout_name,
)
from seal_generator.resources import font_path
from seal_generator.glyphs import glyph_alpha_tile


REFERENCE_CENTER_SWEEPS = {
    8: 214.0,
    9: 218.0,
    10: 224.0,
    11: 226.0,
    12: 236.0,
    13: 240.0,
    14: 242.0,
    15: 244.0,
    16: 254.0,
}


def _oriented_box(glyph, clearance=0.0):
    font = ImageFont.truetype(str(font_path()), glyph.font_size)
    left, top, right, bottom = font.getbbox(glyph.char)
    rotation = radians(glyph.angle_deg - 90)
    tangent = (cos(rotation), -sin(rotation))
    normal = (sin(rotation), cos(rotation))
    return (
        (glyph.x, glyph.y),
        tangent,
        normal,
        ((right - left) * glyph.horizontal_scale + clearance) / 2,
        (bottom - top + clearance) / 2,
    )


def _corners(box):
    center, tangent, normal, half_width, half_height = box
    return tuple(
        (
            center[0] + x_sign * half_width * tangent[0] + y_sign * half_height * normal[0],
            center[1] + x_sign * half_width * tangent[1] + y_sign * half_height * normal[1],
        )
        for x_sign in (-1, 1)
        for y_sign in (-1, 1)
    )


def _separated(first, second):
    first_center, first_x, first_y, first_half_width, first_half_height = first
    second_center, second_x, second_y, second_half_width, second_half_height = second
    center_delta = (
        second_center[0] - first_center[0],
        second_center[1] - first_center[1],
    )
    for axis in (first_x, first_y, second_x, second_y):
        distance = abs(center_delta[0] * axis[0] + center_delta[1] * axis[1])
        first_radius = (
            first_half_width * abs(first_x[0] * axis[0] + first_x[1] * axis[1])
            + first_half_height * abs(first_y[0] * axis[0] + first_y[1] * axis[1])
        )
        second_radius = (
            second_half_width * abs(second_x[0] * axis[0] + second_x[1] * axis[1])
            + second_half_height * abs(second_y[0] * axis[0] + second_y[1] * axis[1])
        )
        if distance >= first_radius + second_radius:
            return True
    return False


def _assert_safe_geometry(layout):
    assert all(-135 <= glyph.angle_deg - 90 <= 135 for glyph in layout.glyphs)
    assert not layout_module._glyph_ink_overlaps(
        layout, font_path(), collision_scale=2
    )
    boxes = tuple(_oriented_box(glyph, GLYPH_CLEARANCE) for glyph in layout.glyphs)
    safe_radius = layout.outer_radius - layout.ring_width / 2 - RING_CLEARANCE
    assert all(
        hypot(x - layout.center[0], y - layout.center[1]) <= safe_radius
        for box in boxes
        for x, y in _corners(box)
    )


@pytest.mark.parametrize("length", range(8, 17))
def test_each_supported_length_has_safe_ordered_placements(length):
    layout = layout_name("\u5370" * length, font_path())

    assert layout.size == CANVAS_SIZE == 1024
    assert len(layout.glyphs) == length
    assert all(
        first.angle_deg > second.angle_deg
        for first, second in zip(layout.glyphs, layout.glyphs[1:])
    )
    assert all(132 <= glyph.font_size <= 140 for glyph in layout.glyphs)
    assert layout.outer_radius == 426
    assert layout.ring_width == 13
    assert layout.star_outer_radius == 120
    _assert_safe_geometry(layout)


@pytest.mark.parametrize("length", range(8, 17))
def test_standard_chinese_names_use_reference_scale_and_center_sweep(length):
    layout = layout_name("\u5370" * length, font_path())

    sizes = {glyph.font_size for glyph in layout.glyphs}
    assert len(sizes) == 1
    assert 132 <= sizes.pop() <= 140
    assert layout.glyphs[0].angle_deg - layout.glyphs[-1].angle_deg == pytest.approx(
        REFERENCE_CENTER_SWEEPS[length], abs=0.01
    )
    assert all(
        hypot(glyph.x - 512, glyph.y - 512) == pytest.approx(329, abs=1)
        for glyph in layout.glyphs
    )


def test_sixteen_character_endpoints_follow_reference_arc_without_mirroring():
    layout = layout_name("\u5370" * 16, font_path())

    rotations = [glyph.angle_deg - 90 for glyph in layout.glyphs]
    assert rotations[0] == pytest.approx(127.0, abs=0.1)
    assert rotations[-1] == pytest.approx(-127.0, abs=0.1)
    assert all(-135 <= rotation <= 135 for rotation in rotations)


def test_wide_sixteen_character_name_uses_fixed_width_scale_without_tiny_text():
    layout = layout_name("W" * 16, font_path())

    assert all(glyph.font_size >= 132 for glyph in layout.glyphs)
    assert all(glyph.horizontal_scale == pytest.approx(0.52) for glyph in layout.glyphs)
    assert not layout_module._glyph_ink_overlaps(layout, font_path(), collision_scale=2)


def test_reference_sixteen_character_arc_has_no_visible_ink_overlap():
    layout = layout_name("\u5370" * 16, font_path())

    assert not layout_module._glyph_ink_overlaps(
        layout, font_path(), collision_scale=2
    )


def _two_glyph_collision_fixture(first_x, second_x):
    glyphs = tuple(
        GlyphPlacement("\u5370", x, 512, 90, 136, 0.52)
        for x in (first_x, second_x)
    )
    return SealLayout("\u5370\u5370", 1024, (512, 512), 426, 13, 120, 48, glyphs)


def test_overlapping_glyph_boxes_without_shared_visible_ink_are_safe():
    layout = _two_glyph_collision_fixture(482, 542)
    first, second = (_oriented_box(glyph, 0) for glyph in layout.glyphs)

    assert not _separated(first, second)
    assert not layout_module._glyph_ink_overlaps(layout, font_path(), collision_scale=2)


def test_visible_ink_collision_is_detected_when_glyph_is_clipped_by_canvas():
    layout = _two_glyph_collision_fixture(-10, 5)

    assert layout_module._glyph_ink_overlaps(layout, font_path(), collision_scale=2)


def test_glyph_ink_overlap_matches_renderer_layers():
    layout = layout_name("\u5370" * 16, font_path())
    scale = 4
    layers = []
    for glyph in layout.glyphs:
        layer = Image.new("RGBA", (layout.size * scale, layout.size * scale))
        _draw_glyph(
            layer,
            glyph.char,
            (glyph.x, glyph.y),
            glyph.angle_deg,
            font_path(),
            glyph.font_size,
            glyph.horizontal_scale,
        )
        layers.append(layer.getchannel("A").point(lambda alpha: 255 if alpha >= 32 else 0))

    renderer_overlaps = any(
        ImageChops.multiply(first, second).getbbox() is not None
        for index, first in enumerate(layers)
        for second in layers[index + 1 :]
    )
    assert layout_module._glyph_ink_overlaps(
        layout, font_path(), collision_scale=scale
    ) is renderer_overlaps


def test_actual_compressed_glyph_boxes_stay_inside_the_ring():
    layout = layout_name("\u5370" * 16, font_path())

    boxes = []
    for glyph in layout.glyphs:
        tile = glyph_alpha_tile(font_path(), glyph.font_size, glyph.char, glyph.horizontal_scale, 1)
        bounds = layout_module._GlyphBounds(
            ceil((tile.width - 8) / glyph.horizontal_scale), ceil(tile.height - 8)
        )
        boxes.append(layout_module._box(glyph.angle_deg, 329, bounds, glyph.horizontal_scale))
    assert all(layout_module._inside_ring(box) for box in boxes)


def test_sixteen_character_layout_rejects_unsafe_ink_without_shrinking(monkeypatch):
    attempted_sizes = []

    def always_overlaps(layout, font_file, collision_scale=2):
        attempted_sizes.append(layout.glyphs[0].font_size)
        return True

    monkeypatch.setattr(layout_module, "_glyph_ink_overlaps", always_overlaps)

    with pytest.raises(ValueError, match="\u53c2\u8003\u7248\u5f0f"):
        layout_name("\u5370" * 16, font_path())

    assert attempted_sizes
    assert min(attempted_sizes) == 132




@pytest.mark.parametrize(
    "name",
    (
        "IWIWIWIWIWIWIWIW",
        "ABC123\uff08\uff09ABC123\uff08\uff09",
        "W\u5370I ABC123\uff08\uff09\u79d1\u6280\u516c\u53f8",
    ),
)
def test_mixed_width_names_have_oriented_clearance(name):
    layout = layout_name(name, font_path())

    _assert_safe_geometry(layout)


@pytest.mark.parametrize(
    "name",
    (
        "IIIIIIII",
        "ABC12345",
        "印章ABC科技公司",
    ),
)
def test_normal_names_use_the_preset_target_sweep(name):
    layout = layout_name(name, font_path())

    actual_sweep = layout.glyphs[0].angle_deg - layout.glyphs[-1].angle_deg
    assert actual_sweep == pytest.approx(PRESETS[len(name)].sweep_deg, abs=0.01)
    _assert_safe_geometry(layout)


def test_tall_glyphs_stay_clear_of_the_ring():
    layout = layout_name("\u9b31" * 16, font_path())

    _assert_safe_geometry(layout)


def test_u_fe33_and_u_20a3_pair_retains_oriented_clearance():
    layout = layout_name("\ufe33\u20a3" * 8, font_path())

    assert layout.name == "\ufe33\u20a3" * 8
    _assert_safe_geometry(layout)


def test_u_256c_glyphs_fail_instead_of_using_unsafe_geometry():
    with pytest.raises(ValueError, match="\u53c2\u8003\u7248\u5f0f"):
        layout_name("\u256c" * 16, font_path())


def test_one_layout_stays_within_interactive_time_budget(tmp_path):
    isolated_font = tmp_path / "STZHONGS.TTF"
    isolated_font.write_bytes(font_path().read_bytes())
    started = perf_counter()

    layout_name("\u5370" * 8, isolated_font)

    assert perf_counter() - started < 1.0


def test_repeated_bounds_lookup_reuses_cached_font_metrics(monkeypatch, tmp_path):
    isolated_font = tmp_path / "STZHONGS.TTF"
    isolated_font.write_bytes(font_path().read_bytes())
    real_truetype = ImageFont.truetype
    font_loads = 0

    def tracking_truetype(*args, **kwargs):
        nonlocal font_loads
        font_loads += 1
        return real_truetype(*args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", tracking_truetype)

    layout_module._bounds("\u5370\u9b31W", isolated_font, 84)
    first_lookup_loads = font_loads
    layout_module._bounds("\u5370\u9b31W", isolated_font, 84)

    assert first_lookup_loads == 1
    assert font_loads == first_lookup_loads


def test_presets_and_preset_values_are_immutable():
    with pytest.raises(TypeError):
        PRESETS[8] = LayoutPreset(1, 1, 1)
    with pytest.raises(FrozenInstanceError):
        PRESETS[8].font_size = 1


@pytest.mark.parametrize("length", (7, 17))
def test_layout_rejects_out_of_range_names(length):
    with pytest.raises(ValueError, match=r"8\uff5e16"):
        layout_name("\u5370" * length, font_path())
