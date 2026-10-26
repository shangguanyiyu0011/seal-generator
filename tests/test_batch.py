from PIL import Image
import pytest

from seal_generator.batch import BatchProcessor, failed_records
from seal_generator.models import BatchSummary, NameRecord, ProgressSnapshot, RecordStatus
from seal_generator.validation import MISSING_GLYPH_PREFIX, validate_single


def ready(index: int, name: str) -> NameRecord:
    return NameRecord(index, name, name, len(name), RecordStatus.READY)


def tiny_renderer(name: str) -> Image.Image:
    if name.startswith("failure"):
        raise RuntimeError("render failed")
    return Image.new("RGBA", (8, 8), (230, 0, 18, 255))


def test_batch_counts_results_and_reports_total_progress(tmp_path):
    records = [
        ready(0, "success-company"),
        ready(1, "failure-company"),
        NameRecord(2, "short", "short", 5, RecordStatus.SKIPPED, "invalid length"),
    ]
    seen = []

    summary = BatchProcessor(tiny_renderer).run(records, tmp_path, seen.append)

    assert [record.status for record in summary.records] == [
        RecordStatus.SUCCESS,
        RecordStatus.FAILED,
        RecordStatus.SKIPPED,
    ]
    assert (seen[-1].processed, seen[-1].total) == (3, 3)
    assert (seen[-1].success, seen[-1].failed, seen[-1].skipped) == (1, 1, 1)
    assert summary.records[0].saved_path == tmp_path / "success-company.png"
    assert summary.records[0].saved_path.is_file()
    assert records[0].status is RecordStatus.READY


def test_batch_cancellation_preserves_current_save_and_does_not_render_next_ready(tmp_path):
    rendered = []
    seen = []

    def recording_renderer(name: str) -> Image.Image:
        rendered.append(name)
        return tiny_renderer(name)

    processor = BatchProcessor(recording_renderer)

    def cancel_after_first(snapshot):
        seen.append(snapshot)
        if snapshot.processed == 1:
            processor.cancel()

    summary = processor.run(
        [ready(0, "first-company"), ready(1, "second-company")],
        tmp_path,
        cancel_after_first,
    )

    assert summary.cancelled is True
    assert rendered == ["first-company"]
    assert summary.records[0].saved_path.is_file()
    assert summary.records[1].status is RecordStatus.CANCELLED
    assert summary.records[1].reason == "用户取消"
    assert len(seen) == 1
    assert (seen[-1].processed, seen[-1].total) == (1, 2)
    assert seen[-1].processed == seen[-1].success + seen[-1].failed + seen[-1].skipped


def test_cancel_accepted_before_run_is_not_reset(tmp_path):
    rendered = []
    processor = BatchProcessor(lambda name: rendered.append(name) or tiny_renderer(name))
    processor.cancel()

    summary = processor.run([ready(0, "first-company")], tmp_path)

    assert summary.cancelled is True
    assert rendered == []
    assert summary.records[0].status is RecordStatus.CANCELLED
    assert summary.final_snapshot == ProgressSnapshot(1, 1, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("first", "expected_counts"),
    [
        (ready(0, "first-company"), (1, 0, 0)),
        (
            NameRecord(0, "short", "short", 5, RecordStatus.SKIPPED, "invalid length"),
            (0, 0, 1),
        ),
        (
            NameRecord(0, "broken", "broken", 6, RecordStatus.FAILED, "prior error"),
            (0, 1, 0),
        ),
    ],
    ids=("ready", "skipped", "failed"),
)
def test_cancel_after_first_visited_record_retains_actual_progress(
    first, expected_counts, tmp_path
):
    processor = BatchProcessor(tiny_renderer)
    seen = []

    def cancel_after_first(snapshot):
        seen.append(snapshot)
        processor.cancel()

    summary = processor.run(
        [first, ready(1, "second-company")], tmp_path, cancel_after_first
    )

    assert len(seen) == 1
    assert summary.final_snapshot is seen[0]
    assert (summary.final_snapshot.processed, summary.final_snapshot.total) == (1, 2)
    assert (
        summary.final_snapshot.success,
        summary.final_snapshot.failed,
        summary.final_snapshot.skipped,
    ) == expected_counts
    assert summary.records[1].status is RecordStatus.CANCELLED


def test_batch_processor_documents_single_run_cancellation_lifecycle():
    assert "single-use" in (BatchProcessor.__doc__ or "")


def test_batch_reports_each_completed_record_with_its_snapshot(tmp_path):
    completed = []
    records = [
        ready(0, "success-company"),
        NameRecord(1, "short", "short", 5, RecordStatus.SKIPPED, "invalid length"),
    ]

    summary = BatchProcessor(tiny_renderer).run(
        records,
        tmp_path,
        on_record=lambda record, snapshot: completed.append((record, snapshot)),
    )

    assert [record.status for record, _ in completed] == [
        RecordStatus.SUCCESS,
        RecordStatus.SKIPPED,
    ]
    assert [snapshot.processed for _, snapshot in completed] == [1, 2]
    assert completed[0][0] is summary.records[0]


def test_failed_records_returns_new_ready_copies_of_failed_records_only(tmp_path):
    original_failure = NameRecord(
        0, "prior", "prior", 5, RecordStatus.FAILED, "previous error"
    )
    summary = BatchProcessor(tiny_renderer).run(
        [original_failure, ready(1, "failure-company"), ready(2, "success-company")],
        tmp_path,
    )

    retries = failed_records(summary)

    assert [record.cleaned for record in retries] == ["prior", "failure-company"]
    assert all(record.status is RecordStatus.READY for record in retries)
    assert all(record.reason == "" for record in retries)
    assert retries[0] is not summary.records[0]
    retries[0].cleaned = "changed"
    assert summary.records[0].cleaned == "prior"


def test_failed_records_does_not_retry_unsupported_glyph_validation_failures():
    name = "印章企业科技有限公司鼠"
    supported = frozenset(ord(character) for character in name if character != "鼠")
    unsupported = validate_single(name, supported)
    runtime_failure = NameRecord(
        1, "runtime", "runtime", 7, RecordStatus.FAILED, "render failed"
    )

    retries = failed_records(BatchSummary([unsupported, runtime_failure]))

    assert unsupported.reason.startswith(MISSING_GLYPH_PREFIX)
    assert [record.cleaned for record in retries] == ["runtime"]
