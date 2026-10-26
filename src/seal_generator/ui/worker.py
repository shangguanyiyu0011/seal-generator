"""Qt bridge for running the domain batch processor outside the GUI thread."""

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from seal_generator.importers import load_table, worksheet_names


class TaskSignals(QObject):
    completed = Signal(int, object, str)


class BackgroundTask(QRunnable):
    """A pool task that reports data only; all widgets stay on the GUI thread."""

    def __init__(self, token, operation):
        super().__init__()
        self.token = token
        self.operation = operation
        self.signals = TaskSignals()

    @Slot()
    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.completed.emit(self.token, None, str(exc))
        else:
            self.signals.completed.emit(self.token, result, "")


def read_import(path, has_header, sheet_name):
    names, active = worksheet_names(path)
    selected = sheet_name if sheet_name in names else active
    return names, selected, load_table(path, has_header=has_header, sheet_name=selected)


class BatchWorker(QObject):
    """Forward batch processor callbacks as thread-safe Qt signals."""

    progress = Signal(object)
    record_completed = Signal(object, object)
    finished = Signal(object)
    crashed = Signal(str)

    def __init__(self, processor, records, folder):
        super().__init__()
        self.processor = processor
        self.records = records
        self.folder = folder

    @Slot()
    def run(self) -> None:
        try:
            summary = self.processor.run(
                self.records,
                self.folder,
                self.progress.emit,
                self.record_completed.emit,
            )
        except Exception as exc:  # pragma: no cover - exact source is external I/O
            self.crashed.emit(str(exc))
        else:
            self.finished.emit(summary)

    @Slot()
    def cancel(self) -> None:
        self.processor.cancel()
