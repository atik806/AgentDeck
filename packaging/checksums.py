#!/usr/bin/env python3
"""Write a SHA256SUMS.txt manifest for a directory of release artifacts.

    python packaging/checksums.py packaging/Releases

Produces ``<dir>/SHA256SUMS.txt`` with one ``<hex>  <name>`` line per file
(sorted, the manifest itself excluded) -- the format ``sha256sum -c`` expects.
The GitHub Actions release workflow runs this and uploads the manifest beside
the installer so downloaders can verify what they got; run it yourself before a
local ``vpk upload`` and attach the file to the release.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

MANIFEST = "SHA256SUMS.txt"


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("packaging/Releases")
    if not target.is_dir():
        print(f"[checksums] not a directory: {target}", file=sys.stderr)
        return 1

    files = sorted(
        p for p in target.iterdir() if p.is_file() and p.name != MANIFEST
    )
    if not files:
        print(f"[checksums] no artifacts in {target}", file=sys.stderr)
        return 1

    lines = [f"{_digest(p)}  {p.name}" for p in files]
    out = target / MANIFEST
    out.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"[checksums] wrote {out} ({len(lines)} files)")
    for line in lines:
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
