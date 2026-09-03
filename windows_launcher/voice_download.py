"""Download a whisper.cpp GGML model with progress, off the GUI thread.

pywhispercpp downloads a missing model itself on first use, but silently and
with no way to show progress. This controller does the same fetch (same URL,
same ``~/.cache/pywhispercpp/`` destination, atomic rename) while emitting Qt
signals a dialog or the overlay can render.

``voice_capture.model_downloader`` has the URL registry and a downloader, but
that one prints to stdout and then blocks on a load-verify -- not what a GUI
wants -- so only the registry is reused.
"""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

__all__ = ["ModelDownloadController", "model_is_downloaded", "cache_path"]


def _cache_dir() -> Path:
    try:
        from voice_capture.model_downloader import PYWHISPERCPP_CACHE

        return Path(PYWHISPERCPP_CACHE)
    except Exception:  # noqa: BLE001
        return Path.home() / ".cache" / "pywhispercpp"


def cache_path(model_name: str) -> Path:
    return _cache_dir() / f"ggml-{model_name}.bin"


def model_is_downloaded(model_name: str) -> bool:
    try:
        return cache_path(model_name).is_file()
    except Exception:  # noqa: BLE001
        return False


def _url_for(model_name: str) -> Optional[str]:
    try:
        from voice_capture.model_downloader import MODEL_REGISTRY

        info = MODEL_REGISTRY.get(model_name)
        return info.get("url") if info else None
    except Exception:  # noqa: BLE001
        return None


class ModelDownloadController(QObject):
    """Fetch one GGML model on a worker thread, reporting progress 0..100."""

    progress = Signal(int)
    finished = Signal(str)          # model name
    failed = Signal(str)            # message
    busy_changed = Signal(bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._busy = False
        self._thread: Optional[threading.Thread] = None

    @property
    def busy(self) -> bool:
        return self._busy

    def download(self, model_name: str) -> None:
        if self._busy:
            return
        if model_is_downloaded(model_name):
            self.finished.emit(model_name)
            return
        url = _url_for(model_name)
        if not url:
            self.failed.emit(f"No download URL for {model_name!r}.")
            return
        self._busy = True
        self.busy_changed.emit(True)
        self._thread = threading.Thread(
            target=self._run, args=(model_name, url), name="voice-model-dl", daemon=True
        )
        self._thread.start()

    # -- worker ----------------------------------------------------------------

    def _run(self, model_name: str, url: str) -> None:
        dest = cache_path(model_name)
        tmp = dest.with_suffix(dest.suffix + f".part{id(self) & 0xffff:x}")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)

            def hook(block_num: int, block_size: int, total: int) -> None:
                if total > 0:
                    pct = max(0, min(100, int(block_num * block_size * 100 / total)))
                    self.progress.emit(pct)

            urllib.request.urlretrieve(url, tmp, reporthook=hook)
            tmp.replace(dest)
            self.progress.emit(100)
            self.finished.emit(model_name)
        except Exception as exc:  # noqa: BLE001
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            self.failed.emit(str(exc))
        finally:
            self._busy = False
            self.busy_changed.emit(False)
