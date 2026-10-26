from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RecordStatus(str, Enum):
    READY = "待生成"
    SUCCESS = "成功"
    FAILED = "失败"
    SKIPPED = "跳过"
    CANCELLED = "已取消"


@dataclass(slots=True)
class NameRecord:
    index: int
    original: str
    cleaned: str
    length: int
    status: RecordStatus
    reason: str = ""
    actual_filename: str = ""
    saved_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    total: int
    valid: int
    processed: int
    success: int
    failed: int
    skipped: int


@dataclass(slots=True)
class BatchSummary:
    records: list[NameRecord] = field(default_factory=list)
    cancelled: bool = False
    final_snapshot: ProgressSnapshot | None = None
