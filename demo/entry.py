"""Deployment entrypoint.

A serverless host builds the function by tracing imports, and it reports any
failure during that import as an opaque 500 with no detail. That turns every
deployment mistake into a blind round-trip: push, rebuild, refresh, guess.

This module makes the repository root importable before anything else runs, and
if the application still fails to import, it serves the traceback instead of
letting the host swallow it.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# The app imports `cleared`, which lives beside `demo/` rather than inside it.
# Depending on how the host sets the function's working directory, the
# repository root may not be on the path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from demo.app import app
except Exception:  # noqa: BLE001 - the whole point is to report anything
    _detail = traceback.format_exc()

    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()

    @app.get("/{path:path}")
    def startup_failure(path: str) -> PlainTextResponse:
        listing = "\n".join(
            f"  {item.name}{'/' if item.is_dir() else ''}" for item in sorted(ROOT.iterdir())
        )
        return PlainTextResponse(
            "Cleared to Call failed to start.\n\n"
            f"{_detail}\n"
            f"sys.path[0]: {sys.path[0]}\n"
            f"repository root: {ROOT}\n"
            f"contents of root:\n{listing}\n",
            status_code=500,
        )


__all__ = ["app"]
