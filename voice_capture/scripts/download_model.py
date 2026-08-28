#!/usr/bin/env python3
"""
Standalone script to download Whisper GGML models.

Usage:
    python scripts/download_model.py tiny.en
    python scripts/download_model.py base --force
    python scripts/download_model.py --list
    python scripts/download_model.py --list-downloaded
    python scripts/download_model.py --download-vad silero-v6.2.0
"""
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_capture.model_downloader import main

if __name__ == "__main__":
    main()
