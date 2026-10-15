"""Optional proxy support: health check, optional local forwarder, optional "proxy up" hook.

Nothing is hardcoded; everything comes from prefs (proxy_url, proxy_upstream, proxy_up_command).
"""
from __future__ import annotations

import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

PROBE_URL = "https://www.youtube.com/generate_204"


class ProxyError(RuntimeError):
    pass


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def ensure_forwarder(listen_url: str, upstream: str) -> str:
    """Start a local pproxy forwarder (listen_url -> upstream chain) if not listening."""
    if not listen_url or not upstream:
        return "skipped"
    u = urlparse(listen_url)
    host, port = u.hostname or "127.0.0.1", u.port or 8080
    if _listening(host, port):
        return "up"
    exe = shutil.which("pproxy") or str(Path(sys.executable).with_name("pproxy"))
    if not Path(exe).exists():
        return "failed: pproxy not installed (pip install pproxy)"
    subprocess.Popen([exe, "-l", f"http://{host}:{port}", "-r", upstream],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(20):
        time.sleep(0.25)
        if _listening(host, port):
            return "started"
    return "failed: forwarder did not start"


def probe(proxy_url: str, timeout: float = 12) -> tuple[bool, str]:
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(PROBE_URL, timeout=timeout) as r:
            return r.status in (200, 204), f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:200]


def run_up_command(cmd: str, timeout: int = 90) -> str:
    try:
        r = subprocess.run(shlex.split(cmd), capture_output=True, timeout=timeout)
        return f"exit {r.returncode}"  # output deliberately not echoed (may contain secrets)
    except Exception as e:  # noqa: BLE001
        return f"failed: {type(e).__name__}"


def ensure(prefs: dict) -> dict:
    """Return a status dict. Raises ProxyError with an actionable message if a configured proxy is dead."""
    url = (prefs.get("proxy_url") or "").strip()
    if not url:
        return {"proxy": None, "status": "not configured"}
    steps = {"forwarder": ensure_forwarder(url, prefs.get("proxy_upstream", ""))}
    ok, detail = probe(url)
    if not ok and prefs.get("proxy_up_command"):
        steps["up_command"] = run_up_command(prefs["proxy_up_command"])
        steps["forwarder_retry"] = ensure_forwarder(url, prefs.get("proxy_upstream", ""))
        for _ in range(3):
            time.sleep(3)
            ok, detail = probe(url)
            if ok:
                break
    if not ok:
        raise ProxyError(
            f"Proxy {url} is configured but not reachable ({detail}). Start the proxy (or the network "
            "tunnel behind it), set prefs.proxy_up_command to a script that does, or clear prefs.proxy_url "
            "to connect directly.")
    return {"proxy": url, "status": "ok", "detail": detail, **steps}
