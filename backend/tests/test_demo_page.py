"""Tests for the demo client route."""
from __future__ import annotations

import app.main as main


def test_demo_page_is_served_outside_production(client):
    resp = client.get("/demo")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "FitWaze" in resp.text


def test_demo_page_is_not_exposed_in_production(client, monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "production")
    assert client.get("/demo").status_code == 404


def test_demo_page_does_not_persist_credentials_in_the_browser(client):
    """The demo keeps its access token in a variable for the life of the tab.
    Nothing may write credentials into browser storage, where they would
    outlive the session and be readable by anything else on this origin."""
    page = client.get("/demo").text
    for persistent_store in ("localStorage.setItem", "sessionStorage.setItem", "document.cookie ="):
        assert persistent_store not in page
