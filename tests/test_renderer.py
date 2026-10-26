from math import atan2, degrees, hypot
import warnings

from PIL import Image, ImageChops
import pytest

import seal_generator.renderer as renderer_module
from seal_generator.layout import layout_name
from seal_generator.renderer import render_seal, wear_ratio
from seal_generator.resources import font_path


REFERENCE_VISIBLE_SWEEPS = {
    8: 231.0,
    9: 236.0,
    10: 239.0,
    11: 240.0,
    12: 249.0,
    13: 252.0,
    14: 254.0,
    15: 253.0,
    16: 264.0,
}


def _pixels(image):
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Image.Image.getdata is deprecated",
            category=DeprecationWarning,
        )
        return image.getdata()


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def _text_ink_metrics(image):
    angles = []
    radii = []
    for y in range(1024):
        for x in range(1024):
            if image.getpixel((x, y))[3] < 32:
                continue
            radius = hypot(x - 512, y - 512)
            if not 0.48 * 426 < radius < 0.955 * 426:
                continue
            angle = degrees(atan2(512 - y, x - 512))
            angles.append(((angle - 90 + 180) % 360) - 180)
            radii.append(radius)
    return (
        _percentile(angles, 0.995) - _percentile(angles, 0.005),
        _percentile(radii, 0.99) - _percentile(radii, 0.01),
    )


@pytest.mark.parametrize("length", range(8, 17))
def test_clean_render_matches_corresponding_reference_text_proportions(length):
    # Match the lettering of the 2026-10-26 reference, rather than substituting
    # a narrower repeated glyph whose visible endpoint bounds differ.
    name = ("某某市某某某中学" if length == 8 else
            "某某市某某某某公司" if length == 9 else
            "某某某市" + "某" * (length - 6) + "公司")
    layout = layout_name(name, font_path())
    clean = renderer_module._draw_clean_layer(layout, font_path()).resize(
        (1024, 1024), Image.Resampling.LANCZOS
    )

    visible_sweep, visible_depth = _text_ink_metrics(clean)
    assert visible_sweep == pytest.approx(REFERENCE_VISIBLE_SWEEPS[length], abs=4)
    assert 108 <= visible_depth <= 123


def test_text_does_not_shrink_as_character_count_increases():
    depths = []
    for length in range(8, 17):
        clean = renderer_module._draw_clean_layer(
            layout_name("印" * length, font_path()), font_path()
        ).resize((1024, 1024), Image.Resampling.LANCZOS)
        depths.append(_text_ink_metrics(clean)[1])

    assert max(depths) - min(depths) <= 5


@pytest.mark.parametrize("length", range(8, 17))
def test_rendered_seal_is_rgba_1024_with_transparent_corners(length):
    image = render_seal("印" * length, font_path())

    assert image.mode == "RGBA"
    assert image.size == (1024, 1024)
    assert all(
        image.getpixel(point)[3] == 0
        for point in ((0, 0), (1023, 0), (0, 1023), (1023, 1023))
    )
    assert not any(
        r > 245 and g > 245 and b > 245 and a > 0
        for r, g, b, a in _pixels(image)
    )


def test_same_name_is_identical_and_different_names_have_close_wear():
    first_name = "印章企业科技有限公司"
    other_name = "本地企业科技有限公司"

    first = render_seal(first_name, font_path())
    again = render_seal(first_name, font_path())
    other = render_seal(other_name, font_path())

    assert ImageChops.difference(first, again).getbbox() is None
    assert ImageChops.difference(first, other).getbbox() is not None
    assert abs(
        wear_ratio(first_name, font_path()) - wear_ratio(other_name, font_path())
    ) <= 0.015


def test_wear_ratio_stays_controlled_across_all_supported_lengths():
    ratios = [wear_ratio("印" * length, font_path()) for length in range(8, 17)]

    assert all(0.055 <= ratio <= 0.057 for ratio in ratios)
    assert max(ratios) - min(ratios) <= 0.015


@pytest.mark.parametrize(
    "name",
    (
        "IIIIIIII",
        "ABC12345",
        "印章ABC科技公司",
        "上海科技股份有限公司",
        "印章企业科技有限公司",
        "鬱鬱鬱鬱鬱鬱鬱鬱",
    ),
)
def test_wear_ratio_is_guaranteed_for_representative_valid_names(name):
    assert 0.055 <= wear_ratio(name, font_path()) <= 0.057


def test_representative_names_keep_close_but_distinct_wear_amounts():
    ratios = [
        wear_ratio(name, font_path())
        for name in ("IIIIIIII", "印章ABC科技公司", "上海科技股份有限公司")
    ]

    assert max(ratios) - min(ratios) <= 0.002
    assert len({round(ratio, 6) for ratio in ratios}) > 1


def test_render_boundary_rejects_unsupported_glyph_without_tofu_fallback():
    with pytest.raises(ValueError, match="固定字体不支持字符：🐱"):
        render_seal("印章企业科技🐱公司", font_path())


def test_wear_creates_material_fully_erased_alpha_defects():
    name = "印章企业科技有限公司"
    font_file = font_path()
    clean = renderer_module._draw_clean_layer(layout_name(name, font_file), font_file)
    worn = renderer_module._apply_wear(clean, renderer_module._stable_seed(name))

    erased_pixels = sum(
        clean_alpha >= 128 and worn_alpha == 0
        for clean_alpha, worn_alpha in zip(
            _pixels(clean.getchannel("A")),
            _pixels(worn.getchannel("A")),
        )
    )

    assert erased_pixels >= 1_000
    assert (
        ImageChops.difference(clean.convert("RGB"), worn.convert("RGB")).getbbox()
        is None
    )
