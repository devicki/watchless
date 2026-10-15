"""watchless CLI. Every command prints one JSON object to stdout.

Exit codes: 0 ok, 2 error, 3 needs user confirmation (e.g. audio download for ASR).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from watchless import __version__, prefs as P


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def parse_time(s):
    if s is None:
        return None
    parts = [float(x) for x in str(s).split(":")]
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t


def _ver(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (r.stdout or r.stderr).strip().splitlines()[0][:80]
    except Exception:  # noqa: BLE001
        return None


def cmd_doctor(a):
    pr = P.load()
    checks = {"watchless": __version__, "python": sys.version.split()[0], "prefs_path": str(P.prefs_path()),
              "prefs_exists": P.prefs_path().exists(),
              "ffmpeg": _ver(["ffmpeg", "-version"]) if shutil.which("ffmpeg") else None,
              "ffprobe": bool(shutil.which("ffprobe")),
              "yt_dlp": _ver([sys.executable, "-m", "yt_dlp", "--version"]),
              "js_runtime": "deno" if shutil.which("deno") else ("node" if shutil.which("node") else None)}
    try:
        import faster_whisper  # noqa: F401
        checks["faster_whisper"] = getattr(faster_whisper, "__version__", "installed")
    except Exception:  # noqa: BLE001
        checks["faster_whisper"] = None
    prefs_view = {k: v for k, v in pr.items() if k not in ("proxy_upstream", "cookies_file", "proxy_up_command")}
    prefs_view["proxy_upstream_set"] = bool(pr.get("proxy_upstream"))
    prefs_view["proxy_up_command_set"] = bool(pr.get("proxy_up_command"))
    prefs_view["cookies_file_set"] = bool(pr.get("cookies_file"))
    problems = []
    if not checks["ffmpeg"]:
        problems.append("ffmpeg missing: install it (apt install ffmpeg / brew install ffmpeg).")
    if not checks["yt_dlp"]:
        problems.append("yt-dlp missing: pip install -U 'yt-dlp[default]'.")
    if not checks["js_runtime"]:
        problems.append("No JS runtime (deno or node): YouTube extraction may miss formats.")
    if not checks["faster_whisper"]:
        problems.append("faster-whisper missing: speech-to-text fallback disabled (pip install faster-whisper).")
    from watchless import proxy
    try:
        checks["proxy"] = proxy.ensure(pr)
    except proxy.ProxyError as e:
        checks["proxy"] = {"status": "error", "message": str(e)}
        problems.append(str(e))
    if a.youtube:
        from watchless import ytdlp
        try:
            ytdlp.run(pr, ["--skip-download", "--print", "title", "--", "https://www.youtube.com/watch?v=jNQXAC9IVRw"],
                      timeout=120)
            checks["youtube"] = "ok"
        except ytdlp.FetchError as e:
            checks["youtube"] = {"status": e.code, "message": str(e)}
            problems.append(str(e))
    out({"ok": not problems, "checks": checks, "prefs": prefs_view, "problems": problems}, 0 if not problems else 2)


def cmd_fetch(a):
    from watchless import fetch as F
    from watchless.proxy import ProxyError
    pr = P.load()
    try:
        r = F.fetch(a.source, pr, lang=a.lang, audio_fallback=a.audio_fallback,
                    start=parse_time(a.start), end=parse_time(a.end), with_user_lang=not a.no_user_lang)
    except F.NeedsConfirmation as e:
        out(json.loads(str(e)), 3)
    except F.FetchError as e:
        out({"ok": False, "error": e.code, "message": str(e)}, 2)
    except ProxyError as e:
        out({"ok": False, "error": "proxy", "message": str(e)}, 2)
    out({"ok": True, **r})


def cmd_transcribe(a):
    from watchless import asr
    pr = P.load()
    try:
        r = asr.transcribe(a.path, model=a.model or pr["asr"].get("model", "small"),
                           language=None if a.lang in (None, "auto") else a.lang,
                           start=parse_time(a.start), end=parse_time(a.end))
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": "asr", "message": f"{type(e).__name__}: {e}"[:400]}, 2)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1))
        r = {k: v for k, v in r.items() if k != "segments"} | {"segment_count": len(r["segments"]), "file": a.out}
    out({"ok": True, **r})


def cmd_frames(a):
    from watchless import fetch as F, frames
    pr = P.load()
    detail = a.detail or pr.get("frames_detail", "balanced")
    start, end = parse_time(a.start), parse_time(a.end)
    try:
        if F.is_url(a.source):
            from watchless import proxy
            proxy.ensure(pr)
            import hashlib
            d = F.run_dir(pr, "frames-" + hashlib.sha1(a.source.encode()).hexdigest()[:10])
            if detail == "transcript":
                out({"ok": True, "detail": detail, "frames": []})
            video = str(F.download_video(pr, a.source, d, start, end))
            offset = start or 0
            fr = frames.extract(video, d, detail)
            for f in fr:
                f["time"] = round(f["time"] + offset, 2)
        else:
            p = Path(a.source).expanduser().resolve()
            d = F.run_dir(pr, F.slugify("file-" + p.stem) + "-frames")
            fr = frames.extract(str(p), d, detail, start, end)
        if F.is_url(a.source) and start:
            from watchless.captions import fmt_ts
            for f in fr:
                f["timestamp"] = fmt_ts(f["time"])
        (d / "frames.json").write_text(json.dumps(fr, indent=1))
        out({"ok": True, "detail": detail, "count": len(fr), "frames_json": str(d / "frames.json"), "frames": fr})
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", "error")
        out({"ok": False, "error": code, "message": str(e)[:400]}, 2)


def cmd_prefs(a):
    pr = P.load()
    if a.set:
        for kv in a.set:
            k, _, v = kv.partition("=")
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
            cur = pr
            parts = k.split(".")
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = v
        P.save(pr)
    view = {k: ("<set>" if k in ("proxy_upstream", "cookies_file", "proxy_up_command") and v else v)
            for k, v in pr.items()}
    out({"ok": True, "path": str(P.prefs_path()), "prefs": view})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="watchless", description="Turn videos into clean, timestamped text.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="check tools, prefs, proxy")
    d.add_argument("--youtube", action="store_true", help="also try a tiny YouTube metadata request")
    d.set_defaults(fn=cmd_doctor)
    f = sub.add_parser("fetch", help="URL or file -> transcript.json + transcript.md")
    f.add_argument("source")
    f.add_argument("--lang", default="auto", help="force caption/ASR language (default: auto)")
    f.add_argument("--audio-fallback", choices=["ask", "auto", "never"])
    f.add_argument("--start")
    f.add_argument("--end")
    f.add_argument("--no-user-lang", action="store_true", help="skip the secondary user-language track")
    f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("transcribe", help="speech-to-text a local audio/video file")
    t.add_argument("path")
    t.add_argument("--model")
    t.add_argument("--lang", default="auto")
    t.add_argument("--start")
    t.add_argument("--end")
    t.add_argument("--out", help="write full result JSON here")
    t.set_defaults(fn=cmd_transcribe)
    fr = sub.add_parser("frames", help="extract timestamped frames")
    fr.add_argument("source")
    fr.add_argument("--detail", choices=["transcript", "efficient", "balanced"])
    fr.add_argument("--start")
    fr.add_argument("--end")
    fr.set_defaults(fn=cmd_frames)
    pp = sub.add_parser("prefs", help="show or set prefs (key=value, dotted keys)")
    pp.add_argument("--set", nargs="*")
    pp.set_defaults(fn=cmd_prefs)
    a = ap.parse_args(argv)
    a.fn(a)
