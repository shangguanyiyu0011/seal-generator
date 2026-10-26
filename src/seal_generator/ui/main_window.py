"""Main PySide6 window for importing, previewing, and generating seals."""

from __future__ import annotations

from pathlib import Path
from collections import OrderedDict

from PIL.Image import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QEvent, QThread, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from seal_generator.batch import BatchProcessor, failed_records, pending_records, merge_summaries, summary_snapshot
from seal_generator.importers import TableData, extract_column
from seal_generator.models import BatchSummary, NameRecord, ProgressSnapshot, RecordStatus
from seal_generator.renderer import render_seal
from seal_generator.resources import font_path, load_supported_codepoints
from seal_generator.storage import save_png_atomic
from seal_generator.ui.worker import BatchWorker, BackgroundTask, read_import
from seal_generator.validation import DUPLICATE_REASON, LENGTH_REASON, prepare_records


DISCLAIMER = (
    "本软件仅用于印章视觉排版和设计样稿，不具备电子签章、"
    "身份认证或法律效力。"
)

STATUS_TEXT = {
    RecordStatus.READY: "待生成",
    RecordStatus.SUCCESS: "成功",
    RecordStatus.FAILED: "失败",
    RecordStatus.SKIPPED: "跳过",
    RecordStatus.CANCELLED: "已取消",
}


