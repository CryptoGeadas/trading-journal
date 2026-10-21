"""Tests for safe defaults and the one-command start."""

import importlib

import pytest

from app import config, server


@pytest.fixture
def reload_config(monkeypatch):
    def _reload(**env):
        for key in ("HOST", "PORT", "AUTH_USER", "AUTH_PASS"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield _reload
    monkeypatch.undo()
    importlib.reload(config)


def test_default_host_is_localhost(reload_config):
    assert reload_config().HOST == "127.0.0.1"


def test_host_setting_is_used_when_given(reload_config):
    assert reload_config(HOST="0.0.0.0").HOST == "0.0.0.0"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_no_warning_on_this_computer_only(host):
    assert server.network_warning(host, auth_enabled=False) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "my-pc.local"])
def test_warning_when_reachable_from_the_network(host):
    warning = server.network_warning(host, auth_enabled=True)
    assert warning and "other devices" in warning


def test_louder_warning_when_reachable_and_no_login():
    with_auth = server.network_warning("0.0.0.0", auth_enabled=True)
    without_auth = server.network_warning("0.0.0.0", auth_enabled=False)
    assert "AUTH_USER" in without_auth and "AUTH_USER" not in with_auth


def test_local_url_uses_a_browsable_address():
    assert server.local_url("127.0.0.1", 8000) == "http://127.0.0.1:8000"
    assert server.local_url("0.0.0.0", 8123) == "http://127.0.0.1:8123"
    assert server.local_url("::", 8000) == "http://[::1]:8000"
    assert server.local_url("::1", 8000) == "http://[::1]:8000"
    assert server.local_url("192.168.1.20", 8000) == "http://192.168.1.20:8000"
    assert server.local_url("my-pc", 8000) == "http://my-pc:8000"


def test_start_runs_server_on_configured_address_and_opens_browser(monkeypatch):
    calls = {}

    class FakeServer:
        def __init__(self, cfg):
            calls["host"], calls["port"] = cfg.host, cfg.port

        def run(self):
            calls["ran"] = True

    monkeypatch.setattr(server.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(server, "port_in_use", lambda host, port: False)
    monkeypatch.setattr(server, "_open_when_ready", lambda url: calls.setdefault("opened", url))
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)

    server.main([], host="127.0.0.1", port=8123, auth_enabled=False)

    assert calls == {"host": "127.0.0.1", "port": 8123, "ran": True, "opened": "http://127.0.0.1:8123"}


def test_start_can_skip_the_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(server, "port_in_use", lambda host, port: False)
    monkeypatch.setattr(server.uvicorn, "Server", lambda cfg: type("S", (), {"run": lambda self: None})())
    monkeypatch.setattr(server, "_open_when_ready", opened.append)
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)

    server.main(["--no-browser"], host="127.0.0.1", port=8123, auth_enabled=False)

    assert opened == []


def test_browser_opens_only_after_the_app_answers(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(server, "_is_up", lambda url: next(answers))
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", opened.append)

    server._open_when_ready("http://127.0.0.1:8123")

    assert opened == ["http://127.0.0.1:8123"]


def test_browser_is_not_opened_if_the_app_never_starts(monkeypatch, capsys):
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(server, "_is_up", lambda url: False)
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", opened.append)

    server._open_when_ready("http://127.0.0.1:8123")

    assert opened == []
    out = capsys.readouterr().out
    assert "Still starting" in out and "did not start" in out


class _ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def test_shipped_settings_keep_the_journal_on_this_computer():
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    env_example = (repo / ".env.example").read_text(encoding="utf-8")
    assert "HOST=127.0.0.1" in env_example.splitlines()
    compose = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    port_lines = [line.strip() for line in compose.splitlines() if line.strip().startswith('- "') and ":8000" in line]
    assert port_lines == ['- "127.0.0.1:8000:8000"']


class _FakeServer:
    seen = {}

    def __init__(self, cfg):
        _FakeServer.seen = {"host": cfg.host, "port": cfg.port}

    def run(self):
        pass


@pytest.fixture
def fake_start(monkeypatch):
    monkeypatch.setattr(server.uvicorn, "Server", _FakeServer)
    monkeypatch.setattr(server, "port_in_use", lambda host, port: False)
    return _FakeServer


def test_start_uses_settings_by_default(reload_config, fake_start):
    reload_config(PORT="8124")
    server.main(["--no-browser"])
    assert fake_start.seen == {"host": "127.0.0.1", "port": 8124}


def test_start_prints_the_network_warning(reload_config, fake_start, capsys):
    reload_config(HOST="0.0.0.0", AUTH_USER="me")  # password missing = login is off
    server.main(["--no-browser"])
    out = capsys.readouterr().out
    assert "WARNING" in out and "Login is OFF" in out


def test_start_prints_nothing_alarming_on_localhost(reload_config, fake_start, capsys):
    reload_config()
    server.main(["--no-browser"])
    assert "WARNING" not in capsys.readouterr().out


def test_start_stops_with_a_clear_message_when_the_port_is_taken(monkeypatch, capsys):
    import socket

    started, opened = [], []
    monkeypatch.setattr(server.uvicorn, "Server", lambda cfg: started.append(cfg))
    monkeypatch.setattr(server, "_open_when_ready", opened.append)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(SystemExit) as exit_info:
            server.main([], host="127.0.0.1", port=port, auth_enabled=False)

    assert exit_info.value.code == 1
    assert started == [] and opened == []
    assert "already in use" in capsys.readouterr().out


def test_python_dash_m_app_starts_on_this_computer_only(tmp_path):
    """Real start: the journal answers on 127.0.0.1 and not on the machine's network address."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    from pathlib import Path

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "PORT": str(port), "TRADING_JOURNAL_DATA_DIR": str(tmp_path / "data")}
    env.pop("HOST", None)
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "app", "--no-browser"], cwd=repo, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                    assert resp.status == 200
                    break
            except OSError:
                assert time.monotonic() < deadline, "journal did not start"
                time.sleep(0.3)

        lan_ip = socket.gethostbyname(socket.gethostname())
        if not lan_ip.startswith("127."):
            with pytest.raises(OSError):
                socket.create_connection((lan_ip, port), timeout=2).close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
