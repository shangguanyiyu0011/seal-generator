# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the Windows desktop application."""

from pathlib import Path
import os
import sys

PROJECT_ROOT = Path(SPECPATH).resolve()
SOURCE_ROOT = PROJECT_ROOT / "src"
APP_ENTRY = PROJECT_ROOT / "src/seal_generator/app.py"
FONT_SOURCE = PROJECT_ROOT / "resources/fonts/STZHONGS.TTF"

# A host PATH can include PDF/image toolchains with incompatible copies of ICU
# and the Windows CRT. Analyse only against this Python and Windows; package
# hooks add PySide6/Pillow's own library directories explicitly. This affects
# this build process only, never the user's persisted environment.
_original_build_path = os.environ.get("PATH", "")
_windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
os.environ["PATH"] = os.pathsep.join(str(path) for path in (
    Path(sys.executable).parent,
    Path(sys.base_prefix),
    Path(sys.base_prefix) / "Scripts",
    _windows / "System32",
    _windows,
))

a = Analysis(
    [str(APP_ENTRY)],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=[(str(FONT_SOURCE), "resources/fonts")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["charset_normalizer"],
    noarchive=False,
)
os.environ["PATH"] = _original_build_path
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="印章生成器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="印章生成器",
)
