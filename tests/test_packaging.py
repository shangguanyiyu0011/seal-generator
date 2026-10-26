from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_bundles_only_required_runtime_resource():
    source = (PROJECT_ROOT / "seal_generator.spec").read_text(encoding="utf-8")

    compile(source, "seal_generator.spec", "exec")
    assert '"src/seal_generator/app.py"' in source
    assert '"resources/fonts/STZHONGS.TTF"' in source
    assert '"resources/fonts"' in source
    assert "resources/reference" not in source
    assert 'name="印章生成器"' in source
    assert "console=False" in source
    compact = "".join(source.split())
    assert "collect_submodules" not in source
    assert "hiddenimports=[]" in compact
    assert 'excludes=["charset_normalizer"]' in compact


def test_build_script_verifies_every_native_step_and_packaged_executable():
    path = PROJECT_ROOT / "build.ps1"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    source = path.read_text(encoding="utf-8-sig")

    assert "py" in source
    assert "@('-3.14', '-3.13', '-3.12')" in source
    assert source.index("'-3.14'") < source.index("'-3.13'") < source.index("'-3.12'")
    assert "sys.maxsize > 2**32" in source
    assert "WindowsApps" in source
    assert "requirements-dev.txt" in source
    assert "@('-m', 'pytest', '-v')" in source
    assert "@('-m', 'PyInstaller', '--noconfirm', '--clean', 'seal_generator.spec')" in source
    assert "dist\\印章生成器\\印章生成器.exe" in source
    assert "--smoke-test" in source
    assert "--gui-smoke-test" in source
    assert source.index("--smoke-test") < source.index("--gui-smoke-test")
    assert "Start-Process" in source
    assert "-Wait" in source and "-PassThru" in source
    assert ".ExitCode" in source
    assert "打包程序冒烟测试退出码" in source
    assert "$LASTEXITCODE" in source
    assert "构建完成：dist\\印章生成器\\印章生成器.exe" in source


def test_runtime_requirements_do_not_include_charset_normalizer():
    runtime = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    development = (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "charset-normalizer" not in runtime.lower()
    assert "charset-normalizer" not in development.lower()


def test_readme_documents_reproducible_source_build_and_portable_use():
    source = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    required_phrases = (
        "python -m pip install -r requirements-dev.txt",
        "python -m seal_generator.app",
        "powershell -ExecutionPolicy Bypass -File .\\build.ps1",
        "dist\\印章生成器\\印章生成器.exe",
        "完整复制",
        "8～16",
        "STZHONGS.TTF",
        "py -3.14`、`py -3.13`、`py -3.12",
        "64 位",
        "32 位",
        "透明",
        "不会覆盖",
        "取消",
        "重试",
        "本机",
        "不具备电子签章、身份认证或法律效力",
        "--smoke-test",
        "--gui-smoke-test",
    )
    for phrase in required_phrases:
        assert phrase in source

    verification_commands = (
        "python -m seal_generator.app --smoke-test",
        "python -m seal_generator.app --gui-smoke-test",
        ".\\dist\\印章生成器\\印章生成器.exe --smoke-test",
        ".\\dist\\印章生成器\\印章生成器.exe --gui-smoke-test",
    )
    for command in verification_commands:
        assert command in source
