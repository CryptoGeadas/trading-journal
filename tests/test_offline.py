"""The frontend must load nothing from the internet (only connected exchanges are contacted)."""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# Anything the browser would fetch by itself: src/href on scripts, styles, fonts,
# preconnects, images, and url(...) / @import in CSS.
_LOAD_PATTERNS = [
    r"<script[^>]+src=[\"'](https?:)?//",
    r"<link[^>]+href=[\"'](https?:)?//",
    r"<img[^>]+src=[\"'](https?:)?//",
    r"url\(\s*[\"']?(https?:)?//",
    r"@import\s+[\"'](https?:)?//",
    r"fetch\(\s*[\"'`](https?:)?//",
]


def _frontend_files():
    return [p for p in FRONTEND.rglob("*") if p.suffix in {".html", ".css", ".js"} and "vendor" not in p.parts]


def test_frontend_loads_nothing_from_other_hosts():
    offenders = []
    for path in _frontend_files():
        text = path.read_text(encoding="utf-8")
        for pattern in _LOAD_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                offenders.append(f"{path.relative_to(FRONTEND)}: {m.group(0)}")
    assert offenders == []


def test_bundled_files_exist_and_keep_their_licences():
    vendor = FRONTEND / "vendor"
    assert (vendor / "lightweight-charts" / "lightweight-charts.standalone.production.js").is_file()
    assert (vendor / "lightweight-charts" / "LICENSE").is_file()
    assert (vendor / "lightweight-charts" / "NOTICE").is_file()
    fonts = list((vendor / "fonts").glob("*.woff2"))
    assert fonts
    assert (vendor / "fonts" / "OFL-DM-Sans.txt").is_file()
    assert (vendor / "fonts" / "OFL-JetBrains-Mono.txt").is_file()


def test_every_bundled_font_is_declared_and_every_declared_font_exists():
    css = (FRONTEND / "vendor" / "fonts" / "fonts.css").read_text(encoding="utf-8")
    declared = set(re.findall(r"url\(\s*[\"']?([^\"')]+\.woff2)", css))
    on_disk = {p.name for p in (FRONTEND / "vendor" / "fonts").glob("*.woff2")}
    assert declared == on_disk


def test_chart_library_attribution_is_shown():
    """The chart library's licence asks for its notice and a link to TradingView on the page."""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'href="https://www.tradingview.com/"' in html
    assert "TradingView Lightweight Charts" in html


# The only outside address allowed in our own frontend files: the chart
# library's required attribution link (a link the user may click, not a load).
_ALLOWED_URLS = {"https://www.tradingview.com/"}


def test_no_outside_addresses_in_our_frontend_code():
    found = set()
    for path in _frontend_files():
        found |= set(re.findall(r"(?:https?|wss?)://[^\s\"'`)<>]+", path.read_text(encoding="utf-8")))
    assert found <= _ALLOWED_URLS


def test_linked_bundled_files_are_served():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    html = client.get("/").text
    linked = re.findall(r"""(?:src|href)=["'](/static/vendor/[^"']+)""", html)
    assert len(linked) == 2  # fonts.css + chart library
    fonts_css = client.get("/static/vendor/fonts/fonts.css")
    fonts = ["/static/vendor/fonts/" + f for f in re.findall(r"url\(\s*[\"']?([^\"')]+)", fonts_css.text)]
    for url in linked + fonts:
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert len(resp.content) > 100, url


def test_licence_files_have_the_right_text():
    vendor = FRONTEND / "vendor"
    assert "Apache License" in (vendor / "lightweight-charts" / "LICENSE").read_text(encoding="utf-8")
    assert "TradingView" in (vendor / "lightweight-charts" / "NOTICE").read_text(encoding="utf-8")
    for ofl in (vendor / "fonts").glob("OFL-*.txt"):
        assert "SIL Open Font License" in ofl.read_text(encoding="utf-8"), ofl.name
    js = (vendor / "lightweight-charts" / "lightweight-charts.standalone.production.js").read_text(encoding="utf-8")
    assert "4.1.3" in js[:400]


def test_backend_only_contacts_exchanges_and_itself():
    """Outbound network code lives only in the exchange adapters (ccxt) and the local start check."""
    app_dir = FRONTEND.parent / "app"
    network = re.compile(r"\b(import (requests|httpx|aiohttp)|from (requests|httpx|aiohttp)|urllib\.request|create_connection)\b")
    users = {
        str(p.relative_to(app_dir)).replace("\\", "/")
        for p in app_dir.rglob("*.py")
        if network.search(p.read_text(encoding="utf-8"))
    }
    assert users <= {"server.py"}  # polls its own /health on 127.0.0.1


def test_bundled_font_css_only_points_at_files_next_to_it():
    css = (FRONTEND / "vendor" / "fonts" / "fonts.css").read_text(encoding="utf-8")
    for target in re.findall(r"url\(\s*[\"']?([^\"')]+)", css):
        assert "/" not in target and ":" not in target, target
