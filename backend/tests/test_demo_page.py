"""Tests for the demo client route."""
from __future__ import annotations

import app.main as main
import pytest


@pytest.mark.parametrize("path", ["/", "/demo", "/mobile"])
def test_demo_page_is_served_outside_production(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "FitWaze" in resp.text


@pytest.mark.parametrize("path", ["/", "/demo", "/mobile"])
def test_demo_page_is_not_exposed_in_production(client, monkeypatch, path):
    monkeypatch.setattr(main.settings, "environment", "production")
    assert client.get(path).status_code == 404


def test_demo_page_does_not_persist_credentials_in_the_browser(client):
    """The demo keeps its access token in a variable for the life of the tab.
    Nothing may write credentials into browser storage, where they would
    outlive the session and be readable by anything else on this origin."""
    page = client.get("/demo").text
    for persistent_store in ("localStorage.setItem", "sessionStorage.setItem", "document.cookie ="):
        assert persistent_store not in page


def test_demo_page_is_served_in_the_hosted_pilot(client, monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "pilot")
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("environment, secure", [("development", False), ("pilot", True), ("production", True)])
def test_refresh_cookie_is_https_only_when_hosted(monkeypatch, environment, secure):
    from app.routers import auth

    monkeypatch.setattr(auth.settings, "environment", environment)
    assert auth._cookie_secure() is secure
