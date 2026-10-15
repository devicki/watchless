"""Thin yt-dlp subprocess wrapper with clear, actionable error classification."""
from __future__ import annotations

import shutil
import subprocess
import sys


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


HINTS = {
    "bot_check": ("YouTube asked to 'confirm you're not a bot'. Cloud/datacenter IPs are often blocked. "
                  "Set prefs.proxy_url to a residential/home proxy, or prefs.cookies_file to an exported "
                  "cookies file, or send the video file directly."),
    "rate_limited": ("YouTube returned HTTP 429 (too many requests) for this IP. Wait and retry, or use "
                     "prefs.proxy_url (home proxy) / prefs.cookies_file."),
    "unavailable": "The video is private, removed, age/region-restricted, or the URL is wrong.",
    "network": ("Network error reaching the site. If a proxy is configured, check it with "
                "`watchless doctor`; otherwise check connectivity."),
    "outdated": "yt-dlp may be outdated. Run `pip install -U yt-dlp` and retry.",
    "unknown": "yt-dlp failed. Run `pip install -U yt-dlp`, then retry; see stderr_tail for details.",
}


def classify(stderr: str) -> str:
    s = stderr.lower()
    if "confirm you" in s and "bot" in s:
        return "bot_check"
    if "429" in s or "too many requests" in s:
        return "rate_limited"
    if any(x in s for x in ("private video", "video unavailable", "removed", "sign in to confirm your age",
                            "not available in your country", "unsupported url", "is not a valid url")):
        return "unavailable"
    if any(x in s for x in ("connection", "timed out", "name or service not known", "proxy",
                            "remote end closed", "network is unreachable")):
        return "network"
    if "http error 403" in s or "nsig" in s or "signature" in s:
        return "outdated"
    return "unknown"


def base_args(prefs: dict) -> list[str]:
    args = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "--ignore-config"]
    if prefs.get("proxy_url"):
        args += ["--proxy", prefs["proxy_url"]]
    if prefs.get("cookies_file"):
        args += ["--cookies", str(prefs["cookies_file"])]
    if not shutil.which("deno") and shutil.which("node"):
        args += ["--js-runtimes", "node"]
    return args


def run(prefs: dict, extra: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    cmd = base_args(prefs) + extra
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FetchError("network", f"yt-dlp timed out after {timeout}s. " + HINTS["network"]) from None
    if r.returncode != 0:
        code = classify(r.stderr)
        tail = " | ".join(r.stderr.strip().splitlines()[-3:])[-400:]
        raise FetchError(code, f"{HINTS[code]} (yt-dlp: {tail})")
    return r
