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
    assert "flash bad" in response.text


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
