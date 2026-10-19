"""Real Google client compatibility without provider requests or real credentials."""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from plugins.platforms.google_chat import oauth


@pytest.fixture
def oauth_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    client_path = tmp_path / "client.json"
    client_path.write_text(json.dumps({"installed": {
        "client_id": "client.example",
        "client_secret": "synthetic-secret",
        "auth_uri": "https://accounts.example/authorize",
        "token_uri": "https://accounts.example/token",
        "redirect_uris": ["http://localhost:1"],
    }}), encoding="utf-8")
    monkeypatch.setattr(oauth, "_client_secret_path", lambda: client_path)
    # Dependency selection belongs to the separate installer contract; this
    # test exercises the selected real libraries without switching environments.
    monkeypatch.setattr(oauth, "_ensure_deps", lambda: None)
    return "operator@example.test"


def test_real_google_flow_preserves_state_and_pkce(oauth_home, capsys):
    pytest.importorskip("google_auth_oauthlib.flow")

    oauth.get_auth_url(oauth_home)

    query = parse_qs(urlparse(capsys.readouterr().out.strip()).query)
    pending = oauth._load_pending_auth(oauth_home)
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(pending["code_verifier"].encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert query["state"] == [pending["state"]]
    assert query["redirect_uri"] == [pending["redirect_uri"]]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [expected_challenge]


def test_real_google_token_exchange_preserves_verifier_and_granted_scopes(
    oauth_home, monkeypatch, capsys,
):
    pytest.importorskip("google_auth_oauthlib.flow")
    oauth.get_auth_url(oauth_home)
    capsys.readouterr()
    pending = oauth._load_pending_auth(oauth_home)
    requests_seen = []

    def send(_session, request, **_kwargs):
        requests_seen.append(request)
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response._content = json.dumps({
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": oauth.SCOPES[0],
        }).encode("utf-8")
        return response

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    oauth.exchange_auth_code(
        f"http://localhost:1/?code=synthetic-code&state={pending['state']}", oauth_home,
    )

    assert len(requests_seen) == 1
    request = requests_seen[0]
    body = request.body.decode("utf-8") if isinstance(request.body, bytes) else request.body
    fields = parse_qs(body)
    assert request.url == "https://accounts.example/token"
    assert request.method == "POST"
    assert fields["code_verifier"] == [pending["code_verifier"]]
    assert fields["code"] == ["synthetic-code"]
    token = json.loads(oauth._token_path(oauth_home).read_text(encoding="utf-8"))
    assert token["type"] == "authorized_user"
    assert token["refresh_token"] == "synthetic-refresh"
    assert token["scopes"] == oauth.SCOPES
    assert not oauth._pending_auth_path(oauth_home).exists()
