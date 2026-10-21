"""Text from users, imports and exchanges must never run as code in the page."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client


# ---- Content-Security-Policy ----

def _csp(resp):
    policy = resp.headers.get("content-security-policy", "")
    return {d.split()[0]: d.split()[1:] for d in (p.strip() for p in policy.split(";")) if d}


@pytest.mark.parametrize("path", ["/", "/api/overview", "/static/js/app.js", "/api/does-not-exist", "/docs-x"])
def test_csp_blocks_inline_script_and_outside_connections(api, path):
    csp = _csp(api.get(path))
    assert csp["script-src"] == ["'self'"]  # no inline script, no inline handlers, no eval
    assert csp["connect-src"] == ["'self'"]  # injected code can't send data anywhere
    assert csp["img-src"] == ["'self'", "data:", "blob:"]  # no image beacons to other hosts
    assert csp["object-src"] == ["'none'"]
    assert csp["base-uri"] == ["'none'"]
    assert csp["form-action"] == ["'self'"]
    assert csp["default-src"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'none'"]


def test_only_the_api_docs_pages_skip_the_policy(api):
    assert "content-security-policy" not in api.get("/docs").headers


def test_pages_tell_the_browser_not_to_guess_content_types(api):
    assert api.get("/").headers.get("x-content-type-options") == "nosniff"


def test_frontend_has_no_inline_event_handlers():
    for name in ("index.html", "js/app.js"):
        text = (FRONTEND / name).read_text(encoding="utf-8")
        handlers = re.findall(r"<[^>]*\son[a-z]+\s*=", text)
        assert handlers == [], f"{name}: {handlers}"


# ---- Colours (they go into style attributes) ----

BAD_COLOURS = ["red", "#fff", "#12345g", "#123456; background:url(x)", "#123456\"><b>", ""]


@pytest.mark.parametrize("colour", BAD_COLOURS)
def test_strategy_and_tag_colours_must_be_hex(api, colour):
    assert api.post("/api/strategies", json={"name": "S", "colour": colour}).status_code == 422
    assert api.post("/api/emotion-tags", json={"name": "T", "colour": colour}).status_code == 422


def test_colour_updates_must_be_hex(api):
    sid = api.post("/api/strategies", json={"name": "S", "colour": "#34d399"}).json()["id"]
    assert api.patch(f"/api/strategies/{sid}", json={"colour": "url(x)"}).status_code == 422
    tid = api.post("/api/emotion-tags", json={"name": "T", "colour": "#34d399"}).json()["id"]
    assert api.patch(f"/api/emotion-tags/{tid}", json={"colour": "url(x)"}).status_code == 422


@pytest.mark.parametrize("route, key", [("/api/strategies-import", "strategies"), ("/api/emotion-tags-import", "emotion_tags")])
def test_imports_with_a_bad_colour_are_refused_and_nothing_is_added(api, route, key):
    data = {key: [{"name": "Good", "colour": "#34d399"}, {"name": "Evil", "colour": "red;background:url(//x)"}]}
    resp = api.post(route, json=data)
    assert resp.status_code == 400
    assert "Evil" in resp.json()["detail"]
    listed = api.get(route.replace("-import", "")).json()
    assert "Good" not in [x["name"] for x in listed]


@pytest.mark.parametrize("route, key", [("/api/strategies-import", "strategies"), ("/api/emotion-tags-import", "emotion_tags")])
def test_imports_with_wrong_field_types_are_refused(api, route, key):
    resp = api.post(route, json={key: [{"name": ["not", "text"], "colour": "#34d399"}]})
    assert resp.status_code == 400


def test_html_in_names_is_stored_as_plain_text(api):
    """Names may contain any characters; the page escapes them when showing them."""
    name = "Mike's <img src=x onerror=alert(1)> setup"
    assert api.post("/api/strategies", json={"name": name, "colour": "#34d399"}).status_code == 201
    assert name in [s["name"] for s in api.get("/api/strategies").json()]


def test_every_action_in_the_page_has_a_handler():
    js = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for attr, table in (("data-action", "CLICK_ACTIONS"), ("data-change", "CHANGE_ACTIONS"), ("data-blur", "BLUR_ACTIONS")):
        block = re.search(table + r" = \{(.*?)\n\};", js, re.S).group(1)
        handled = set(re.findall(r"'([a-z-]+)':", block))
        used = set(re.findall(attr + r'="([a-z-]+)"', js + html))
        used |= set(re.findall(r"dataset\.action = '([a-z-]+)'", js)) if attr == "data-action" else set()
        assert used <= handled, f"{attr} without handler: {used - handled}"


# Snippets built by our own code from fixed text (or already escaped parts).
_SAFE_TEMPLATE_PARTS = {
    "icon", "typeBadge", "detailHtml", "options", "noStrategies", "clearOption", "strategyCell",
    "expandBtn", "thumbs + uploadBtn", "staleTag", "staleClass", "statusColour", "colour", "pnlClass",
}


def test_every_value_in_an_html_template_is_escaped():
    js = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
    unescaped = []
    for template in re.findall(r"`([^`]*<[^`]*)`", js):
        for expr in re.findall(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", template):
            expr = expr.strip()
            if expr in _SAFE_TEMPLATE_PARTS:
                continue
            if re.match(r"^(esc|safeColour|getStrategyColour|statusBadge|sideClass)\(", expr):
                continue
            if re.match(r"^[^`$]+\? '[^'<\"]*' : '[^'<\"]*'$", expr):  # picks one of two fixed words, e.g. x ? 'active' : ''
                continue
            if re.match(r"^\w+ \? `", expr):  # nested template, checked on its own
                continue
            unescaped.append(expr)
    assert unescaped == []


@pytest.mark.parametrize("route, key", [("/api/strategies-import", "strategies"), ("/api/emotion-tags-import", "emotion_tags")])
@pytest.mark.parametrize("payload", ["abc", {"a": 1}, [1], [None], ["text"]])
def test_imports_of_the_wrong_shape_are_refused(api, route, key, payload):
    assert api.post(route, json={key: payload}).status_code == 400
    assert api.get(route.replace("-import", "")).json() == [] or key == "emotion_tags"


@pytest.mark.parametrize("route, key", [("/api/strategies-import", "strategies"), ("/api/emotion-tags-import", "emotion_tags")])
def test_import_without_colour_gets_the_default(api, route, key):
    """Older exports may have no colour or null; they still import, with the default colour."""
    resp = api.post(route, json={key: [{"name": "NoColour"}, {"name": "NullColour", "colour": None}]})
    assert resp.status_code == 200
    listed = {x["name"]: x["colour"] for x in api.get(route.replace("-import", "")).json()}
    assert listed["NoColour"] == listed["NullColour"] == "#6c9cfc"


def test_strategy_export_imports_again(api):
    api.post("/api/strategies", json={"name": "Plain", "colour": "#34d399"})  # no description, no playbook
    exported = api.get("/api/strategies-export").json()
    sid = api.get("/api/strategies").json()[0]["id"]
    api.delete(f"/api/strategies/{sid}", params={"confirm": "true"})
    assert api.post("/api/strategies-import", json=exported).status_code == 200
    assert [s["name"] for s in api.get("/api/strategies").json()] == ["Plain"]


def test_imported_html_names_are_kept_as_text(api):
    name = "<script>x</script>"
    assert api.post("/api/strategies-import", json={"strategies": [{"name": name, "colour": "#34d399"}]}).status_code == 200
    assert [s["name"] for s in api.get("/api/strategies").json()] == [name]


def test_uploaded_screenshots_can_never_be_scripts_or_pages():
    """Uploads are served from our own origin, which the policy trusts for scripts."""
    from app.api.routes_screenshots import ALLOWED_EXTENSIONS
    assert ALLOWED_EXTENSIONS <= {".png", ".jpg", ".jpeg", ".webp", ".gif"}
