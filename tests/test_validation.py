from seal_generator.models import RecordStatus
from seal_generator.resources import font_path, load_supported_codepoints
from seal_generator.validation import MISSING_GLYPH_PREFIX, prepare_records, validate_single


SUPPORTED = frozenset(ord(character) for character in "印章企业科技股份有限公司内部 空格ABC123（）")


def test_prepare_records_removes_blank_rows_and_trims_edges():
    rows = prepare_records(["  印章企业科技有限公司  ", "  ", None], SUPPORTED)

    assert [row.cleaned for row in rows] == ["印章企业科技有限公司"]
    assert rows[0].status is RecordStatus.READY


def test_length_bounds_and_duplicates_are_skipped():
    rows = prepare_records(["印" * 7, "印" * 8, "印" * 8, "印" * 16, "印" * 17], SUPPORTED)

    assert [row.status for row in rows] == [
        RecordStatus.SKIPPED,
        RecordStatus.READY,
        RecordStatus.SKIPPED,
        RecordStatus.READY,
        RecordStatus.SKIPPED,
    ]
    assert rows[0].reason == "字数不符合要求（须为 8～16 个字）"
    assert rows[2].reason == "重复名称，已跳过"


def test_duplicates_are_compared_after_edge_whitespace_is_trimmed():
    rows = prepare_records(["  印印印印印印印印  ", "印印印印印印印印"], SUPPORTED)

    assert [row.cleaned for row in rows] == ["印印印印印印印印"] * 2
    assert [row.status for row in rows] == [RecordStatus.READY, RecordStatus.SKIPPED]
    assert rows[1].reason == "重复名称，已跳过"


def test_internal_space_and_punctuation_each_count_as_one_character():
    row = validate_single(" ABC 123（）", SUPPORTED)

    assert row.cleaned == "ABC 123（）"
    assert row.length == 9


def test_missing_glyph_is_a_failure_without_fallback():
    row = validate_single("印章企业科技🐱公司", SUPPORTED)

    assert row.status is RecordStatus.FAILED
    assert row.reason == f"{MISSING_GLYPH_PREFIX}🐱"


def test_bundled_font_is_used_for_real_glyph_validation():
    supported = load_supported_codepoints(font_path())

    row = validate_single("上海科技股份有限公司", supported)

    assert row.status is RecordStatus.READY
