from pathlib import Path

from PIL import Image
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
)

from seal_generator.importers import TableData
from seal_generator.models import BatchSummary, NameRecord, ProgressSnapshot, RecordStatus
from seal_generator.ui.main_window import MainWindow
from seal_generator.ui.worker import BatchWorker
import seal_generator.ui.main_window as main_window_module


VALID_NAME = "印章企业科技有限公司"
SECOND_VALID_NAME = "测试企业管理有限公司"


def _show_at(qtbot, window, width, height):
    qtbot.addWidget(window)
    window.resize(width, height)
    window.show()
    qtbot.waitExposed(window)
    window.set_values([VALID_NAME])
    QApplication.processEvents()


def test_validation_table_shows_header_and_complete_row_at_reported_logical_size(qtbot):
    window = MainWindow()
    _show_at(qtbot, window, 1028, 690)

    required = (
        window.records_table.horizontalHeader().height()
        + window.records_table.rowHeight(0)
        + 4
    )
    assert window.records_table.height() >= required


def test_small_high_dpi_viewport_uses_reachable_scrollable_workspace(qtbot):
    window = MainWindow()
    _show_at(qtbot, window, 911, 512)

    scroll = window.findChild(QScrollArea, "workspaceScroll")
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.widget().minimumWidth() <= scroll.viewport().width()
    assert window.records_table.minimumHeight() >= 90


