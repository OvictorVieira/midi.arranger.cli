"""Helpers for linking arrangement plans back to their originating brief."""

from __future__ import annotations

import hashlib
from pathlib import Path


def brief_sha256(path: str | Path) -> str:
    """Return the lowercase SHA-256 hex digest of the brief file bytes.

    This is the same value recorded by the harness in
    `.midiarranger/brief.sha256`: hash the file exactly as written, without
    parsing or normalizing JSON.
    """
    h = hashlib.sha256()
    with open(Path(path).expanduser(), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["brief_sha256"]
