import errno
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from PIL import Image
import pytest

import seal_generator.storage as storage_module
from seal_generator.storage import (
    next_available_path,
    sanitise_filename,
    save_png_atomic,
)


def test_sanitises_windows_characters_reserved_names_and_tail():
    assert sanitise_filename('公司/A:B*?"<测试>|. ') == "公司／A：B＊？＂＜测试＞｜"
    assert sanitise_filename("CON") == "_CON"


@pytest.mark.parametrize("name", ("CON.txt", "nul.PnG", "LPT9.csv", "com1."))
def test_reserved_device_stems_remain_safe_when_an_extension_is_present(name):
    assert sanitise_filename(name).startswith("_")


@pytest.mark.parametrize("name", ("", "   ", "...", " . . "))
def test_blank_or_dot_only_names_use_readable_fallback(name):
    assert sanitise_filename(name) == "未命名"


def test_collision_suffix_never_overwrites(tmp_path):
    (tmp_path / "印章企业科技有限公司.png").touch()
    (tmp_path / "印章企业科技有限公司_2.png").touch()

    assert next_available_path(tmp_path, "印章企业科技有限公司").name == "印章企业科技有限公司_3.png"


def test_atomic_save_produces_valid_png_and_no_temp_file(tmp_path):
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))

    saved = save_png_atomic(image, tmp_path, "印章企业科技有限公司")

    assert saved.is_file()
    with Image.open(saved) as reopened:
        assert reopened.size == (1024, 1024)
        reopened.verify()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_save_flushes_and_fsyncs_temp_before_publish(monkeypatch, tmp_path):
    image = Image.new("RGBA", (16, 16), (230, 0, 18, 255))
    fsynced = []
    real_fsync = storage_module.os.fsync

    def recording_fsync(file_descriptor):
        fsynced.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(storage_module.os, "fsync", recording_fsync)

    saved = save_png_atomic(image, tmp_path, "同步印章")

    assert saved.is_file()
    assert len(fsynced) == 1


@pytest.mark.skipif(
    storage_module.os.name != "nt",
    reason="the no-clobber rename fallback is specific to Windows",
)
def test_atomic_save_falls_back_when_hard_links_are_unsupported_without_overwrite(
    monkeypatch, tmp_path
):
    existing = tmp_path / "兼容印章.png"
    existing.write_bytes(b"pre-existing final")
    image = Image.new("RGBA", (16, 16), (230, 0, 18, 255))

    def unsupported_link(source, destination):
        raise OSError(errno.ENOTSUP, "hard links unsupported")

    monkeypatch.setattr(storage_module.os, "link", unsupported_link)

    saved = save_png_atomic(image, tmp_path, "兼容印章")

    assert saved.name == "兼容印章_2.png"
    assert existing.read_bytes() == b"pre-existing final"
    with Image.open(saved) as reopened:
        reopened.verify()
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_atomic_saves_retry_real_link_collisions(monkeypatch, tmp_path):
    first_candidate_barrier = Barrier(8)
    real_link = storage_module.os.link

    def synchronized_link(source, destination):
        if destination.name == "并发印章.png":
            first_candidate_barrier.wait(timeout=5)
        return real_link(source, destination)

    monkeypatch.setattr(storage_module.os, "link", synchronized_link)

    def save_one(index):
        image = Image.new("RGBA", (16, 16), (index, 0, 0, 255))
        return save_png_atomic(image, tmp_path, "并发印章")

    with ThreadPoolExecutor(max_workers=8) as pool:
        saved = list(pool.map(save_one, range(8)))

    assert len(set(saved)) == 8
    assert {path.name for path in saved} == {
        "并发印章.png",
        *(f"并发印章_{index}.png" for index in range(2, 9)),
    }
    for path in saved:
        with Image.open(path) as reopened:
            reopened.verify()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_save_cleans_temp_on_encoder_failure(tmp_path):
    class BrokenImage:
        def save(self, *args, **kwargs):
            raise OSError("disk failure")

    with pytest.raises(OSError, match="disk failure"):
        save_png_atomic(BrokenImage(), tmp_path, "印章企业科技有限公司")

    assert not list(tmp_path.iterdir())


def test_atomic_save_failure_preserves_preexisting_final(tmp_path):
    existing = tmp_path / "印章企业科技有限公司.png"
    existing.write_bytes(b"pre-existing final")

    class BrokenImage:
        def save(self, *args, **kwargs):
            raise OSError("disk failure")

    with pytest.raises(OSError, match="disk failure"):
        save_png_atomic(BrokenImage(), tmp_path, "印章企业科技有限公司")

    assert existing.read_bytes() == b"pre-existing final"
    assert list(tmp_path.iterdir()) == [existing]


def test_atomic_save_never_exposes_final_file_during_failed_encode(tmp_path):
    encoding_started = Event()
    allow_failure = Event()

    class BlockingBrokenImage:
        def save(self, *args, **kwargs):
            encoding_started.set()
            assert allow_failure.wait(timeout=5)
            raise OSError("disk failure")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(save_png_atomic, BlockingBrokenImage(), tmp_path, "阻塞印章")
        assert encoding_started.wait(timeout=5)
        assert not list(tmp_path.glob("*.png"))
        allow_failure.set()
        with pytest.raises(OSError, match="disk failure"):
            future.result(timeout=5)

    assert not list(tmp_path.iterdir())


def test_atomic_save_cleans_temp_and_candidate_when_link_publish_fails(
    monkeypatch, tmp_path
):
    image = Image.new("RGBA", (16, 16), (230, 0, 18, 255))

    def fail_link(source, destination):
        raise OSError("link failure")

    monkeypatch.setattr(storage_module.os, "link", fail_link)

    with pytest.raises(OSError, match="link failure"):
        save_png_atomic(image, tmp_path, "印章企业科技有限公司")

    assert not list(tmp_path.iterdir())
