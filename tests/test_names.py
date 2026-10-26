"""Strategy and emotion-tag names stay unique when renamed."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path", ["/api/strategies", "/api/emotion-tags"])
def test_renaming_to_an_existing_name_returns_409(api, path):
    first = api.post(path, json={"name": "Breakout"})
    second = api.post(path, json={"name": "Scalp"})
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    target = second.json()["id"]
    resp = api.patch(f"{path}/{target}", json={"name": "breakout"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    assert api.patch(f"{path}/{target}", json={"name": "  "}).status_code == 400
    assert api.patch(f"{path}/{target}", json={"name": "Scalp v2"}).status_code == 200
    assert api.patch(f"{path}/{target}", json={"name": "Scalp v2"}).status_code == 200  # its own name is fine



@pytest.mark.parametrize("path", ["/api/strategies", "/api/emotion-tags"])
def test_creating_a_name_that_differs_only_by_spaces_or_case_returns_409(api, path):
    assert api.post(path, json={"name": "Breakout2"}).status_code == 201
    assert api.post(path, json={"name": " breakout2 "}).status_code == 409
    assert api.post(path, json={"name": "   "}).status_code == 400
