"""Screenshot names and uploads, and the optional login."""

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def api():
    from app.main import app
    with TestClient(app) as client:
        yield client


def _trade(api):
    api.post("/api/trades", json={"pair": "BTC/USDT", "side": "buy", "quantity": 1, "price": 1})
    return api.get("/api/trades").json()["trades"][0]["id"]


@pytest.mark.parametrize("name", ["..%2F..%2Fjournal.db", "..\\journal.db", "journal.db", ".encryption_key",
                                  "abcdef123456.txt", "a.b.png", "missing-file.png"])
def test_bad_screenshot_names_always_get_the_same_answer(api, data_root, name):
    (data_root / "journal.db").touch()  # exists elsewhere on disk: must not change the answer
    resp = api.get(f"/api/screenshots/{name}")
    # A name with an encoded slash never reaches the route (the router's own 404).
    assert resp.status_code == 404
    assert resp.json()["detail"] in ("Screenshot not found", "Not Found")


def test_a_real_screenshot_is_still_served(api):
    trade_id = _trade(api)
    up = api.post(f"/api/trades/{trade_id}/screenshots", files={"file": ("chart.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    got = api.get(up.json()["url"])
    assert (got.status_code, got.content) == (200, PNG)


@pytest.mark.parametrize("name, body", [
    ("chart.png", b"<html><script>alert(1)</script></html>"),
    ("chart.jpg", PNG),           # a PNG renamed .jpg
    ("chart.gif", b"MZ\x90\x00"),  # a program
])
def test_a_file_that_is_not_the_image_its_name_says_is_refused(api, name, body):
    trade_id = _trade(api)
    resp = api.post(f"/api/trades/{trade_id}/screenshots", files={"file": (name, body, "image/png")})
    assert resp.status_code == 400
    assert "not a" in resp.json()["detail"]


@pytest.fixture
def login_app(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUTH_USER", "me")
    monkeypatch.setattr(config, "AUTH_PASS", "s3cret")
    import app.main
    module = importlib.reload(app.main)
    yield module.app
    monkeypatch.undo()
    importlib.reload(app.main)


def _basic(text):
    return {"Authorization": "Basic " + base64.b64encode(text.encode()).decode()}


def test_login_accepts_only_the_right_user_and_password(login_app):
    with TestClient(login_app) as client:
        assert client.get("/api/pairs").status_code == 401
        assert client.get("/api/pairs", headers=_basic("me:s3cret")).status_code == 200
        assert client.get("/api/pairs", headers=_basic("me:wrong")).status_code == 401
        assert client.get("/api/pairs", headers=_basic("mex:s3cret")).status_code == 401
        assert client.get("/api/pairs", headers={"Authorization": "Basic !!!not-base64"}).status_code == 401
        assert client.get("/api/pairs", headers=_basic("no-colon")).status_code == 401


def test_a_screenshot_with_an_older_style_name_is_still_served(api, data_root):
    folder = data_root / "screenshots"
    folder.mkdir(exist_ok=True)
    (folder / "a1b2c3d4e5f6a7b8.png").write_bytes(PNG)
    assert api.get("/api/screenshots/a1b2c3d4e5f6a7b8.png").status_code == 200



@pytest.mark.parametrize("name, body, ok", [
    ("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", True),
    ("a.webp", b"RIFF\x00\x00\x00\x00WAVEfmt ", False),
    ("a.gif", b"GIF87a" + b"\x00" * 10, True),
    ("a.gif", b"GIF89a" + b"\x00" * 10, True),
    ("a.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 10, True),
    ("a.jpeg", b"\xff\xd8\xff\xe1" + b"\x00" * 10, True),
    ("a.PNG", PNG, True),
    ("a.png", b"", False),
])
def test_image_signatures(api, name, body, ok):
    resp = api.post(f"/api/trades/{_trade(api)}/screenshots", files={"file": (name, body, "image/png")})
    assert (resp.status_code == 200) == ok
    if ok:
        assert api.get(resp.json()["url"]).status_code == 200


def test_login_edge_cases(login_app):
    with TestClient(login_app) as client:
        bad_utf8 = {"Authorization": "Basic " + base64.b64encode(b"me:\xff\xfe").decode()}
        assert client.get("/api/pairs", headers=bad_utf8).status_code == 401
        assert client.get("/").status_code == 401
        assert client.get("/api/screenshots/abc.png").status_code == 401
        assert client.get("/health").status_code == 200
