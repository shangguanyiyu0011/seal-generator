"""Safe, collision-free persistence for generated seal images."""

import errno
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image


TRANSLATION = str.maketrans('\\/:*?"<>|', '＼／：＊？＂＜＞｜')
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

_WINDOWS_LINK_FALLBACK_ERRNOS = {
    errno.EACCES,
    errno.ENOTSUP,
    errno.EPERM,
}
_WINDOWS_LINK_FALLBACK_WINERRORS = {
    1,  # ERROR_INVALID_FUNCTION (for example, FAT/exFAT)
    5,  # ERROR_ACCESS_DENIED (for example, restricted shares)
    50,  # ERROR_NOT_SUPPORTED
    1314,  # ERROR_PRIVILEGE_NOT_HELD
}


def _can_fallback_from_hard_link(error: OSError) -> bool:
    return os.name == "nt" and (
        error.errno in _WINDOWS_LINK_FALLBACK_ERRNOS
        or getattr(error, "winerror", None) in _WINDOWS_LINK_FALLBACK_WINERRORS
    )


def _rename_no_replace_windows(source: Path, destination: Path) -> None:
    """Atomically move *source* on Windows, failing if *destination* exists."""
    if os.name != "nt":
        raise OSError(errno.ENOTSUP, "no no-clobber rename fallback on this platform")
    # Unlike POSIX rename, the Windows operation used by os.rename never replaces
    # an existing destination. Both paths are in the same directory here.
    os.rename(source, destination)


def sanitise_filename(name: str) -> str:
    """Return a Windows-safe filename stem without discarding its readable text."""
    safe = name.translate(TRANSLATION).rstrip(" .") or "未命名"
    if safe.split(".", 1)[0].upper() in RESERVED:
        return f"_{safe}"
    return safe


def next_available_path(folder: Path, name: str) -> Path:
    """Find the first unused PNG path, adding a numeric suffix when necessary."""
    base = sanitise_filename(name)
    candidate = folder / f"{base}.png"
    index = 2
    while candidate.exists():
        candidate = folder / f"{base}_{index}.png"
        index += 1
    return candidate


def save_png_atomic(image: Image.Image, folder: Path, name: str) -> Path:
    """Encode a PNG to a temp file and atomically publish it in *folder*."""
    folder.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=folder, prefix=".seal-", suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            image.save(handle, format="PNG")
            handle.flush()
            os.fsync(handle.fileno())

        base = sanitise_filename(name)
        index = 1
        while True:
            suffix = "" if index == 1 else f"_{index}"
            candidate = folder / f"{base}{suffix}.png"
            try:
                os.link(temp_path, candidate)
                return candidate
            except FileExistsError:
                index += 1
            except OSError as error:
                if not _can_fallback_from_hard_link(error):
                    raise
                try:
                    _rename_no_replace_windows(temp_path, candidate)
                    return candidate
                except FileExistsError:
                    index += 1
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