class PreviewLabel(QLabel):
    """A QLabel whose backdrop makes image transparency unambiguous."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("previewLabel")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setWordWrap(True)
        self.setMargin(28)
        checker = QPixmap(24, 24)
        checker.fill(QColor("#f7f3e9"))
        from PySide6.QtGui import QPainter

        painter = QPainter(checker)
        painter.fillRect(0, 0, 12, 12, QColor("#e4ded2"))
        painter.fillRect(12, 12, 12, 12, QColor("#e4ded2"))
        painter.end()
        palette = self.palette()
        palette.setBrush(QPalette.ColorRole.Window, checker)
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self._source_pixmap: QPixmap | None = None

    def set_pillow_image(self, image: Image) -> None:
        qimage = ImageQt(image.convert("RGBA"))
        self._source_pixmap = QPixmap.fromImage(qimage.copy())
        self.setText("")
        self._fit_pixmap()

    def show_reason(self, reason: str) -> None:
        self._source_pixmap = None
        self.clear()
        self.setText(f"无法生成预览\n{reason}")

    def pixmap(self):
        """Return ``None`` for a text-only invalid state across Qt versions."""
        if self._source_pixmap is None:
            return None
        return super().pixmap()

    def _fit_pixmap(self) -> None:
        if self._source_pixmap is None:
            return
        target = self.contentsRect().size()
        target.setWidth(max(32, target.width() - 40))
        target.setHeight(max(32, target.height() - 40))
        self.setPixmap(
            self._source_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_pixmap()


class MainWindow(QMainWindow):
    """A focused print-studio workbench with one normalized data path."""

    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("印章生成器")
        self.setObjectName("mainWindow")
        self.resize(1260, 850)
        self.setMinimumSize(860, 500)

        self._font_path = font_path()
        self._supported = load_supported_codepoints(self._font_path)
        self._import_table: TableData | None = None
        self.records: list[NameRecord] = []
        self.current_record: NameRecord | None = None
        self.current_image: Image | None = None
        self.last_summary: BatchSummary | None = None
        self._run_base: BatchSummary | None = None
        self._run_indices: set[int] = set()
        self._result_rows: dict[int, int] = {}
        self._preview_token = 0
        self._preview_task = None
        self._preview_pending = None
        self._preview_cache = OrderedDict()
        self._save_after_preview = None
        self._import_token = 0
        self._import_task = None
        self._import_pending = None
        self._import_path = None
        self._importing = False
        self._processor: BatchProcessor | None = None
        self._worker: BatchWorker | None = None
        self._thread: QThread | None = None
        self._busy = False
        self._cancelling = False
        self._close_pending = False

        self._build_ui()
        self._apply_theme()
        self._wire_events()
        self._update_record_actions()
        self.preview_label.show_reason("请输入或导入公司名称")

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame(objectName="masthead")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 20, 28, 18)
        title_block = QVBoxLayout()
        title = QLabel("印章生成器", objectName="appTitle")
        subtitle = QLabel("SEAL STUDIO   /   本地印章设计工作台", objectName="appSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header_layout.addLayout(title_block)
        header_layout.addStretch()
        local_badge = QLabel("●  全程本地处理", objectName="localBadge")
        header_layout.addWidget(local_badge)
        outer.addWidget(header)

        workspace = QWidget(objectName="workspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(24, 12, 24, 20)
        workspace_layout.setSpacing(20)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(18)
        splitter.addWidget(self._build_data_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setSizes([730, 470])
        workspace_layout.addWidget(splitter, 3)
        workspace_layout.addWidget(self._build_job_panel(), 2)

        self.disclaimer_label = QLabel(DISCLAIMER, objectName="disclaimerLabel")
        self.disclaimer_label.setWordWrap(False)
        self.disclaimer_label.setMinimumHeight(28)
        workspace_layout.addWidget(self.disclaimer_label)
        self.workspace_scroll = QScrollArea(objectName="workspaceScroll")
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        workspace.setMinimumSize(820, 740)
        self.workspace_scroll.setWidget(workspace)
        outer.addWidget(self.workspace_scroll, 1)

    def _section_heading(self, number: str, title: str, note: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        index = QLabel(number, objectName="sectionIndex")
        heading = QLabel(title, objectName="sectionTitle")
        hint = QLabel(note, objectName="sectionHint")
        layout.addWidget(index)
        layout.addWidget(heading)
        layout.addStretch()
        layout.addWidget(hint)
        return row

    def _build_data_panel(self) -> QWidget:
        panel = QFrame(objectName="panel")
        self._data_panel = panel
        panel.installEventFilter(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self._section_heading("01", "名称数据", "每行一个名称"))

        self.source_tabs = QTabWidget(objectName="sourceTabs")
        manual_page = QWidget()
        manual_layout = QVBoxLayout(manual_page)
        manual_layout.setContentsMargins(10, 10, 10, 10)
        self.manual_input = QPlainTextEdit(objectName="manualInput")
        self.manual_input.setPlaceholderText("例：印章企业科技有限公司\n每行输入一个公司名称")
        self.manual_input.setAccessibleName("手动输入公司名称")
        manual_layout.addWidget(self.manual_input)
        self.source_tabs.addTab(manual_page, "手动输入")

        import_page = QWidget()
        import_layout = QVBoxLayout(import_page)
        import_layout.setContentsMargins(10, 10, 10, 10)
        import_layout.setSpacing(8)
        file_row = QHBoxLayout()
        self.import_path_edit = QLineEdit(objectName="importPath")
        self.import_path_edit.setReadOnly(True)
        self.import_path_edit.setPlaceholderText("支持 .xlsx / .xlsm / .xls / .csv")
        self.import_button = QPushButton("选择表格…", objectName="importButton")
        self.import_button.setShortcut("Ctrl+I")
        file_row.addWidget(self.import_path_edit, 1)
        file_row.addWidget(self.import_button)
        import_layout.addLayout(file_row)
        column_row = QHBoxLayout()
        column_row.addWidget(QLabel("公司名称列"))
        self.column_combo = QComboBox(objectName="columnCombo")
        self.column_combo.setAccessibleName("公司名称列")
        column_row.addWidget(self.column_combo, 1)
        import_layout.addLayout(column_row)
        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("工作表"))
        self.sheet_combo = QComboBox(objectName="sheetCombo")
        self.sheet_combo.setAccessibleName("选择工作表")
        self.sheet_combo.setEnabled(False)
        options_row.addWidget(self.sheet_combo, 1)
        self.header_check = QCheckBox("首行是表头", objectName="headerCheck")
        self.header_check.setToolTip("勾选后首行仅作列名，不参与生成；不勾选则保留第一家公司。")
        options_row.addWidget(self.header_check)
        import_layout.addLayout(options_row)
        self.import_note = QLabel("保留首行数据 · 预览最多显示 100 行", objectName="quietText")
        import_layout.addWidget(self.import_note)
        self.import_preview = self._table("importPreviewTable")
        self.import_preview.setMinimumHeight(80)
        import_layout.addWidget(self.import_preview)
        self.source_tabs.addTab(import_page, "表格导入")
        self._data_splitter = QSplitter(Qt.Orientation.Vertical)
        self._data_splitter.setChildrenCollapsible(False)
        self._data_splitter.setHandleWidth(14)
        self._data_splitter.addWidget(self.source_tabs)
        layout.addWidget(self._data_splitter, 1)

        records_group = QWidget()
        records_layout = QVBoxLayout(records_group)
        records_layout.setContentsMargins(0, 0, 0, 0)
        records_layout.setSpacing(8)
        records_head = QHBoxLayout()
        records_head.addWidget(QLabel("清理与校验", objectName="subheading"))
        records_head.addStretch()
        records_head.addWidget(QLabel("选中一行预览", objectName="quietText"))
        records_layout.addLayout(records_head)
        self.records_table = self._table("recordsTable")
        self.records_table.setColumnCount(4)
        self.records_table.setHorizontalHeaderLabels(["序号", "清理后名称", "字数", "状态 / 原因"])
        self.records_table.horizontalHeader().setStretchLastSection(True)
        self.records_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.records_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.records_table.setMinimumHeight(96)
        records_layout.addWidget(self.records_table, 1)
        self._data_splitter.addWidget(records_group)
        self._soft_shadow(panel)
        return panel

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_data_panel", None) and event.type() == QEvent.Type.Resize and hasattr(self, "_data_splitter"):
            orientation = Qt.Orientation.Horizontal if event.size().width() >= 690 else Qt.Orientation.Vertical
            if self._data_splitter.orientation() != orientation:
                self._data_splitter.setOrientation(orientation)
                self._data_splitter.setSizes([340, 400] if orientation is Qt.Orientation.Horizontal else [260, 190])
                QTimer.singleShot(0, self._fit_workspace)
        if watched is getattr(self, "_job_panel", None) and event.type() == QEvent.Type.Resize and hasattr(self, "_metrics_layout"):
            direction = QBoxLayout.Direction.LeftToRight if event.size().width() >= 1100 else QBoxLayout.Direction.TopToBottom
            if self._metrics_layout.direction() != direction:
                self._metrics_layout.setDirection(direction)
                QTimer.singleShot(0, self._fit_workspace)
        return super().eventFilter(watched, event)

    def _fit_workspace(self) -> None:
        if not hasattr(self, "workspace_scroll"):
            return
        workspace = self.workspace_scroll.widget()
        workspace.layout().activate()
        workspace.resize(self.workspace_scroll.viewport().size().expandedTo(workspace.minimumSizeHint()).expandedTo(workspace.minimumSize()))

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame(objectName="panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self._section_heading("02", "印章校样", "1024 × 1024 RGBA"))
        self.preview_label = PreviewLabel()
        self.preview_label.setAccessibleName("印章预览")
        self.preview_label.setMinimumSize(220, 220)
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.preview_label, 1)
        self.preview_name_label = QLabel("尚未选择有效名称", objectName="previewName")
        self.preview_name_label.setWordWrap(True)
        layout.addWidget(self.preview_name_label)
        hint = QLabel("STZHONGS 固定字体  ·  8～16 字  ·  透明底", objectName="quietText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.save_current_button = QPushButton("保存当前印章", objectName="saveCurrentButton")
        self.save_current_button.setProperty("primary", True)
        self.save_current_button.setShortcut("Ctrl+S")
        layout.addWidget(self.save_current_button)
        self._soft_shadow(panel)
        return panel

    def _build_job_panel(self) -> QWidget:
        panel = QFrame(objectName="panel")
        self._job_panel = panel
        panel.installEventFilter(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(9)
        layout.addWidget(self._section_heading("03", "输出作业", "生成后立即落盘"))

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("保存文件夹"))
        self.folder_edit = QLineEdit(objectName="folderPath")
        self.folder_edit.setPlaceholderText("请先选择本地文件夹")
        self.folder_button = QPushButton("选择…", objectName="folderButton")
        self.folder_button.setShortcut("Ctrl+O")
        self.open_folder_button = QPushButton("打开文件夹", objectName="openFolderButton")
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.folder_button)
        folder_row.addWidget(self.open_folder_button)
        layout.addLayout(folder_row)

        metrics = QWidget()
        self._metrics_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, metrics)
        self._metrics_layout.setContentsMargins(0, 0, 0, 0)
        self._metrics_layout.setSpacing(9)
        source_group = QWidget()
        source_metrics = QHBoxLayout(source_group)
        source_metrics.setContentsMargins(0, 0, 0, 0)
        self.source_total_value = self._metric(source_metrics, "来源总数")
        self.source_blank_value = self._metric(source_metrics, "空白")
        self.source_duplicate_value = self._metric(source_metrics, "重复")
        self.source_invalid_length_value = self._metric(source_metrics, "字数不合规")
        self.source_valid_value = self._metric(source_metrics, "有效")
        source_metrics.addStretch()
        self._metrics_layout.addWidget(source_group)

        batch_group = QWidget()
        batch_metrics = QHBoxLayout(batch_group)
        batch_metrics.setContentsMargins(0, 0, 0, 0)
        self.batch_total_value = self._metric(batch_metrics, "作业总数")
        self.batch_processed_value = self._metric(batch_metrics, "已处理")
        self.batch_success_value = self._metric(batch_metrics, "成功")
        self.batch_failed_value = self._metric(batch_metrics, "失败")
        self.batch_skipped_value = self._metric(batch_metrics, "跳过")
        batch_metrics.addStretch()
        self.status_label = QLabel("就绪", objectName="jobStatus")
        batch_metrics.addWidget(self.status_label)
        self._metrics_layout.addWidget(batch_group)
        layout.addWidget(metrics)

        action_row = QHBoxLayout()
        self.batch_button = QPushButton("开始批量生成", objectName="batchButton")
        self.batch_button.setProperty("primary", True)
        self.batch_button.setShortcut("Ctrl+Return")
        self.cancel_button = QPushButton("取消作业", objectName="cancelButton")
        self.retry_button = QPushButton("重新生成失败项", objectName="retryButton")
        self.resume_button = QPushButton("继续未完成项", objectName="resumeButton")
        action_row.addWidget(self.batch_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.retry_button)
        action_row.addWidget(self.resume_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.progress_bar = QProgressBar(objectName="progressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("已处理 0 / 0")
        layout.addWidget(self.progress_bar)

        self.results_table = self._table("resultsTable")
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(
            ["源名称", "状态", "实际文件名", "完整路径", "原因"]
        )
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setMinimumHeight(88)
        layout.addWidget(self.results_table)
        self._soft_shadow(panel)
        return panel

    def _soft_shadow(self, widget: QWidget) -> None:
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(24)
        effect.setOffset(5, 7)
        effect.setColor(QColor(133, 111, 101, 38))
        widget.setGraphicsEffect(effect)

    def _table(self, name: str) -> QTableWidget:
        table = QTableWidget(objectName=name)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        return table

    def _metric(self, layout: QHBoxLayout, label: str) -> QLabel:
        block = QFrame(objectName="metric")
        block_layout = QHBoxLayout(block)
        block_layout.setContentsMargins(8, 4, 8, 4)
        block_layout.setSpacing(6)
        block_layout.addWidget(QLabel(label, objectName="metricLabel"))
        value = QLabel("0", objectName="metricValue")
        block_layout.addWidget(value)
        layout.addWidget(block)
        return value

    def _wire_events(self) -> None:
        self._manual_timer = QTimer(self)
        self._manual_timer.setSingleShot(True)
        self._manual_timer.setInterval(250)
        self._manual_timer.timeout.connect(lambda: self.refresh_manual_records())
        self.manual_input.textChanged.connect(self._manual_timer.start)
        self.source_tabs.currentChanged.connect(self._on_source_changed)
        self.import_button.clicked.connect(self.choose_import_file)
        self.sheet_combo.currentTextChanged.connect(self._reload_import)
        self.header_check.toggled.connect(self._reload_import)
        self.column_combo.currentIndexChanged.connect(self._on_column_changed)
        self.records_table.currentCellChanged.connect(self._on_record_selected)
        self.folder_button.clicked.connect(self.choose_folder)
        self.folder_edit.textChanged.connect(self._update_record_actions)
        self.open_folder_button.clicked.connect(self.open_folder)
        self.save_current_button.clicked.connect(self.save_current)
        self.batch_button.clicked.connect(self.start_batch)
        self.cancel_button.clicked.connect(self.cancel_batch)
        self.retry_button.clicked.connect(self.retry_failed)
        self.resume_button.clicked.connect(self.resume_pending)

    def refresh_manual_records(self) -> None:
        """Normalize the manual editor through the one shared records path."""
        self._manual_timer.stop()
        self.set_values(self.manual_input.toPlainText().splitlines())

    def _flush_manual_source(self) -> None:
        if self.source_tabs.currentIndex() == 0 and self._manual_timer.isActive():
            self.refresh_manual_records()

    def _on_source_changed(self, index: int) -> None:
        self._manual_timer.stop()
        if self._busy:
            return
        if index == 0:
            self.refresh_manual_records()
        elif self._import_table is not None and self.column_combo.currentIndex() >= 0:
            self._on_column_changed(self.column_combo.currentIndex())

    def set_values(self, values: list[object]) -> None:
        """Validate any source and refresh records, statistics, and preview."""
        source_values = list(values)
        self._source_total = len(source_values)
        self._source_blank = sum(
            value is None or not str(value).strip() for value in source_values
        )
        self.records = prepare_records(source_values, self._supported)
        self.last_summary = None
        self._run_base = None
        self._result_rows.clear()
        self._save_after_preview = None
        self.retry_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.records_table.blockSignals(True)
        self._populate_records()
        self._update_validation_counts()
        if self.records:
            self.records_table.setCurrentCell(0, 0)
        else:
            self._preview_token += 1
            self._preview_pending = None
            self.current_record = None
            self.current_image = None
            self.preview_name_label.setText("尚未选择有效名称")
            self.preview_label.show_reason("请输入或导入公司名称")
        self.records_table.blockSignals(False)
        if self.records:
            self.render_record(self.records[0])
        self.results_table.setRowCount(0)
        self._show_batch_snapshot(ProgressSnapshot(0, 0, 0, 0, 0, 0))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"已处理 0 / {len(self.records)}")
        self._update_record_actions()

    def _populate_records(self) -> None:
        self.records_table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            details = STATUS_TEXT[record.status]
            if record.reason:
                details = f"{details} · {record.reason}"
            values = (str(row + 1), record.cleaned, str(record.length), details)
            for column, value in enumerate(values):
                self.records_table.setItem(row, column, QTableWidgetItem(value))
        self.records_table.resizeColumnsToContents()

    def _update_validation_counts(self) -> None:
        valid = sum(record.status is RecordStatus.READY for record in self.records)
        duplicate = sum(record.reason == DUPLICATE_REASON for record in self.records)
        invalid_length = sum(record.reason == LENGTH_REASON for record in self.records)
        self.source_total_value.setText(str(self._source_total))
        self.source_blank_value.setText(str(self._source_blank))
        self.source_duplicate_value.setText(str(duplicate))
        self.source_invalid_length_value.setText(str(invalid_length))
        self.source_valid_value.setText(str(valid))

    def _on_record_selected(self, row: int, _column: int, *_args) -> None:
        if 0 <= row < len(self.records):
            self.render_record(self.records[row])

    def _render(self, name: str) -> Image:
        return render_seal(name, self._font_path)

    def render_record(self, record: NameRecord) -> None:
        """Coalesce selection changes; only the latest result may update widgets."""
        self._preview_token += 1
        self._preview_pending = None
        self._save_after_preview = None
        self.current_record = record
        self.current_image = None
        self.preview_name_label.setText(record.cleaned or "空名称")
        if record.status is not RecordStatus.READY:
            self.current_image = None
            self.preview_label.show_reason(record.reason or STATUS_TEXT[record.status])
            self._update_record_actions()
            return
        if record.cleaned in self._preview_cache:
            image = self._preview_cache[record.cleaned]
            self._preview_cache.move_to_end(record.cleaned)
            self.current_image = image
            self.preview_label.set_pillow_image(image)
            if not self._busy:
                self.status_label.setText("预览就绪")
        else:
            self.preview_label.clear()
            self.preview_label._source_pixmap = None
            self.preview_label.setText("正在生成预览…\n你可以继续选择名称")
            self._preview_pending = (self._preview_token, record.cleaned)
            self._start_preview()
        self._update_record_actions()

    def _start_preview(self) -> None:
        if self._preview_task is not None or self._preview_pending is None or self._close_pending:
            return
        token, name = self._preview_pending
        self._preview_pending = None
        renderer = self._render
        task = BackgroundTask(token, lambda: (name, renderer(name)))
        task.signals.completed.connect(self._on_preview_finished)
        self._preview_task = task
        QThreadPool.globalInstance().start(task)

    @Slot(int, object, str)
    def _on_preview_finished(self, token: int, result, error: str) -> None:
        self._preview_task = None
        if result is not None:
            name, image = result
            self._preview_cache[name] = image
            self._preview_cache.move_to_end(name)
            while len(self._preview_cache) > 12:
                self._preview_cache.popitem(last=False)
        if token == self._preview_token and not self._close_pending:
            if error:
                self.preview_label.show_reason(error)
                if not self._busy:
                    self.status_label.setText(f"预览失败：{error}")
                self._save_after_preview = None
            else:
                self.current_image = result[1]
                self.preview_label.set_pillow_image(self.current_image)
                if not self._busy:
                    self.status_label.setText("预览就绪")
                if self._save_after_preview is not None:
                    folder = self._save_after_preview
                    self._save_after_preview = None
                    self._save_current_to(folder)
        # A superseded result may now satisfy the newest request from cache.
        if self._preview_pending is not None and self._preview_pending[1] in self._preview_cache:
            token, name = self._preview_pending
            self._preview_pending = None
            if token == self._preview_token:
                self.current_image = self._preview_cache[name]
                self.preview_label.set_pillow_image(self.current_image)
                if self._save_after_preview is not None:
                    folder = self._save_after_preview
                    self._save_after_preview = None
                    self._save_current_to(folder)
        self._start_preview()
        self._update_record_actions()
        self._maybe_finish_close()

    def choose_import_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择公司名称表格",
            "",
            "表格文件 (*.xlsx *.xlsm *.xls *.csv)",
        )
        if selected:
            self.load_import_file(Path(selected))

    def load_import_file(self, path: Path) -> None:
        if self._busy:
            return
        self._manual_timer.stop()
        self._import_path = path
        self._queue_import(None)

    def _reload_import(self, *_args) -> None:
        if self._import_path is not None and not self._busy:
            self._queue_import(self.sheet_combo.currentText() or None)

    def _queue_import(self, sheet_name) -> None:
        self._manual_timer.stop()
        self.source_tabs.blockSignals(True)
        self.source_tabs.setCurrentIndex(1)
        self.source_tabs.blockSignals(False)
        self._import_token += 1
        self._import_pending = (self._import_token, self._import_path, self.header_check.isChecked(), sheet_name)
        self._importing = True
        self._save_after_preview = None
        self.status_label.setText("正在读取表格…")
        self._update_record_actions()
        self._start_import()

    def _start_import(self) -> None:
        if self._import_task is not None or self._import_pending is None or self._close_pending:
            return
        token, path, has_header, sheet_name = self._import_pending
        self._import_pending = None
        task = BackgroundTask(token, lambda: (path, read_import(path, has_header, sheet_name)))
        task.signals.completed.connect(self._on_import_finished)
        self._import_task = task
        QThreadPool.globalInstance().start(task)

    @Slot(int, object, str)
    def _on_import_finished(self, token: int, result, error: str) -> None:
        self._import_task = None
        if token == self._import_token and not self._close_pending:
            self._importing = False
            if error:
                self._show_error("无法导入表格", error)
            else:
                path, (names, selected, table) = result
                self.sheet_combo.blockSignals(True)
                self.sheet_combo.clear()
                self.sheet_combo.addItems(list(names))
                if selected:
                    self.sheet_combo.setCurrentText(selected)
                self.sheet_combo.setEnabled(bool(names))
                self.sheet_combo.blockSignals(False)
                self.set_import_table(table, path)
        self._start_import()
        self._update_record_actions()
        self._maybe_finish_close()

    def set_import_table(self, table: TableData, path: Path) -> None:
        self._manual_timer.stop()
        self._import_table = table
        self.import_path_edit.setText(str(path))
        self.import_preview.setColumnCount(len(table.headers))
        self.import_preview.setHorizontalHeaderLabels(list(table.headers))
        self.import_preview.setUpdatesEnabled(False)
        self.import_preview.setRowCount(min(100, len(table.rows)))
        for row_index, row in enumerate(table.rows[:100]):
            for column, value in enumerate(row):
                text = "" if value is None else str(value)
                self.import_preview.setItem(row_index, column, QTableWidgetItem(text))
        self.import_preview.resizeColumnsToContents()
        self.import_preview.setUpdatesEnabled(True)
        self.import_note.setText(f"共 {len(table.rows):,} 行 · 预览前 {min(100, len(table.rows))} 行 · 批量处理全部数据")
        self.column_combo.blockSignals(True)
        self.column_combo.clear()
        self.column_combo.addItems(list(table.headers))
        self.column_combo.setCurrentIndex(0 if table.headers else -1)
        self.column_combo.blockSignals(False)
        self.source_tabs.blockSignals(True)
        self.source_tabs.setCurrentIndex(1)
        self.source_tabs.blockSignals(False)
        if table.headers:
            self._on_column_changed(0)

    def _on_column_changed(self, index: int) -> None:
        if self._import_table is None or index < 0:
            return
        try:
            values = extract_column(self._import_table, index)
        except Exception as exc:
            self._show_error("无法读取所选列", str(exc))
            return
        self.set_values(values)

    def choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择印章保存文件夹", self.folder_edit.text()
        )
        if selected:
            self.folder_edit.setText(selected)

    def _folder(self, prompt: bool = True) -> Path | None:
        text = self.folder_edit.text().strip()
        if not text and prompt:
            self.choose_folder()
            text = self.folder_edit.text().strip()
        if not text:
            self.status_label.setText("请先选择保存文件夹")
            return None
        return Path(text)

    def save_current(self) -> None:
        self._flush_manual_source()
        if self._busy or self._importing:
            return
        if self.current_record is not None and self.current_record.status is RecordStatus.READY and self.current_image is None and (self._preview_task is not None or self._preview_pending is not None):
            token = self._preview_token
            folder = self._folder()
            if folder is not None and token == self._preview_token and self.current_image is not None:
                self._save_current_to(folder)
            elif folder is not None and token == self._preview_token and (self._preview_task is not None or self._preview_pending is not None):
                self._save_after_preview = folder
                self.status_label.setText("预览完成后自动保存当前印章…")
            return
        if self.current_record is None or self.current_image is None:
            if self.current_record is None:
                reason = "请先输入或选择一个印章名称"
            elif self.current_record.reason == LENGTH_REASON:
                reason = "印章名称须为 8～16 个字"
            else:
                reason = self.current_record.reason or "当前名称无法生成预览"
            self._show_error("无法生成", reason)
            return
        folder = self._folder()
        if folder is None:
            return
        self._save_current_to(folder)

    def _save_current_to(self, folder: Path) -> None:
        if self.current_record is None or self.current_image is None:
            return
        try:
            saved = save_png_atomic(
                self.current_image, folder, self.current_record.cleaned
            )
        except Exception as exc:
            self._show_error("保存失败", str(exc))
        else:
            self.status_label.setText(f"已保存：{saved.name}")
            self._append_result(
                self.current_record.original,
                "成功",
                saved.name,
                str(saved),
                "",
            )

    def start_batch(self) -> None:
        self._flush_manual_source()
        self._start_records(self.records)

    def _start_records(self, records: list[NameRecord], *, preserve_history: bool = False) -> None:
        if self._busy or self._importing or self._thread is not None:
            return
        folder = self._folder()
        if folder is None:
            return
        if not records:
            self._show_error("无法开始作业", "没有可处理的名称")
            return

        processor = BatchProcessor(lambda name: render_seal(name, self._font_path))
        thread = QThread(self)
        worker = BatchWorker(processor, records, folder)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.record_completed.connect(self._on_record_completed)
        worker.finished.connect(worker.deleteLater)
        worker.crashed.connect(worker.deleteLater)
        worker.finished.connect(self._on_batch_finished)
        worker.crashed.connect(self._on_batch_crashed)
        worker.finished.connect(thread.quit)
        worker.crashed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        self.cancel_requested.connect(
            worker.cancel, Qt.ConnectionType.QueuedConnection
        )

        self._processor = processor
        self._cancelling = False
        self._worker = worker
        self._thread = thread
        self._run_base = self.last_summary if preserve_history else None
        self._run_indices = {record.index for record in records}
        self._save_after_preview = None
        if self._run_base is not None:
            self._populate_results(self._run_base.records)
        else:
            self.results_table.setRowCount(0)
            self._result_rows.clear()
        self.last_summary = None
        self._show_batch_snapshot(self._cumulative_progress(
            ProgressSnapshot(
                len(records),
                sum(record.status is RecordStatus.READY for record in records),
                0,
                0,
                0,
                0,
            )
        ))
        self.status_label.setText("正在生成…")
        self._set_busy(True)
        thread.start()

    def _on_progress(self, snapshot: ProgressSnapshot) -> None:
        combined = self._cumulative_progress(snapshot)
        self._show_batch_snapshot(combined)
        self.status_label.setText(
            f"正在生成 · 成功 {combined.success} · 失败 {combined.failed}"
        )

    def _cumulative_progress(self, snapshot: ProgressSnapshot) -> ProgressSnapshot:
        if self._run_base is None:
            return snapshot
        untouched = [record for record in self._run_base.records if record.index not in self._run_indices]
        base = summary_snapshot(untouched)
        return ProgressSnapshot(len(self._run_base.records), base.valid + snapshot.valid,
                                base.processed + snapshot.processed, base.success + snapshot.success,
                                base.failed + snapshot.failed, base.skipped + snapshot.skipped)

    def _show_batch_snapshot(self, snapshot: ProgressSnapshot) -> None:
        percent = round(snapshot.processed * 100 / snapshot.total) if snapshot.total else 100
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"已处理 {snapshot.processed} / {snapshot.total}")
        self.batch_total_value.setText(str(snapshot.total))
        self.batch_processed_value.setText(str(snapshot.processed))
        self.batch_success_value.setText(str(snapshot.success))
        self.batch_failed_value.setText(str(snapshot.failed))
        self.batch_skipped_value.setText(str(snapshot.skipped))

    def _on_record_completed(
        self,
        record: NameRecord,
        snapshot: ProgressSnapshot,
        thread: QThread | None = None,
    ) -> None:
        if thread is not None and thread is not self._thread:
            return
        self._on_progress(snapshot)
        self._upsert_record(record)

    def _on_batch_finished(
        self, summary: BatchSummary, thread: QThread | None = None
    ) -> None:
        if thread is not None and thread is not self._thread:
            return
        if self._run_base is not None:
            summary = merge_summaries(self._run_base, summary)
            self._run_base = None
        self.last_summary = summary
        self._populate_results(summary.records)
        if summary.final_snapshot is not None:
            snapshot = summary.final_snapshot
        elif summary.cancelled:
            snapshot = ProgressSnapshot(
                len(summary.records),
                sum(record.status is RecordStatus.READY for record in summary.records),
                0,
                0,
                0,
                0,
            )
        else:
            success = sum(
                record.status is RecordStatus.SUCCESS for record in summary.records
            )
            failed = sum(
                record.status is RecordStatus.FAILED for record in summary.records
            )
            skipped = sum(
                record.status is RecordStatus.SKIPPED for record in summary.records
            )
            snapshot = ProgressSnapshot(
                len(summary.records),
                success + failed,
                success + failed + skipped,
                success,
                failed,
                skipped,
            )
        self._show_batch_snapshot(snapshot)
        self.status_label.setText("作业已取消" if summary.cancelled else "作业完成")
        if thread is None and self._thread is None:
            self._set_busy(False)

    def _on_batch_crashed(
        self, reason: str, thread: QThread | None = None
    ) -> None:
        if thread is not None and thread is not self._thread:
            return
        self.status_label.setText(f"后台作业失败：{reason}")
        self._show_error("后台作业失败", reason)
        if thread is None and self._thread is None:
            self._set_busy(False)

    def _on_thread_finished(self, thread: QThread | None = None) -> None:
        if thread is None:
            sender = self.sender()
            if not isinstance(sender, QThread):
                return
            thread = sender
        thread.deleteLater()
        if thread is not self._thread:
            return
        self._worker = None
        self._thread = None
        self._processor = None
        self._set_busy(False)
        self._maybe_finish_close()

    def cancel_batch(self) -> None:
        if not self._busy or self._processor is None:
            return
        # BatchProcessor uses a threading.Event, so cancellation is observable
        # even while the worker event loop is occupied by the blocking run call.
        self._processor.cancel()
        self._cancelling = True
        self.cancel_requested.emit()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在取消，当前图片保存后停止…")

    def retry_failed(self) -> None:
        self._manual_timer.stop()
        if self.last_summary is None:
            return
        retries = failed_records(self.last_summary)
        if retries:
            self._start_records(retries, preserve_history=True)

    def resume_pending(self) -> None:
        self._manual_timer.stop()
        if self.last_summary is not None:
            pending = pending_records(self.last_summary)
            if pending:
                self._start_records(pending, preserve_history=True)

    def open_folder(self) -> None:
        folder = self._folder(prompt=False)
        if folder is None:
            return
        if not folder.exists():
            self._show_error("无法打开文件夹", "所选文件夹不存在")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _populate_results(self, records: list[NameRecord]) -> None:
        self.results_table.setRowCount(0)
        self._result_rows.clear()
        for record in records:
            self._upsert_record(record)
        self.results_table.resizeColumnsToContents()

    def _upsert_record(self, record: NameRecord) -> None:
        row = self._result_rows.get(record.index)
        if row is None:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self._result_rows[record.index] = row
        values = (record.original, STATUS_TEXT[record.status], record.actual_filename,
                  str(record.saved_path) if record.saved_path else "", record.reason)
        for column, value in enumerate(values):
            self.results_table.setItem(row, column, QTableWidgetItem(value))

    def _append_result(
        self, source: str, status: str, filename: str, path: str, reason: str
    ) -> None:
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        for column, value in enumerate((source, status, filename, path, reason)):
            self.results_table.setItem(row, column, QTableWidgetItem(value))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.source_tabs.setEnabled(not busy)
        self.manual_input.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.column_combo.setEnabled(not busy and self.column_combo.count() > 0)
        self.folder_button.setEnabled(not busy)
        self.folder_edit.setEnabled(not busy)
        self.batch_button.setEnabled(not busy and bool(self.records))
        self.cancel_button.setEnabled(busy)
        self.retry_button.setEnabled(
            not busy
            and self.last_summary is not None
            and bool(failed_records(self.last_summary))
        )
        self.resume_button.setEnabled(not busy and self.last_summary is not None and bool(pending_records(self.last_summary)))
        self._update_record_actions()

    def _update_record_actions(self) -> None:
        available = not self._busy and not self._importing and not self._close_pending
        self.save_current_button.setEnabled(available)
        self.batch_button.setEnabled(available and bool(self.records))
        self.retry_button.setEnabled(available and self.last_summary is not None and bool(failed_records(self.last_summary)))
        self.resume_button.setEnabled(available and self.last_summary is not None and bool(pending_records(self.last_summary)))
        self.manual_input.setEnabled(available)
        self.source_tabs.tabBar().setEnabled(not self._busy and not self._importing)
        self.column_combo.setEnabled(available and self.column_combo.count() > 0)
        self.header_check.setEnabled(not self._busy)
        self.sheet_combo.setEnabled(not self._busy and self.sheet_combo.count() > 0)
        self.cancel_button.setEnabled(self._busy and not self._cancelling)
        self.open_folder_button.setEnabled(bool(self.folder_edit.text().strip()))

    def _show_error(self, title: str, message: str) -> None:
        self.status_label.setText(f"{title}：{message}")
        if self.isVisible():
            QMessageBox.warning(self, title, message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (self._thread is not None and self._thread.isRunning()) or self._preview_task is not None or self._import_task is not None:
            self._close_pending = True
            self._preview_pending = None
            self._import_pending = None
            self._save_after_preview = None
            if self._processor is not None:
                self._processor.cancel()
            self.status_label.setText("正在结束后台任务，完成后自动关闭…")
            event.ignore()
            return
        super().closeEvent(event)

    def _maybe_finish_close(self) -> None:
        if self._close_pending and self._thread is None and self._preview_task is None and self._import_task is None:
            self._close_pending = False
            QTimer.singleShot(0, self.close)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#workspace { background: #f0e7e0; color: #29272c; }
            QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #37343b; }
            QFrame#masthead { background: #f0e7e0; border: none; }
            QLabel#appTitle { color: #24232b; font-size: 26px; font-weight: 700; letter-spacing: 1px; }
            QLabel#appSubtitle { color: #77717a; font-size: 10px; letter-spacing: 1px; padding-top: 4px; }
            QLabel#localBadge { color: #4857ad; background: #ece7f0; border: 1px solid #faf6f5; border-radius: 15px; padding: 8px 16px; font-size: 11px; }
            QFrame#panel { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f8f0ea, stop:1 #f0e6df); border: 1px solid #fff9f4; border-radius: 20px; }
            QLabel#sectionIndex { color: #4c5bb6; background: #e7e3f0; border-radius: 9px; padding: 4px 7px; font-size: 11px; font-weight: 700; }
            QLabel#sectionTitle { font-size: 15px; font-weight: 700; padding-left: 3px; }
            QLabel#sectionHint, QLabel#quietText { color: #7b737b; font-size: 10px; }
            QLabel#subheading { font-weight: 700; font-size: 12px; }
            QLabel#previewName { color: #4b568f; background: #eee7e5; border: 1px solid #fff7f1; border-radius: 10px; padding: 9px 12px; font-size: 12px; }
            QLabel#previewLabel { color: #80767c; border: 1px solid #e0d6d0; border-radius: 14px; font-size: 13px; }
            QLabel#disclaimerLabel { color: #80767a; padding: 2px 5px; font-size: 10px; }
            QLabel#jobStatus { color: #4a58ab; font-size: 11px; font-weight: 600; }
            QFrame#metric { background: #eee5df; border: 1px solid #fcf5ef; border-radius: 9px; }
            QLabel#metricLabel { color: #817780; font-size: 10px; }
            QLabel#metricValue { color: #4a56aa; font-size: 16px; font-weight: 700; }
            QTabWidget::pane { border: 1px solid #e5dad4; border-radius: 12px; background: #eee5de; top: -1px; }
            QTabBar::tab { background: transparent; color: #897e85; padding: 8px 17px; margin: 0 5px 6px 0; border: 1px solid transparent; border-radius: 10px; }
            QTabBar::tab:selected { background: #faf3ed; color: #4958af; border: 1px solid #fffaf7; font-weight: 700; }
            QLineEdit, QPlainTextEdit, QComboBox, QTableWidget { background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 #e9dfd9, stop:0.25 #f0e7e0, stop:1 #f6eee7); border: 1px solid #ded2cb; border-bottom-color: #fff9f2; border-right-color: #fff9f2; border-radius: 9px; selection-background-color: #dce0f4; selection-color: #303c85; }
            QLineEdit, QComboBox { min-height: 28px; padding: 0 10px; font-size: 12px; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView { background: #faf2ec; border: 1px solid #ded3cc; selection-background-color: #e0e2f2; }
            QPlainTextEdit { padding: 9px; }
            QTableWidget { background: #f6eee8; alternate-background-color: #eee5df; gridline-color: transparent; font-size: 12px; }
            QTableWidget::item { padding: 3px; }
            QTableWidget::item:selected { background: #dfe1f1; color: #303d83; }
            QHeaderView::section { background: #eae0d9; color: #7b6f78; padding: 6px; border: none; font-size: 11px; font-weight: 600; }
            QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #fcf5ef, stop:1 #e9ded7); color: #645b66; border: 1px solid #fff9f3; border-right-color: #d8ccc5; border-bottom-color: #d8ccc5; border-radius: 10px; padding: 7px 13px; min-height: 18px; font-size: 12px; font-weight: 600; }
            QPushButton:hover { background: #fcf5ef; color: #4655aa; border-color: #c7c5df; }
            QPushButton:pressed { background: #e4dcd8; border-color: #c7bbb5; padding-top: 8px; padding-bottom: 6px; }
            QPushButton:focus { border: 1px solid #737fc3; }
            QPushButton[primary="true"] { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6370cb, stop:1 #4252b0); color: #ffffff; border-color: #7b87dc; border-right-color: #3a4897; border-bottom-color: #3a4897; }
            QPushButton[primary="true"]:hover { background: #5968c3; }
            QPushButton[primary="true"]:pressed { background: #3d4ca6; }
            QPushButton:disabled { background: #e9e0da; color: #aaa0a6; border-color: #eee6e1; }
            QProgressBar { background: #e4dad4; border: 1px solid #fff7ef; border-top-color: #d3c8c1; border-radius: 9px; text-align: center; min-height: 19px; font-size: 10px; color: #393550; }
            QProgressBar::chunk { background: #aeb7e4; border-radius: 8px; }
            QSplitter::handle { background: transparent; width: 18px; }
            QCheckBox { color: #796e78; font-size: 11px; spacing: 6px; }
            QCheckBox::indicator { width: 15px; height: 15px; border-radius: 5px; border: 1px solid #c7bbb4; background: #e9dfd8; }
            QCheckBox::indicator:checked { background: #5666bd; border: 3px solid #dce0f2; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 9px; background: #eee5df; margin: 2px; }
            QScrollBar::handle:vertical { background: #cfc3bf; border-radius: 3px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { height: 9px; background: #eee5df; margin: 2px; }
            QScrollBar::handle:horizontal { background: #cfc3bf; border-radius: 3px; min-width: 24px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            """
        )
