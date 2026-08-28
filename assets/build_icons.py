"""Rasterise an SVG app mark into PNGs and a multi-resolution Windows .ico.

Usage:
    python assets/build_icons.py <icon.svg> <output-dir> [<output-dir> ...]

Qt's own ICO writer only emits a single frame, so the .ico is assembled here
by hand: every frame is stored as a PNG payload (supported by Windows Vista and
later), which keeps the 256px frame small and the 16/32px frames sharp.

Run with any of the project virtualenvs that have PySide6, e.g.
    windows_launcher/.venv/Scripts/python.exe assets/build_icons.py \
        windows_launcher/assets/icon.svg windows_launcher/assets
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QBuffer, QByteArray
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(renderer: QSvgRenderer, size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(frames: list[bytes], sizes: list[int]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    for data, size in zip(frames, sizes):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
    return header + entries + b"".join(frames)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2

    src = Path(argv[1])
    out_dirs = [Path(p) for p in argv[2:]]

    QGuiApplication(sys.argv[:1])
    renderer = QSvgRenderer(str(src))
    if not renderer.isValid():
        print(f"error: could not load {src}")
        return 1

    imgs = {s: render(renderer, s) for s in SIZES}
    frames = [png_bytes(imgs[s]) for s in SIZES]
    ico = build_ico(frames, SIZES)

    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
        (d / "icon-256.png").write_bytes(frames[-1])
        (d / "icon-128.png").write_bytes(png_bytes(imgs[128]))
        (d / "icon.ico").write_bytes(ico)
        print(f"wrote {d / 'icon.ico'} ({len(ico)} bytes) + icon-256.png, icon-128.png")

    r = QImageReader(str(out_dirs[0] / "icon.ico"))
    frames_found = []
    for i in range(r.imageCount()):
        r.jumpToImage(i)
        frames_found.append(r.size().width())
    print("ico frames:", frames_found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
