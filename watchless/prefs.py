"""User preferences at ~/.watchless/prefs.json (override with WATCHLESS_HOME)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "version": 1,
    "lang": "en",                 # user's main language (BCP-47 base, e.g. en, ko, ja)
    "default_mode": "quick",      # quick | study | agent-md | emotion | translate
    "length": "medium",           # short | medium | long
    "asr": {"enabled": True, "model": "small", "policy": "ask"},  # policy: ask | auto | never
    "frames_detail": "transcript",  # transcript | efficient | balanced
    "proxy_url": "",              # optional HTTP proxy for YouTube, e.g. http://127.0.0.1:18888
    "proxy_up_command": "",       # optional script run when the proxy health check fails
    "proxy_upstream": "",         # optional pproxy -r chain; starts a local forwarder at proxy_url
    "cookies_file": "",           # optional Netscape cookies file for yt-dlp (use with care)
    "output_dir": "~/watchless-out",
}


def home() -> Path:
    return Path(os.environ.get("WATCHLESS_HOME", "~/.watchless")).expanduser()


def prefs_path() -> Path:
    return home() / "prefs.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load() -> dict:
    p = prefs_path()
    data = json.loads(p.read_text()) if p.exists() else {}
    return _merge(DEFAULTS, data)


def save(prefs: dict) -> Path:
    p = prefs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prefs, ensure_ascii=False, indent=2) + "\n")
    os.chmod(p, 0o600)
    return p


def output_dir(prefs: dict) -> Path:
    d = Path(prefs.get("output_dir") or DEFAULTS["output_dir"]).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d
