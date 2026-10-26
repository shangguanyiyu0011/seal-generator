import csv

from openpyxl import Workbook
import pytest

from seal_generator.importers import extract_column, load_table


def test_load_utf8_sig_csv_and_extract_selected_column(tmp_path):
    path = tmp_path / "names.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(
            [["编号", "公司名称"], [1, "印章企业科技有限公司"]]
        )

    table = load_table(path)

    assert table.headers == ("编号", "公司名称")
    assert extract_column(table, 1) == ["印章企业科技有限公司"]


def test_load_xlsx_preserves_blank_cells_and_headers(tmp_path):
    path = tmp_path / "names.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["公司名称", "备注"])
    sheet.append(["印章企业科技有限公司", None])
    book.save(path)

    table = load_table(path)

    assert table.headers == ("公司名称", "备注")
    assert table.rows[0] == ("印章企业科技有限公司", None)


def test_rejects_missing_headers_and_unsupported_extension(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="没有可用的表头"):
        load_table(empty)

    bad = tmp_path / "names.txt"
    bad.write_text("name", encoding="utf-8")

    with pytest.raises(ValueError, match="不支持的文件格式"):
        load_table(bad)


@pytest.mark.parametrize("encoding", ["gbk", "gb18030"])
def test_loads_supported_chinese_csv_encodings_deterministically(tmp_path, encoding):
    path = tmp_path / f"names-{encoding}.csv"
    path.write_bytes("\u516c\u53f8\u540d\u79f0\n\u6d4b\u8bd5\u4f01\u4e1a\n".encode(encoding))

    table = load_table(path)

    assert table.headers == ("\u516c\u53f8\u540d\u79f0",)
    assert table.rows == (("\u6d4b\u8bd5\u4f01\u4e1a",),)


def test_csv_keeps_quoted_embedded_newline_in_one_field(tmp_path):
    path = tmp_path / "quoted.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(
            [["\u516c\u53f8\u540d\u79f0", "\u5907\u6ce8"], ["\u6d4b\u8bd5\u4f01\u4e1a", "\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c"]]
        )

    table = load_table(path)

    assert table.rows == (("\u6d4b\u8bd5\u4f01\u4e1a", "\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c"),)


def test_preview_limit_restricts_data_rows(tmp_path):
    path = tmp_path / "preview.csv"
    path.write_text("name\none\ntwo\n", encoding="utf-8")

    table = load_table(path, preview_limit=1)

    assert table.rows == (("one",),)


def test_extract_column_rejects_non_integer_and_out_of_range_indexes(tmp_path):
    path = tmp_path / "names.csv"
    path.write_text("name\nexample\n", encoding="utf-8")
    table = load_table(path)

    with pytest.raises(ValueError, match="\u5217\u7d22\u5f15"):
        extract_column(table, True)
    with pytest.raises(ValueError, match="\u5217\u7d22\u5f15"):
        extract_column(table, "0")
    with pytest.raises(IndexError, match="\u4e0d\u5b58\u5728"):
        extract_column(table, 1)


def test_load_xlsm_uses_excel_reader(tmp_path):
    path = tmp_path / "names.xlsm"
    book = Workbook()
    book.active.append(["name"])
    book.active.append(["example"])
    book.save(path)

    assert load_table(path).rows == (("example",),)


def test_invalid_csv_bytes_report_an_encoding_error(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_bytes(b"\xff\xff")

    with pytest.raises(ValueError, match="无法识别 CSV 文件编码"):
        load_table(path)
