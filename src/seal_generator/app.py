"""Application entry points for GUI launch and deployment verification."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from seal_generator.renderer import render_seal
from seal_generator.resources import font_path, load_supported_codepoints
from seal_generator.storage import save_png_atomic
from seal_generator.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="印章生成器")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--gui-smoke-test", action="store_true")
    parser.add_argument("--generate-samples", type=Path)
    args = parser.parse_args(argv)

    if args.smoke_test:
        font = font_path()
        render_seal("印章企业科技有限公司", font)
        return 0

    if args.generate_samples:
        font = font_path()
        for length in range(8, 17):
            save_png_atomic(
                render_seal("印" * length, font),
                args.generate_samples,
                f"{length}字印章样例",
            )
        return 0

    if args.gui_smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    try:
        app = QApplication.instance() or QApplication([sys.argv[0]])
    except Exception as exc:
        print(f"印章生成器无法启动：无法创建图形应用：{exc}", file=sys.stderr)
        return 1

    try:
        font = font_path()
        load_supported_codepoints(font)
    except Exception as exc:
        message = f"固定字体加载失败：{exc}"
        if args.gui_smoke_test:
            print(f"印章生成器无法启动：{message}", file=sys.stderr)
        else:
            QMessageBox.critical(None, "印章生成器无法启动", message)
        return 1

    try:
        window = MainWindow()
    except Exception as exc:
        message = f"主界面创建失败：{exc}"
        if args.gui_smoke_test:
            print(f"印章生成器无法启动：{message}", file=sys.stderr)
        else:
            QMessageBox.critical(None, "印章生成器无法启动", message)
        return 1

    if args.gui_smoke_test:
        app.processEvents()
        window.close()
        app.processEvents()
        return 0

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
