import os
from pathlib import Path
from types import SimpleNamespace

import seal_generator.resources as resources
import seal_generator.app as app_module
import pytest

from seal_generator.resources import font_path, load_supported_codepoints, resource_path


def test_bundled_font_is_the_only_expected_font():
    path = font_path()
    assert path.name == "STZHONGS.TTF"
    assert path.is_file()
    assert resource_path("fonts/STZHONGS.TTF") == path


def test_font_contains_common_company_name_characters():
    codepoints = load_supported_codepoints(font_path())
    assert all(ord(char) in codepoints for char in "\u4e0a\u6d77\u6709\u9650\u516c\u53f8\u79d1\u6280\u80a1\u4efd")


def test_resource_path_uses_pyinstaller_resource_directory_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "_MEIPASS", tmp_path, raising=False)

    assert resources.resource_path("fonts/STZHONGS.TTF") == tmp_path / "resources" / "fonts" / "STZHONGS.TTF"


def test_missing_font_reports_a_readable_fixed_font_message(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing-font.ttf"
    monkeypatch.setattr(resources, "resource_path", lambda relative: missing_path)

    with pytest.raises(FileNotFoundError) as exc_info:
        resources.font_path()

    assert str(exc_info.value) == f"\u56fa\u5b9a\u5b57\u4f53\u4e0d\u5b58\u5728\uff1a{missing_path}"


def test_corrupt_font_reports_a_readable_chinese_message(tmp_path):
    corrupt = tmp_path / "corrupt.ttf"
    corrupt.write_bytes(b"not a font")
    resources.load_supported_codepoints.cache_clear()

    with pytest.raises(RuntimeError, match="固定字体.*损坏"):
        resources.load_supported_codepoints(corrupt)


class _FakeApplication:
    _instance = None
    events: list[str] = []

    def __init__(self, _argv):
        type(self)._instance = self
        type(self).events.append("app")

    @classmethod
    def instance(cls):
        return cls._instance

    def processEvents(self):
        type(self).events.append("events")

    def exec(self):
        type(self).events.append("exec")
        return 0


def _install_fake_application(monkeypatch):
    _FakeApplication._instance = None
    _FakeApplication.events = []
    monkeypatch.setattr(app_module, "QApplication", _FakeApplication)
    return _FakeApplication.events


def test_gui_launch_creates_application_before_font_validation_and_reports_failure(
    monkeypatch,
):
    events = _install_fake_application(monkeypatch)
    messages = []
    monkeypatch.setattr(
        app_module,
        "font_path",
        lambda: events.append("font") or (_ for _ in ()).throw(FileNotFoundError("字体丢失")),
    )
    monkeypatch.setattr(
        app_module,
        "QMessageBox",
        SimpleNamespace(critical=lambda parent, title, message: messages.append((title, message))),
        raising=False,
    )

    try:
        result = app_module.main([])
    except FileNotFoundError:
        result = None

    assert result == 1
    assert events[:2] == ["app", "font"]
    assert messages == [("印章生成器无法启动", "固定字体加载失败：字体丢失")]


def test_gui_launch_reports_main_window_construction_failure(monkeypatch):
    events = _install_fake_application(monkeypatch)
    messages = []
    monkeypatch.setattr(app_module, "font_path", lambda: events.append("font") or Path("font.ttf"))
    monkeypatch.setattr(
        app_module,
        "load_supported_codepoints",
        lambda path: events.append("cmap") or frozenset(),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "MainWindow",
        lambda: events.append("window") or (_ for _ in ()).throw(RuntimeError("界面损坏")),
    )
    monkeypatch.setattr(
        app_module,
        "QMessageBox",
        SimpleNamespace(critical=lambda parent, title, message: messages.append((title, message))),
        raising=False,
    )

    try:
        result = app_module.main([])
    except RuntimeError:
        result = None

    assert result == 1
    assert events == ["app", "font", "cmap", "window"]
    assert messages == [("印章生成器无法启动", "主界面创建失败：界面损坏")]


def test_gui_smoke_test_uses_offscreen_and_constructs_window_without_event_loop(
    monkeypatch,
):
    events = _install_fake_application(monkeypatch)

    class Window:
        def __init__(self):
            events.append("window")

        def close(self):
            events.append("close")

    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(app_module, "font_path", lambda: events.append("font") or Path("font.ttf"))
    monkeypatch.setattr(
        app_module,
        "load_supported_codepoints",
        lambda path: events.append("cmap") or frozenset({ord("印")}),
        raising=False,
    )
    monkeypatch.setattr(app_module, "MainWindow", Window)

    try:
        result = app_module.main(["--gui-smoke-test"])
    except SystemExit:
        result = None

    assert result == 0
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    assert events == ["app", "font", "cmap", "window", "events", "close", "events"]
