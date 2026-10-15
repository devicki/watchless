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


def probe(proxy_url: str, timeout: float = 4) -> tuple[bool, str]:
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(PROBE_URL, timeout=timeout) as r:
            return r.status in (200, 204), f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:200]


def run_up_command(cmd: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(shlex.split(cmd), capture_output=True, timeout=timeout)
        return f"exit {r.returncode}"  # output deliberately not echoed (may contain secrets)
    except Exception as e:  # noqa: BLE001
        return f"failed: {type(e).__name__}"


def ensure(prefs: dict, wait_seconds: float = 20, probe_timeout: float = 4, interval: float = 1.5,
           _probe=None, _forwarder=None, _up=None, _sleep=time.sleep, _now=time.monotonic) -> dict:
    """Warm up and verify the configured proxy. Returns a status dict with steps and timing.

    1. start the local forwarder (proxy_upstream) if it is not listening, then a quick probe;
    2. on failure: run proxy_up_command (if set), re-check the forwarder, and poll with short
       probes for up to `wait_seconds`.
    Raises ProxyError with an actionable message if the proxy never comes up.
    """
    probe_fn, fwd_fn, up_fn = _probe or probe, _forwarder or ensure_forwarder, _up or run_up_command
    url = (prefs.get("proxy_url") or "").strip()
    if not url:
        return {"proxy": None, "status": "not configured"}
    t0 = _now()
    upstream = prefs.get("proxy_upstream", "")
    steps = {"forwarder": fwd_fn(url, upstream)}
    ok, detail = probe_fn(url, probe_timeout)
    attempts = 1
    if not ok:
        steps["first_probe"] = detail
        if prefs.get("proxy_up_command"):
            steps["up_command"] = up_fn(prefs["proxy_up_command"])
        deadline = _now() + wait_seconds
        while not ok and _now() < deadline:
            steps["forwarder_retry"] = fwd_fn(url, upstream)
            ok, detail = probe_fn(url, min(probe_timeout, 3))
            attempts += 1
            if not ok:
                _sleep(interval)
    elapsed = round(_now() - t0, 1)
    if not ok:
        raise ProxyError(
            f"Proxy {url} is configured but not reachable after {elapsed}s warm-up ({detail}). Start the proxy "
            "(or the network tunnel behind it), set prefs.proxy_up_command to a script that does, or clear "
            "prefs.proxy_url to connect directly.")
    return {"proxy": url, "status": "ok", "detail": detail, "warmup_seconds": elapsed,
            "probe_attempts": attempts, "warmed_up": attempts > 1, **steps}
