"""Smoke tests for the demo view, including that it cannot dial by accident."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from demo import app as demo_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_app, "RUNTIME", tmp_path)
    demo_app.state.run = None
    demo_app.state.live_records.clear()
    demo_app.state.allow_live = False
    demo_app.state.now_input = demo_app.DEFAULT_NOW
    return TestClient(demo_app.app)


def test_the_page_renders_the_batch(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Cleared to Call" in body
    assert "OUTSIDE_CALL_WINDOW" in body
    assert "NO_CONSENT" in body
    assert "ON_SUPPRESSION_LIST" in body


def test_the_page_never_shows_an_unmasked_number(client):
    body = client.get("/").text
    assert "+15550101234" not in body
    assert "+1******1234" in body


def test_the_page_shows_the_opt_out_beat(client):
    body = client.get("/").text
    assert "Stop calling me" in body
    assert "number suppressed" in body


def test_rerunning_with_a_different_clock_changes_the_verdicts(client):
    client.post("/run", data={"now": "2026-08-28T06:00:00Z", "fresh": "1"})
    payload = client.get("/api/run").json()
    reasons = {record["result"]["block_reason"] for record in payload["records"]}
    assert reasons == {"OUTSIDE_CALL_WINDOW"}
    assert payload["summary"]["cleared"] == 0


def test_an_invalid_clock_is_reported_not_crashed(client):
    response = client.post("/run", data={"now": "half past tuesday"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Could not read that clock" in response.text
    # The batch from before the bad input is still on screen.
    assert "OUTSIDE_CALL_WINDOW" in response.text


def test_the_api_returns_the_full_run(client):
    payload = client.get("/api/run").json()
    assert payload["summary"]["accounts"] == 7
    assert payload["summary"]["blocked"] == 3
    assert payload["summary"]["audit"]["ok"] is True


def test_the_audit_api_verifies_the_chain(client):
    client.get("/")
    payload = client.get("/api/audit").json()
    assert payload["verification"]["ok"] is True
    assert len(payload["entries"]) == 7


def test_live_calling_is_refused_when_it_is_switched_off(client):
    client.get("/")
    response = client.post("/call/A-1001")
    assert response.status_code == 403
    assert "live calling is off" in response.json()["detail"]


def test_a_blocked_account_cannot_be_called_even_with_live_enabled(client):
    client.get("/")
    demo_app.state.allow_live = True
    try:
        response = client.post("/call/A-1002")
        assert response.status_code == 403
        assert "OUTSIDE_CALL_WINDOW" in response.json()["detail"]
    finally:
        demo_app.state.allow_live = False


def test_an_unknown_account_cannot_be_called(client):
    client.get("/")
    demo_app.state.allow_live = True
    try:
        assert client.post("/call/A-9999").status_code == 404
    finally:
        demo_app.state.allow_live = False


# Deployment safety: a public URL must not be able to dial anyone.


def reload_demo(monkeypatch, **env):
    """Re-import the demo module with a given environment.

    SERVERLESS and the runtime directory are decided at import time, so a test
    that wants to exercise them has to reload the module.
    """
    import importlib

    for key in ("VERCEL", "CLEARED_PUBLIC_DEMO", "ALLOW_LIVE", "CLEARED_RUNTIME_DIR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(demo_app)


def test_live_calling_is_refused_on_a_serverless_host(tmp_path, monkeypatch):
    module = reload_demo(
        monkeypatch, VERCEL="1", ALLOW_LIVE="1", CLEARED_RUNTIME_DIR=str(tmp_path)
    )
    try:
        assert module.SERVERLESS is True
        # Even with ALLOW_LIVE=1 explicitly set, the switch stays off.
        assert module.state.allow_live is False

        client = TestClient(module.app)
        client.get("/")
        response = client.post("/call/A-1001")
        assert response.status_code == 403
        assert "public read-only demo" in response.json()["detail"]
    finally:
        reload_demo(monkeypatch)


def test_the_runtime_directory_falls_back_when_the_repo_is_read_only(tmp_path, monkeypatch):
    """A serverless filesystem is read-only apart from the temp directory."""
    module = reload_demo(monkeypatch, CLEARED_RUNTIME_DIR=str(tmp_path / "elsewhere"))
    try:
        assert module.RUNTIME == tmp_path / "elsewhere"
        client = TestClient(module.app)
        assert client.get("/").status_code == 200
        assert (tmp_path / "elsewhere" / "audit.jsonl").is_file()
    finally:
        reload_demo(monkeypatch)


def test_the_deployed_demo_still_shows_every_refusal(tmp_path, monkeypatch):
    module = reload_demo(monkeypatch, VERCEL="1", CLEARED_RUNTIME_DIR=str(tmp_path))
    try:
        body = TestClient(module.app).get("/").text
        for reason in ("OUTSIDE_CALL_WINDOW", "NO_CONSENT", "ON_SUPPRESSION_LIST"):
            assert reason in body
    finally:
        reload_demo(monkeypatch)


def test_healthz_reports_a_working_deployment(client):
    payload = client.get("/healthz").json()
    assert payload["ok"] is True
    assert payload["missing_files"] == []
    assert payload["zoneinfo"] is True


def test_a_deployment_missing_its_data_files_says_which(client, monkeypatch):
    """Vercel traces imports, not data files. This is the failure that causes."""
    monkeypatch.setattr(
        demo_app,
        "REQUIRED_RUNTIME_FILES",
        (demo_app.ROOT / "cleared" / "policy.json", demo_app.ROOT / "fixtures" / "gone.json"),
    )
    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.json()["missing_files"] == ["fixtures/gone.json"]

    page = client.get("/")
    assert page.status_code == 503
    assert "fixtures/gone.json" in page.text
    assert "includeFiles" in page.text


def bundle_patterns() -> list[str]:
    """The globs vercel.json ships with the function."""
    import json

    config = json.loads((demo_app.ROOT / "vercel.json").read_text(encoding="utf-8"))
    entry = next(iter(config["functions"]))
    raw = config["functions"][entry]["includeFiles"].strip()
    if raw.startswith("{") and raw.endswith("}"):
        raw = raw[1:-1]
    return [part.strip() for part in raw.split(",") if part.strip()]


def test_the_bundle_config_ships_every_required_runtime_file():
    """Vercel traces imports, not data files. Each one must be named."""
    import fnmatch

    patterns = bundle_patterns()
    for path in demo_app.REQUIRED_RUNTIME_FILES:
        relative = str(path.relative_to(demo_app.ROOT)).replace("\\", "/")
        covered = any(
            fnmatch.fnmatch(relative, pattern.replace("**", "*")) for pattern in patterns
        )
        assert covered, f"vercel.json does not ship {relative}"


def test_the_bundle_config_ships_the_gate_package():
    """The crash was the package itself missing, not its data file."""
    import fnmatch

    patterns = bundle_patterns()
    for module in ("cleared/__init__.py", "cleared/gate.py", "cleared/runner.py"):
        assert any(
            fnmatch.fnmatch(module, pattern.replace("**", "*")) for pattern in patterns
        ), f"vercel.json does not ship {module}"


def test_the_deployment_entrypoint_exposes_the_app():
    """pyproject points the host at demo.entry, so it must import cleanly."""
    import tomllib

    from demo import entry

    assert entry.app is demo_app.app

    config = tomllib.loads((demo_app.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["vercel"]["entrypoint"] == "demo.entry:app"


def test_the_startup_fallback_needs_nothing_but_the_standard_library():
    """The fallback must survive the very failure it exists to report.

    An earlier version built the error page out of FastAPI, so a missing FastAPI
    killed the fallback on the same import as the app and the host served its
    own opaque error instead.
    """
    source = (demo_app.ROOT / "demo" / "entry.py").read_text(encoding="utf-8")
    fallback = source[source.index("def _stdlib_asgi_app") : source.index("try:\n    from demo.app")]
    for forbidden in ("fastapi", "starlette", "jinja2", "uvicorn"):
        assert forbidden not in fallback.lower(), f"the fallback imports {forbidden}"


def test_the_startup_fallback_serves_the_report():
    import asyncio

    from demo.entry import _stdlib_asgi_app

    application = _stdlib_asgi_app("boom: something went wrong")
    sent = []

    async def drive():
        await application(
            {"type": "http", "method": "GET", "path": "/"},
            lambda: asyncio.sleep(0),
            lambda message: sent.append(message) or asyncio.sleep(0),
        )

    asyncio.run(drive())
    assert sent[0]["status"] == 500
    assert b"boom: something went wrong" in sent[1]["body"]


def test_the_web_dependencies_are_installed_not_optional():
    """A host installing this project must get everything the app imports."""
    import tomllib

    config = tomllib.loads((demo_app.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    installed = " ".join(config["project"]["dependencies"]).lower()
    for package in ("fastapi", "jinja2", "python-multipart", "tzdata"):
        assert package in installed, f"{package} is not in [project] dependencies"


def test_icon_ligature_names_cannot_leak_when_the_font_fails(client):
    """Material Symbols renders icon names as text until its font arrives.

    Without a guard, a blocked CDN turns all 66 icons into the words
    "play_arrow", "verified_user" and so on, across the whole page.
    """
    body = client.get("/").text
    assert "icons-pending" in body, "no guard class is applied before the font loads"
    assert ".icons-pending .icon { visibility: hidden; }" in body
    assert ".icons-failed .icon { display: none; }" in body
    assert 'document.fonts.check' in body, "the guard must verify the font actually loaded"


def test_the_page_still_tells_the_story_without_any_icons(client):
    """What a reader sees when the icon font never arrives."""
    body = client.get("/").text
    for essential in (
        "Refused",
        "Cleared",
        "OUTSIDE_CALL_WINDOW",
        "NO_CONSENT",
        "ON_SUPPRESSION_LIST",
    ):
        assert essential in body, f"{essential} is carried only by an icon"