def test_window_has_required_workflow_and_no_style_controls(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "印章生成器"
    assert window.findChild(QPlainTextEdit, "manualInput") is window.manual_input
    assert window.findChild(QTableWidget, "importPreviewTable") is window.import_preview
    assert window.findChild(QComboBox, "columnCombo") is window.column_combo
    assert window.findChild(QTableWidget, "recordsTable") is window.records_table
    assert window.findChild(QLabel, "previewLabel") is window.preview_label
    assert window.findChild(QPushButton, "batchButton") is window.batch_button
    assert window.findChild(QPushButton, "cancelButton") is window.cancel_button
    assert window.findChild(QPushButton, "retryButton") is window.retry_button
    assert window.findChild(QLabel, "disclaimerLabel") is window.disclaimer_label
    assert "不具备电子签章" in window.disclaimer_label.text()
    assert window.findChild(object, "styleControls") is None


def test_manual_input_updates_counts_and_invalid_preview(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.manual_input.setPlainText(f"短名称\n{VALID_NAME}\n{VALID_NAME}")
    window.refresh_manual_records()

    assert window.source_total_value.text() == "3"
    assert window.source_blank_value.text() == "0"
    assert window.source_duplicate_value.text() == "1"
    assert window.source_invalid_length_value.text() == "1"
    assert window.source_valid_value.text() == "1"
    assert window.preview_label.pixmap() is None
    assert "8～16" in window.preview_label.text()


def test_source_counts_are_separate_and_survive_batch_progress(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_values(["", VALID_NAME, VALID_NAME, "短名称"])

    assert window.source_total_value.text() == "4"
    assert window.source_blank_value.text() == "1"
    assert window.source_duplicate_value.text() == "1"
    assert window.source_invalid_length_value.text() == "1"
    assert window.source_valid_value.text() == "1"

    window._on_progress(ProgressSnapshot(3, 2, 2, 1, 1, 0))

    assert window.source_total_value.text() == "4"
    assert window.source_blank_value.text() == "1"
    assert window.source_duplicate_value.text() == "1"
    assert window.source_invalid_length_value.text() == "1"
    assert window.source_valid_value.text() == "1"
    assert window.batch_total_value.text() == "3"
    assert window.batch_processed_value.text() == "2"
    assert window.batch_success_value.text() == "1"
    assert window.batch_failed_value.text() == "1"
    assert window.batch_skipped_value.text() == "0"


def test_invalid_single_save_click_shows_chinese_prompt(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: messages.append((title, message)),
    )
    window.manual_input.setPlainText("短名称")
    window.refresh_manual_records()

    assert window.save_current_button.isEnabled()
    qtbot.mouseClick(window.save_current_button, Qt.MouseButton.LeftButton)

    assert messages == [("无法生成", "印章名称须为 8～16 个字")]


def test_missing_glyph_single_save_click_shows_concrete_reason(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: messages.append((title, message)),
    )
    missing = NameRecord(
        0,
        "印章企业科技有限公司鼠",
        "印章企业科技有限公司鼠",
        11,
        RecordStatus.FAILED,
        "固定字体不支持字符：鼠",
    )
    window.source_tabs.setCurrentIndex(1)
    window.records = [missing]
    window.render_record(missing)

    qtbot.mouseClick(window.save_current_button, Qt.MouseButton.LeftButton)

    assert messages == [("无法生成", "固定字体不支持字符：鼠")]


def test_manual_changes_are_debounced_and_share_set_values_path(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    calls = []
    original = window.set_values
    monkeypatch.setattr(window, "set_values", lambda values: (calls.append(values), original(values))[1])

    window.manual_input.setPlainText(VALID_NAME)
    assert calls == []
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=1000)
    assert calls == [[VALID_NAME]]


def test_import_preview_and_column_selection_feed_normalized_records(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    table = TableData(
        headers=("编号", "公司名称"),
        rows=((1, VALID_NAME), (2, "短名称")),
    )

    window.set_import_table(table, Path("companies.xlsx"))
    assert window.import_preview.columnCount() == 2
    assert window.import_preview.rowCount() == 2
    assert window.column_combo.count() == 2
    window.column_combo.setCurrentIndex(1)

    assert [record.cleaned for record in window.records] == [VALID_NAME, "短名称"]
    assert window.source_total_value.text() == "2"
    assert window.source_valid_value.text() == "1"
    assert window.source_invalid_length_value.text() == "1"


def test_valid_selection_uses_pillow_pixels_for_preview(qtbot, monkeypatch):
    rendered = Image.new("RGBA", (1024, 1024), (198, 45, 39, 128))
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_render", lambda name: rendered)

    window.set_values([VALID_NAME])

    qtbot.waitUntil(lambda: window.current_image is not None)
    assert window.current_image is rendered
    assert window.preview_label.pixmap() is not None
    assert window.preview_label.text() == ""
    assert window.save_current_button.isEnabled()


def test_worker_forwards_progress_completion_and_crashes(qtbot, tmp_path):
    class SuccessfulProcessor:
        def run(self, records, folder, emit, on_record=None):
            snapshot = ProgressSnapshot(1, 1, 1, 1, 0, 0)
            emit(snapshot)
            if on_record is not None:
                on_record(records[0], snapshot)
            return BatchSummary(records)

        def cancel(self):
            pass

    record = NameRecord(0, VALID_NAME, VALID_NAME, len(VALID_NAME), RecordStatus.READY)
    worker = BatchWorker(SuccessfulProcessor(), [record], tmp_path)
    with qtbot.waitSignal(worker.progress) as progress, qtbot.waitSignal(worker.finished) as finished:
        worker.run()
    assert progress.args[0].success == 1
    assert finished.args[0].records == [record]

    class BrokenProcessor(SuccessfulProcessor):
        def run(self, records, folder, emit, on_record=None):
            raise RuntimeError("写入失败")

    broken = BatchWorker(BrokenProcessor(), [record], tmp_path)
    with qtbot.waitSignal(broken.crashed) as crashed:
        broken.run()
    assert crashed.args == ["写入失败"]


def test_batch_completion_populates_results_and_enables_retry(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.folder_edit.setText(str(tmp_path))
    failed = NameRecord(
        0,
        VALID_NAME,
        VALID_NAME,
        len(VALID_NAME),
        RecordStatus.FAILED,
        "disk full",
    )
    saved = tmp_path / "ok.png"
    succeeded = NameRecord(
        1,
        VALID_NAME,
        VALID_NAME,
        len(VALID_NAME),
        RecordStatus.SUCCESS,
        actual_filename=saved.name,
        saved_path=saved,
    )

    window._on_batch_finished(BatchSummary([failed, succeeded]))

    assert window.last_summary is not None
    assert window.results_table.rowCount() == 2
    assert window.results_table.item(0, 4).text() == "disk full"
    assert window.results_table.item(1, 2).text() == "ok.png"
    assert window.retry_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.batch_total_value.text() == "2"
    assert window.batch_processed_value.text() == "2"
    assert window.batch_success_value.text() == "1"
    assert window.batch_failed_value.text() == "1"
    assert window.batch_skipped_value.text() == "0"


def test_cancelled_batch_uses_final_snapshot_for_durable_completed_counts(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    ready = NameRecord(0, VALID_NAME, VALID_NAME, len(VALID_NAME), RecordStatus.READY)
    skipped = NameRecord(1, "短名称", "短名称", 3, RecordStatus.SKIPPED, "字数不合规")
    failed = NameRecord(2, VALID_NAME, VALID_NAME, len(VALID_NAME), RecordStatus.FAILED, "写入失败")
    snapshot = ProgressSnapshot(3, 1, 2, 0, 1, 1)

    window._on_batch_finished(
        BatchSummary([ready, skipped, failed], cancelled=True, final_snapshot=snapshot)
    )

    assert window.batch_total_value.text() == "3"
    assert window.batch_processed_value.text() == "2"
    assert window.batch_success_value.text() == "0"
    assert window.batch_failed_value.text() == "1"
    assert window.batch_skipped_value.text() == "1"
    assert window.progress_bar.format() == "已处理 2 / 3"
    assert window.status_label.text() == "作业已取消"


def test_loading_state_disables_mutating_controls_and_cancel_requests_processor(qtbot):
    class Processor:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    window = MainWindow()
    qtbot.addWidget(window)
    processor = Processor()
    window._processor = processor
    window._set_busy(True)

    assert not window.batch_button.isEnabled()
    assert not window.save_current_button.isEnabled()
    assert window.cancel_button.isEnabled()
    qtbot.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)
    assert processor.cancelled
    assert not window.cancel_button.isEnabled()


def test_cancel_emits_queued_worker_slot_as_well_as_immediate_event(qtbot):
    class Processor:
        def __init__(self):
            self.cancel_calls = 0

        def cancel(self):
            self.cancel_calls += 1

    window = MainWindow()
    qtbot.addWidget(window)
    processor = Processor()
    worker = BatchWorker(processor, [], Path())
    window.cancel_requested.connect(worker.cancel, Qt.ConnectionType.QueuedConnection)
    window._processor = processor
    window._set_busy(True)

    window.cancel_batch()

    assert processor.cancel_calls == 1
    qtbot.waitUntil(lambda: processor.cancel_calls == 2)


def test_retry_stops_pending_manual_refresh_from_clearing_live_results(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    failed = NameRecord(
        0,
        VALID_NAME,
        VALID_NAME,
        len(VALID_NAME),
        RecordStatus.FAILED,
        "disk full",
    )
    window.last_summary = BatchSummary([failed])
    captured = []

    def start_retry(records, **kwargs):
        captured.extend(records)
        window._set_busy(True)
        window.results_table.setRowCount(0)
        window._append_result(VALID_NAME, "成功", "retry.png", "C:/retry.png", "")

    monkeypatch.setattr(window, "_start_records", start_retry)
    window.manual_input.setPlainText("测试企业管理有限公司")
    assert window._manual_timer.isActive()

    window.retry_failed()
    qtbot.wait(350)

    assert not window._manual_timer.isActive()
    assert [record.cleaned for record in captured] == [VALID_NAME]
    assert window.results_table.rowCount() == 1
    assert window.results_table.item(0, 2).text() == "retry.png"


def test_batch_start_flushes_pending_manual_edit(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_render", lambda name: Image.new("RGBA", (8, 8)))
    captured = []
    monkeypatch.setattr(window, "_start_records", lambda records: captured.extend(records))

    window.manual_input.setPlainText(VALID_NAME)
    assert window._manual_timer.isActive()
    window.start_batch()

    assert not window._manual_timer.isActive()
    assert [record.cleaned for record in captured] == [VALID_NAME]


def test_single_save_flushes_pending_manual_edit(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_render", lambda name: Image.new("RGBA", (8, 8)))
    second_name = "测试企业管理有限公司"
    window.folder_edit.setText(str(tmp_path))
    window.manual_input.setPlainText(VALID_NAME)
    window.refresh_manual_records()
    window.manual_input.setPlainText(second_name)

    window.save_current()

    assert not window._manual_timer.isActive()
    qtbot.waitUntil(lambda: (tmp_path / f"{second_name}.png").is_file())
    assert (tmp_path / f"{second_name}.png").is_file()


def test_single_save_keeps_second_valid_manual_selection(qtbot, monkeypatch, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_render", lambda name: Image.new("RGBA", (8, 8)))
    window.folder_edit.setText(str(tmp_path))
    window.manual_input.setPlainText(f"{VALID_NAME}\n{SECOND_VALID_NAME}")
    window.refresh_manual_records()
    window.records_table.setCurrentCell(1, 0)

    window.save_current()

    qtbot.waitUntil(lambda: (tmp_path / f"{SECOND_VALID_NAME}.png").is_file())
    assert (tmp_path / f"{SECOND_VALID_NAME}.png").is_file()
    assert not (tmp_path / f"{VALID_NAME}.png").exists()


def test_single_save_rejects_selected_invalid_second_manual_row(
    qtbot, monkeypatch, tmp_path
):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(window, "_render", lambda name: Image.new("RGBA", (8, 8)))
    messages = []
    save_calls = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        main_window_module,
        "save_png_atomic",
        lambda *args: (save_calls.append(args), tmp_path / "unexpected.png")[1],
    )
    window.folder_edit.setText(str(tmp_path))
    window.manual_input.setPlainText(f"{VALID_NAME}\n短名称")
    window.refresh_manual_records()
    window.records_table.setCurrentCell(1, 0)

    window.save_current()

    assert messages == [("无法生成", "印章名称须为 8～16 个字")]
    assert save_calls == []
    assert list(tmp_path.iterdir()) == []


def test_import_stops_stale_manual_timer(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    imported = "测试企业管理有限公司"
    window.manual_input.setPlainText(VALID_NAME)
    assert window._manual_timer.isActive()

    window.set_import_table(TableData(("名称",), ((imported,),)), Path("names.csv"))
    qtbot.wait(350)

    assert not window._manual_timer.isActive()
    assert [record.cleaned for record in window.records] == [imported]


def test_completed_record_is_added_incrementally_on_gui_thread(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    path = tmp_path / "done.png"
    record = NameRecord(
        0,
        VALID_NAME,
        VALID_NAME,
        len(VALID_NAME),
        RecordStatus.SUCCESS,
        actual_filename=path.name,
        saved_path=path,
    )
    snapshot = ProgressSnapshot(1, 1, 1, 1, 0, 0)

    window._on_record_completed(record, snapshot)

    assert window.results_table.rowCount() == 1
    assert window.results_table.item(0, 0).text() == VALID_NAME
    assert window.results_table.item(0, 1).text() == "成功"
    assert window.results_table.item(0, 2).text() == "done.png"
    assert window.results_table.item(0, 3).text() == str(path)


def test_completion_keeps_busy_until_current_thread_finishes(qtbot):
    class ThreadStub:
        def isRunning(self):
            return False

        def deleteLater(self):
            pass

    window = MainWindow()
    qtbot.addWidget(window)
    thread = ThreadStub()
    window._thread = thread
    window._set_busy(True)

    window._on_batch_finished(BatchSummary([]), thread)

    assert window._busy is True
    assert window._thread is thread
    assert not window.batch_button.isEnabled()


def test_cannot_start_a_new_run_while_thread_is_winding_down(qtbot, monkeypatch, tmp_path):
    class ThreadStub:
        def isRunning(self):
            return False

    window = MainWindow()
    qtbot.addWidget(window)
    current_thread = ThreadStub()
    window._thread = current_thread
    window._set_busy(True)
    window.folder_edit.setText(str(tmp_path))
    constructed = []
    monkeypatch.setattr(main_window_module, "BatchProcessor", lambda renderer: constructed.append(renderer))
    record = NameRecord(0, VALID_NAME, VALID_NAME, len(VALID_NAME), RecordStatus.READY)

    window._start_records([record])

    assert constructed == []
    assert window._thread is current_thread


def test_incremental_worker_delivery_runs_on_gui_thread(qtbot, monkeypatch, tmp_path):
    class FastProcessor:
        def run(self, records, folder, on_progress, on_record):
            record = NameRecord(
                records[0].index,
                records[0].original,
                records[0].cleaned,
                records[0].length,
                RecordStatus.SUCCESS,
                actual_filename="done.png",
                saved_path=folder / "done.png",
            )
            snapshot = ProgressSnapshot(1, 1, 1, 1, 0, 0)
            on_progress(snapshot)
            on_record(record, snapshot)
            return BatchSummary([record])

        def cancel(self):
            pass

    class ObservedWindow(MainWindow):
        def __init__(self):
            self.observed_gui_threads = []
            super().__init__()

        def _on_record_completed(self, *args):
            self.observed_gui_threads.append(QThread.currentThread() is self.thread())
            super()._on_record_completed(*args)

    window = ObservedWindow()
    qtbot.addWidget(window)
    window.folder_edit.setText(str(tmp_path))
    monkeypatch.setattr(main_window_module, "BatchProcessor", lambda renderer: FastProcessor())
    record = NameRecord(0, VALID_NAME, VALID_NAME, len(VALID_NAME), RecordStatus.READY)

    window._start_records([record])
    qtbot.waitUntil(lambda: window._thread is None, timeout=3000)

    assert window.observed_gui_threads == [True]
    assert window.results_table.item(0, 2).text() == "done.png"


def test_old_thread_cleanup_cannot_clear_new_run(qtbot):
    class ThreadStub:
        def __init__(self):
            self.deleted = False

        def isRunning(self):
            return False

        def deleteLater(self):
            self.deleted = True

    window = MainWindow()
    qtbot.addWidget(window)
    old_thread = ThreadStub()
    current_thread = ThreadStub()
    current_worker = object()
    current_processor = object()
    window._thread = current_thread
    window._worker = current_worker
    window._processor = current_processor
    window._set_busy(True)

    window._on_thread_finished(old_thread)

    assert old_thread.deleted
    assert window._thread is current_thread
    assert window._worker is current_worker
    assert window._processor is current_processor
    assert window._busy is True


def test_close_is_deferred_until_running_thread_finishes(qtbot, monkeypatch):
    class ThreadStub:
        def __init__(self):
            self.deleted = False

        def isRunning(self):
            return True

        def deleteLater(self):
            self.deleted = True

    class Processor:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    window = MainWindow()
    qtbot.addWidget(window)
    thread = ThreadStub()
    processor = Processor()
    window._thread = thread
    window._processor = processor
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    assert processor.cancelled
    assert closed == []
    window._on_thread_finished(thread)
    qtbot.waitUntil(lambda: closed == [True])
    assert thread.deleted
