"""Sequential, cancellable batch generation for validated seal records."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from threading import Event

from PIL import Image

from .models import BatchSummary, NameRecord, ProgressSnapshot, RecordStatus
from .storage import save_png_atomic
from .validation import MISSING_GLYPH_PREFIX


class BatchProcessor:
    """Render and save records; single-use after cancellation is requested."""

    def __init__(self, renderer: Callable[[str], Image.Image]) -> None:
        self._renderer = renderer
        self._cancelled = Event()

    def cancel(self) -> None:
        """Request cooperative cancellation before the next READY record."""
        self._cancelled.set()

    def run(
        self,
        records: list[NameRecord],
        folder: Path,
        on_progress: Callable[[ProgressSnapshot], None] = lambda snapshot: None,
        on_record: Callable[[NameRecord, ProgressSnapshot], None] | None = None,
    ) -> BatchSummary:
        """Process records sequentially without mutating the input records."""
        results = copy.deepcopy(records)
        total = len(results)
        valid = sum(record.status is RecordStatus.READY for record in results)
        success = failed = skipped = processed = 0
        snapshot = ProgressSnapshot(total, valid, 0, 0, 0, 0)

        for record in results:
            if self._cancelled.is_set():
                if record.status is RecordStatus.READY:
                    record.status = RecordStatus.CANCELLED
                    record.reason = "用户取消"
                continue

            if record.status is RecordStatus.SKIPPED:
                skipped += 1
            elif record.status is RecordStatus.FAILED:
                failed += 1
            elif record.status is RecordStatus.READY:
                try:
                    image = self._renderer(record.cleaned)
                    path = save_png_atomic(image, folder, record.cleaned)
                    record.status = RecordStatus.SUCCESS
                    record.saved_path = path
                    record.actual_filename = path.name
                    success += 1
                except Exception as exc:
                    record.status = RecordStatus.FAILED
                    record.reason = str(exc)
                    failed += 1
            else:
                continue

            processed += 1
            snapshot = ProgressSnapshot(
                total, valid, processed, success, failed, skipped
            )
            on_progress(snapshot)
            if on_record is not None:
                on_record(record, snapshot)

        return BatchSummary(results, self._cancelled.is_set(), snapshot)


def failed_records(summary: BatchSummary) -> list[NameRecord]:
    """Return independent READY copies of the records that actually failed."""
    retries = []
    for record in summary.records:
        if (
            record.status is RecordStatus.FAILED
            and not record.reason.startswith(MISSING_GLYPH_PREFIX)
        ):
            retry = copy.deepcopy(record)
            retry.status = RecordStatus.READY
            retry.reason = ""
            retry.actual_filename = ""
            retry.saved_path = None
            retries.append(retry)
    return retries


def pending_records(summary: BatchSummary) -> list[NameRecord]:
    """Resume only work never saved; successful files must not be duplicated."""
    pending = []
    for record in summary.records:
        if record.status in {RecordStatus.CANCELLED, RecordStatus.READY}:
            ready = copy.deepcopy(record)
            ready.status = RecordStatus.READY
            ready.reason = ""
            ready.actual_filename = ""
            ready.saved_path = None
            pending.append(ready)
    return pending


def summary_snapshot(records: list[NameRecord]) -> ProgressSnapshot:
    counts = {status: sum(r.status is status for r in records) for status in RecordStatus}
    return ProgressSnapshot(
        len(records), len(records) - counts[RecordStatus.SKIPPED],
        counts[RecordStatus.SUCCESS] + counts[RecordStatus.FAILED] + counts[RecordStatus.SKIPPED],
        counts[RecordStatus.SUCCESS], counts[RecordStatus.FAILED], counts[RecordStatus.SKIPPED],
    )


def merge_summaries(previous: BatchSummary, latest: BatchSummary) -> BatchSummary:
    """Replace attempted source indices, preserving other records and paths."""
    updates = {record.index: record for record in latest.records}
    merged = [copy.deepcopy(updates.get(record.index, record)) for record in previous.records]
    cancelled = latest.cancelled or any(record.status is RecordStatus.CANCELLED for record in merged)
    return BatchSummary(merged, cancelled, summary_snapshot(merged))
