#!/usr/bin/env python3
"""
Voice Capture - Entry Point.

Run with:
    python -m voice_capture.main
    # or after pip install:
    voice-capture
"""

import sys
import os

# Ensure the package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    """Application entry point."""
    from voice_capture.config import load_config
    from voice_capture.app import VoiceCaptureApp

    config = load_config()
    app = VoiceCaptureApp(config)
    exit_code = app.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
