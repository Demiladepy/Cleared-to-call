"""Deployment entrypoint.

A serverless host builds the function by tracing imports, and reports any
failure during that import as an opaque 500 with no detail. That turns every
deployment mistake into a blind round-trip: push, rebuild, refresh, guess.

Two jobs here:

- make the repository root importable, since `cleared` lives beside `demo/`
  rather than inside it;
- if the application still fails to import, serve the traceback.

The fallback deliberately uses nothing but the standard library. An earlier
version built it out of FastAPI, which is useless precisely when the missing
dependency *is* FastAPI - the fallback died on the same import as the app and
the host reported its own error page instead.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _diagnostic_report(detail: str) -> str:
    """Everything needed to tell why the import failed, from stdlib only."""
    try:
        root_listing = "\n".join(
            f"  {item.name}{'/' if item.is_dir() else ''}" for item in sorted(ROOT.iterdir())
        )
    except OSError as error:
        root_listing = f"  <could not list {ROOT}: {error}>"

    installed = []
    try:
        from importlib.metadata import distributions

        installed = sorted(
            f"{dist.metadata['Name']}=={dist.version}"
            for dist in distributions()
            if dist.metadata.get("Name")
        )
    except Exception as error:  # noqa: BLE001
        installed = [f"<could not list installed packages: {error}>"]

    return (
        "Cleared to Call failed to start.\n\n"
        f"{detail}\n"
        f"python: {sys.version}\n"
        f"sys.path[0]: {sys.path[0]}\n"
        f"repository root: {ROOT}\n\n"
        f"contents of root:\n{root_listing}\n\n"
        f"installed packages ({len(installed)}):\n  " + "\n  ".join(installed) + "\n"
    )


def _stdlib_asgi_app(report: str):
    """A minimal ASGI application that serves one plain-text page."""
    body = report.encode("utf-8")

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return application


try:
    from demo.app import app
except Exception:  # noqa: BLE001 - reporting anything at all is the point
    app = _stdlib_asgi_app(_diagnostic_report(traceback.format_exc()))


__all__ = ["app"]
