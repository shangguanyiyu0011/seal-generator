"""Shared pytest configuration for seal-generator tests."""
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication


@pytest.fixture(autouse=True)
def finish_background_jobs(qtbot):
    """Drain real workers before pytest-qt deletes their receiving windows."""
    yield
    qtbot.waitUntil(lambda: QThreadPool.globalInstance().activeThreadCount() == 0, timeout=30000)
    QApplication.processEvents()
    # A completed superseded task can schedule the latest pending selection.
    qtbot.waitUntil(lambda: QThreadPool.globalInstance().activeThreadCount() == 0, timeout=30000)
    QApplication.processEvents()
