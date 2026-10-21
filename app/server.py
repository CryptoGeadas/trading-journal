"""One-command start: run the journal on this computer and open it in the browser.

    python -m app              # start and open the browser
    python -m app --no-browser # start only
"""

import argparse
import ipaddress
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from app import config

_LOCAL_NAMES = {"localhost"}
_STILL_STARTING_SECONDS = 30  # first start on Windows can be slow (antivirus, imports)
_READY_TIMEOUT_SECONDS = 180


def _is_loopback(host: str) -> bool:
    if host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def network_warning(host: str, auth_enabled: bool) -> str | None:
    """Warning text when the journal can be opened from other devices, else None."""
    if _is_loopback(host):
        return None
    warning = (
        f"WARNING: HOST={host} makes the journal reachable from other devices on your network. "
        "Use HOST=127.0.0.1 unless you really need this."
    )
    if not auth_enabled:
        warning += (
            " Login is OFF: anyone on the same network can open your journal. "
            "Set AUTH_USER and AUTH_PASS to require a password."
        )
    return warning


def local_url(host: str, port: int) -> str:
    """Address to open in this computer's browser."""
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def port_in_use(host: str, port: int) -> bool:
    """True if something already listens on this address (e.g. the journal is already running)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


def _is_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def _open_when_ready(url: str) -> None:
    start = time.monotonic()
    told_still_starting = False
    while time.monotonic() - start < _READY_TIMEOUT_SECONDS:
        if not told_still_starting and time.monotonic() - start >= _STILL_STARTING_SECONDS:
            print("Still starting, please wait...", flush=True)
            told_still_starting = True
        if _is_up(url):
            print(f"Trading Journal is running at {url}", flush=True)
            webbrowser.open(url)
            return
        time.sleep(0.5)
    print(f"The journal did not start within {_READY_TIMEOUT_SECONDS}s; check the messages above. Then open {url}.", flush=True)


def main(argv=None, host: str | None = None, port: int | None = None, auth_enabled: bool | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="Start the Trading Journal.")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args(argv)

    host = config.HOST if host is None else host
    port = config.PORT if port is None else port
    auth_enabled = bool(config.AUTH_USER and config.AUTH_PASS) if auth_enabled is None else auth_enabled

    warning = network_warning(host, auth_enabled)
    if warning:
        print(warning, flush=True)

    url = local_url(host, port)
    if port_in_use(host, port):
        print(
            f"Port {port} is already in use. If the journal is already running, open {url}. "
            "Otherwise stop the other program or set PORT to another number.",
            flush=True,
        )
        sys.exit(1)

    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.Server(uvicorn.Config("app.main:app", host=host, port=port)).run()
