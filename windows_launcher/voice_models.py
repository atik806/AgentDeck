"""Whisper model selection helpers for the voice-to-text overlay.

``voice_engine`` used to hard-code ``tiny.en`` -- the smallest, least accurate
whisper.cpp model. This module lets the app pick a sensible model for the
machine (``"auto"``) and validates an explicit choice against the registry that
lives in the sibling ``voice_capture`` project.

Kept deliberately import-light: ``voice_capture`` may be un-importable on a
machine that only installed the panel's core deps, so ``MODEL_REGISTRY`` is
looked up lazily and every path has a static fallback.
"""

from __future__ import annotations

import ctypes
import os
from typing import Dict, Tuple

__all__ = ["recommend_model", "resolve", "MODEL_LABELS", "DEFAULT_PROMPT"]

# whisper.cpp does better on command dictation when it knows roughly what
# vocabulary to expect. Passed as ``initial_prompt`` unless the user set their
# own in ``voice_initial_prompt``.
DEFAULT_PROMPT = (
    "Transcript of spoken terminal commands and code. Expect words like: git, "
    "GitHub, npm, pnpm, yarn, cd, ls, grep, ripgrep, sudo, chmod, curl, ssh, "
    "Docker, kubectl, Kubernetes, Python, TypeScript, JavaScript, React, Node, "
    "async, await, const, let, return, import, export, stdout, stderr, "
    "localhost, API, JSON, YAML, regex, commit, rebase, branch, merge."
)

# name -> (human label, download size). Only the models worth offering; the
# full registry (quantized / large / multilingual variants) stays in
# voice_capture.model_downloader.
MODEL_LABELS: Dict[str, Tuple[str, str]] = {
    "auto": ("Auto (recommended for this machine)", ""),
    "tiny.en": ("Tiny (English) - fastest, least accurate", "75 MB"),
    "base.en": ("Base (English) - fast, good accuracy", "142 MB"),
    "small.en": ("Small (English) - slower, best accuracy", "466 MB"),
    "tiny": ("Tiny (multilingual)", "75 MB"),
    "base": ("Base (multilingual)", "142 MB"),
    "small": ("Small (multilingual)", "466 MB"),
}

_FALLBACK = "base.en"


def _total_ram_gb() -> float:
    """Physical RAM in GiB, or 0.0 if it can't be probed."""
    try:  # Windows
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullTotalPhys / (1024 ** 3)
    except Exception:  # noqa: BLE001 - any probe failure -> use the fallback
        pass
    try:  # POSIX (dev machines / CI)
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except (AttributeError, ValueError, OSError):
        return 0.0


def recommend_model() -> str:
    """Pick an English whisper.cpp model that suits this machine.

    ``medium.en`` is intentionally never chosen -- it is far too slow on CPU
    for interactive, review-before-Enter dictation.
    """
    ram = _total_ram_gb()
    cores = os.cpu_count() or 2
    if ram <= 0.0:
        return _FALLBACK
    if ram < 6 or cores <= 2:
        return "base.en"
    return "small.en"


def _registry() -> Dict[str, dict]:
    try:
        from voice_capture.model_downloader import MODEL_REGISTRY  # type: ignore

        return dict(MODEL_REGISTRY)
    except Exception:  # noqa: BLE001
        return {name: {} for name in MODEL_LABELS if name != "auto"}


def resolve(name: str | None) -> str:
    """Turn a ``voice_model`` config value into a concrete model name."""
    name = (name or "auto").strip()
    if name == "auto":
        return recommend_model()
    if name in _registry():
        return name
    print(f"[WARN] Unknown voice_model {name!r}; using {_FALLBACK!r}.")
    return _FALLBACK
