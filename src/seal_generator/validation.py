from collections.abc import Iterable
from typing import AbstractSet

from .models import NameRecord, RecordStatus


LENGTH_REASON = "字数不符合要求（须为 8～16 个字）"
MISSING_GLYPH_PREFIX = "固定字体不支持字符："
DUPLICATE_REASON = "重复名称，已跳过"


def _missing_characters(name: str, supported: AbstractSet[int]) -> str:
    return "".join(dict.fromkeys(character for character in name if ord(character) not in supported))


def validate_single(value: object, supported: AbstractSet[int], index: int = 0) -> NameRecord:
    original = "" if value is None else str(value)
    cleaned = original.strip()
    length = len(cleaned)
    if not 8 <= length <= 16:
        return NameRecord(index, original, cleaned, length, RecordStatus.SKIPPED, LENGTH_REASON)

    missing = _missing_characters(cleaned, supported)
    if missing:
        return NameRecord(index, original, cleaned, length, RecordStatus.FAILED, f"{MISSING_GLYPH_PREFIX}{missing}")

    return NameRecord(index, original, cleaned, length, RecordStatus.READY)


def prepare_records(values: Iterable[object], supported: AbstractSet[int]) -> list[NameRecord]:
    records: list[NameRecord] = []
    seen: set[str] = set()
    for value in values:
        if value is None or not str(value).strip():
            continue

        record = validate_single(value, supported, len(records))
        if record.cleaned in seen:
            record.status = RecordStatus.SKIPPED
            record.reason = DUPLICATE_REASON
        else:
            seen.add(record.cleaned)
        records.append(record)
    return records
