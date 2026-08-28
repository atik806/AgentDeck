"""
Whisper model download helper.

pywhispercpp auto-downloads models on first use, but this module provides
manual control for pre-downloading, listing available models,
and verifying model files.
"""

import hashlib
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Known model information
# ---------------------------------------------------------------------------

# Source: https://huggingface.co/ggerganov/whisper.cpp
# These are the officially supported whisper.cpp GGML models.
MODEL_REGISTRY: Dict[str, Dict[str, str]] = {
    # English-only models (slightly better accuracy for English)
    "tiny.en": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
        "size": "75 MiB",
        "sha256": "c78c86eb1a8faa21b369bcd33207cc90d64ae9df",
    },
    "base.en": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
        "size": "142 MiB",
        "sha256": "137c3aabf9552327ebdf2b8e7e7bff85bd27b5e0",
    },
    "small.en": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin",
        "size": "466 MiB",
        "sha256": "4112428db90d1a32e99b1317e52db0cf9e3df974",
    },
    "medium.en": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin",
        "size": "1.5 GiB",
        "sha256": "8c30f0e44ce9560643ebd10bbe50cd20eafd3723",
    },

    # Multilingual models
    "tiny": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
        "size": "75 MiB",
        "sha256": "bd577a113a864445d4c299885e0cb97d4ba92b5f",
    },
    "base": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
        "size": "142 MiB",
        "sha256": "465707469ff3a37a2b9b8d8f89f2f99de7299dac",
    },
    "small": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
        "size": "466 MiB",
        "sha256": "b6fc2ae7a7e56dd3253abac7cfe2c0c7e25a1b91",
    },
    "medium": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
        "size": "1.5 GiB",
        "sha256": "f7762106a9db2b086fa1f15816ef613b6041715f",
    },
    "large-v3": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
        "size": "2.9 GiB",
        "sha256": "ad82bf6a9043ceed055076d0fd39f5f186ff8062",
    },
    "large-v3-turbo": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
        "size": "1.5 GiB",
        "sha256": "4af2b29d7ec73d781377bfd1758ca957a807e941",
    },

    # Quantized models (smaller, slightly less accurate)
    "tiny-q5_1": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-q5_1.bin",
        "size": "31 MiB",
    },
    "tiny.en-q5_1": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en-q5_1.bin",
        "size": "31 MiB",
    },
    "base-q5_1": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base-q5_1.bin",
        "size": "56 MiB",
    },
    "base.en-q5_1": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en-q5_1.bin",
        "size": "56 MiB",
    },
    "large-v3-turbo-q5_0": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        "size": "547 MiB",
    },
}

# VAD models (from ggml-org/whisper-vad)
VAD_MODELS: Dict[str, Dict[str, str]] = {
    "silero-v5.1.2": {
        "url": "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin",
        "size": "885 KiB",
    },
    "silero-v6.2.0": {
        "url": "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
        "size": "885 KiB",
    },
}

# Default pywhispercpp cache location
PYWHISPERCPP_CACHE = Path.home() / ".cache" / "pywhispercpp"


# ---------------------------------------------------------------------------
# Model path resolution
# ---------------------------------------------------------------------------

def get_model_path(model_name: str = "tiny.en") -> Path:
    """
    Resolve the path to a GGML model file.

    pywhispercpp auto-downloads models to ~/.cache/pywhispercpp/.
    This function checks there first, then falls back to our models dir.

    Args:
        model_name: Name of the model (e.g., "tiny.en", "base", etc.)

    Returns:
        Path to the model file if it exists.

    Raises:
        FileNotFoundError: If the model file is not found locally.
    """
    # Check pywhispercpp cache first
    py_path = PYWHISPERCPP_CACHE / f"ggml-{model_name}.bin"
    if py_path.exists():
        return py_path

    # Check our own models directory
    our_path = Path.home() / ".cache" / "voice_capture" / "models" / f"ggml-{model_name}.bin"
    if our_path.exists():
        return our_path

    # Check models/ subdirectory of project
    local_path = Path("models") / f"ggml-{model_name}.bin"
    if local_path.exists():
        return local_path.resolve()

    raise FileNotFoundError(
        f"Model '{model_name}' not found locally.\n"
        f"Search paths:\n"
        f"  {py_path}\n"
        f"  {our_path}\n"
        f"  {local_path}\n"
        f"Run 'python -m voice_capture.model_downloader --download {model_name}' to download."
    )


def list_available_models() -> List[str]:
    """Return list of available model names."""
    return sorted(MODEL_REGISTRY.keys())


def list_downloaded_models() -> List[str]:
    """Return list of models that exist locally."""
    downloaded = []
    for name in MODEL_REGISTRY:
        try:
            get_model_path(name)
            downloaded.append(name)
        except FileNotFoundError:
            pass
    return downloaded


# ---------------------------------------------------------------------------
# Download functions
# ---------------------------------------------------------------------------

