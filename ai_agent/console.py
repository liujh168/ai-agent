"""Console helpers for reliable Unicode output on Windows."""

from __future__ import annotations

import sys


def configure_utf8_output() -> None:
    """Reconfigure standard streams when the host permits it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