def _progress_hook(block_num: int, block_size: int, total_size: int) -> None:
    """Report download progress."""
    downloaded = block_num * block_size
    if total_size > 0:
        percent = min(100, downloaded * 100 // total_size)
        bar_len = 40
        filled = bar_len * percent // 100
        bar = "=" * filled + "-" * (bar_len - filled)
        sys.stdout.write(f"\r  [{bar}] {percent:3d}%  {downloaded // 1024:>8} KiB / {total_size // 1024:>8} KiB")
        sys.stdout.flush()
        if percent >= 100:
            sys.stdout.write("\n")


def download_model(
    model_name: str,
    output_dir: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Download a Whisper GGML model from Hugging Face.

    Args:
        model_name: Model identifier (e.g., "tiny.en", "base", "large-v3").
        output_dir: Directory to save the model. Defaults to
                    ~/.cache/pywhispercpp/ (pywhispercpp's cache dir).
        force: If True, re-download even if file exists.

    Returns:
        Path to the downloaded model file.

    Raises:
        ValueError: If model_name is not in the registry.
        urllib.error.URLError: If download fails.
    """
    if model_name not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown model: '{model_name}'. "
            f"Available: {', '.join(list_available_models())}"
        )

    model_info = MODEL_REGISTRY[model_name]
    url = model_info["url"]
    filename = url.rstrip("/").split("/")[-1]

    if output_dir is None:
        output_dir = PYWHISPERCPP_CACHE

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / filename

    if output_path.exists() and not force:
        print(f"[INFO]  Model already exists: {output_path} ({model_info['size']})")
        return output_path

    print(f"[INFO]  Downloading {model_name} ({model_info['size']})...")
    print(f"[INFO]  URL: {url}")
    print(f"[INFO]  Destination: {output_path}")

    try:
        urllib.request.urlretrieve(url, output_path, reporthook=_progress_hook)
    except Exception as exc:
        # Clean up partial download on failure
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError(f"Failed to download model: {exc}") from exc

    # Verify file size
    actual_size = output_path.stat().st_size
    print(f"[INFO]  Downloaded: {actual_size / 1024 / 1024:.1f} MiB")

    # Verify SHA only if we actually have a full SHA256 to compare against.
    expected_sha = model_info.get("sha256", "")
    if len(expected_sha) == 64:
        actual_sha = _sha256(output_path)
        if actual_sha == expected_sha:
            print(f"[INFO]  SHA256 verified: {actual_sha[:16]}...")
        else:
            print("[WARN]  SHA256 mismatch!")
            print(f"        Expected: {expected_sha}")
            print(f"        Actual:   {actual_sha}")

    # Verify it loads in pywhispercpp
    try:
        _verify_model(output_path)
        print(f"[INFO]  Model verified successfully.")
    except Exception as e:
        print(f"[WARN]  Model verification failed: {e}")
        print(f"[WARN]  The file may be corrupt. Try downloading with --force.")

    return output_path


def download_vad_model(
    model_name: str = "silero-v6.2.0",
    output_dir: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Download a VAD model (ggml format) for whisper.cpp's built-in VAD.

    Args:
        model_name: "silero-v5.1.2" or "silero-v6.2.0"
        output_dir: Destination directory.
        force: Re-download if exists.

    Returns:
        Path to downloaded model.
    """
    if model_name not in VAD_MODELS:
        raise ValueError(
            f"Unknown VAD model: '{model_name}'. "
            f"Available: {', '.join(VAD_MODELS.keys())}"
        )

    model_info = VAD_MODELS[model_name]
    url = model_info["url"]
    filename = url.rstrip("/").split("/")[-1]

    if output_dir is None:
        output_dir = Path.home() / ".cache" / "voice_capture" / "models"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    if output_path.exists() and not force:
        print(f"[INFO]  VAD model already exists: {output_path}")
        return output_path

    print(f"[INFO]  Downloading VAD model {model_name} ({model_info['size']})...")
    urllib.request.urlretrieve(url, output_path, reporthook=_progress_hook)
    print(f"[INFO]  VAD model downloaded: {output_path}")
    return output_path


def _verify_model(model_path: Path) -> None:
    """Verify that a model file can be loaded by pywhispercpp."""
    try:
        from pywhispercpp.model import Model
        # Load and immediately unload (just to verify the file)
        model = Model(str(model_path), print_realtime=False, print_progress=False)
        del model
    except ImportError:
        print("[WARN]  pywhispercpp not installed, skipping model verification.")
    except Exception as e:
        raise RuntimeError(f"Model verification failed: {e}")


def _sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for model management."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Voice Capture - Model Downloader",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available models",
    )
    parser.add_argument(
        "--download", "-d",
        type=str,
        metavar="MODEL",
        help="Download a model (e.g., tiny.en, base, large-v3-turbo)",
    )
    parser.add_argument(
        "--download-vad",
        type=str,
        nargs="?",
        const="silero-v6.2.0",
        metavar="VAD_MODEL",
        help="Download a VAD model (silero-v5.1.2 or silero-v6.2.0)",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-download if file exists",
    )
    parser.add_argument(
        "--list-downloaded",
        action="store_true",
        help="List locally downloaded models",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom output directory for downloaded model",
    )

    args = parser.parse_args()

    if args.list:
        print("Available models:")
        for name, info in MODEL_REGISTRY.items():
            marker = ""
            try:
                get_model_path(name)
                marker = " [DOWNLOADED]"
            except FileNotFoundError:
                pass
            print(f"  {name:30s} {info['size']:>8s}{marker}")
        return

    if args.list_downloaded:
        models = list_downloaded_models()
        if models:
            print("Downloaded models:")
            for name in models:
                try:
                    p = get_model_path(name)
                    print(f"  {name:30s} ({p})")
                except FileNotFoundError:
                    pass
        else:
            print("No models downloaded yet.")
        return

    if args.download_vad:
        output_dir = Path(args.output_dir) if args.output_dir else None
        download_vad_model(args.download_vad, output_dir=output_dir, force=args.force)
        return

    if args.download:
        output_dir = Path(args.output_dir) if args.output_dir else None
        download_model(args.download, output_dir=output_dir, force=args.force)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
