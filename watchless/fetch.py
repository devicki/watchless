"""fetch: URL or local file -> transcript.json + transcript.md (captions first, ASR fallback)."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from watchless import captions as cap
from watchless import ytdlp
from watchless.ytdlp import FetchError

SUB_EXT = (".vtt", ".srt")


class NeedsConfirmation(RuntimeError):
    pass


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE).strip("-")
    return s[:60] or "video"


def run_dir(prefs: dict, key: str) -> Path:
    from watchless.prefs import output_dir
    d = output_dir(prefs) / key
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- URL path

def url_info(prefs: dict, url: str, d: Path) -> dict:
    ytdlp.run(prefs, ["--skip-download", "--write-info-json", "-o", str(d / "source.%(ext)s"), "--", url],
              timeout=180)
    p = d / "source.info.json"
    if not p.exists():
        raise FetchError("unknown", "yt-dlp did not write metadata.")
    return json.loads(p.read_text())


def download_track(prefs: dict, url: str, d: Path, track: dict) -> Path:
    kind_flag = "--write-subs" if track["kind"] == "manual" else "--write-auto-subs"
    ytdlp.run(prefs, ["--skip-download", kind_flag, "--sub-langs", "-all," + "^" + re.escape(track["key"]) + "$",
                      "--sub-format", "vtt/srt/best", "-o", str(d / "sub.%(ext)s"), "--", url], timeout=180)
    for ext in ("vtt", "srt"):
        f = d / f"sub.{track['key']}.{ext}"
        if f.exists() and f.stat().st_size:
            return f
    found = sorted(d.glob(f"sub.{track['key']}.*"))
    if found:
        return found[0]
    raise FetchError("unknown", f"Caption track {track['key']} could not be downloaded.")


def download_audio(prefs: dict, url: str, d: Path, start=None, end=None) -> Path:
    extra = ["-f", "ba[abr<=96]/worstaudio/ba/b", "-o", str(d / "audio.%(ext)s")]
    if start is not None or end is not None:
        extra += ["--download-sections", f"*{start or 0}-{end if end is not None else 'inf'}"]
    ytdlp.run(prefs, extra + ["--", url], timeout=1800)
    files = [f for f in d.glob("audio.*") if f.suffix not in (".part", ".json")]
    if not files:
        raise FetchError("unknown", "Audio download produced no file.")
    return files[0]


def download_video(prefs: dict, url: str, d: Path, start=None, end=None) -> Path:
    extra = ["-f", "bv*[height<=480]/b[height<=480]/worst", "-o", str(d / "video.%(ext)s")]
    if start is not None or end is not None:
        extra += ["--download-sections", f"*{start or 0}-{end if end is not None else 'inf'}"]
    ytdlp.run(prefs, extra + ["--", url], timeout=1800)
    files = [f for f in d.glob("video.*") if f.suffix not in (".part", ".json")]
    if not files:
        raise FetchError("unknown", "Video download produced no file.")
    return files[0]


def summarize_info(info: dict, url: str) -> dict:
    return {"title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
            "url": info.get("webpage_url") or url, "id": info.get("id"), "duration": info.get("duration"),
            "upload_date": info.get("upload_date"), "language": info.get("language"),
            "description": (info.get("description") or "")[:1500],
            "chapters": [{"start": c.get("start_time"), "title": c.get("title")} for c in info.get("chapters") or []],
            "tags": (info.get("tags") or [])[:20],
            "available_captions": {"manual": sorted(cap._clean_tracks(info.get("subtitles"))),
                                   "auto_original": [k for k in cap._clean_tracks(info.get("automatic_captions"))
                                                     if k.endswith("-orig")],
                                   "auto_count": len(cap._clean_tracks(info.get("automatic_captions")))}}


# ---------------------------------------------------------------- local path

def ffprobe(path: str) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise FetchError("unavailable", f"Not a readable media file: {path}")
    return json.loads(r.stdout)


def local_sidecars(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.glob(path.stem + "*") if p.suffix.lower() in SUB_EXT)


def extract_embedded_sub(path: str, stream_index: int, d: Path) -> Path | None:
    out = d / f"embedded_{stream_index}.vtt"
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", path, "-map", f"0:{stream_index}",
                        str(out)], capture_output=True)
    return out if r.returncode == 0 and out.exists() and out.stat().st_size else None


# ---------------------------------------------------------------- main

def fetch(source: str, prefs: dict, lang: str = "auto", audio_fallback: str | None = None,
          start: float | None = None, end: float | None = None, with_user_lang: bool = True) -> dict:
    t0 = time.time()
    user_lang = prefs.get("lang", "en")
    asr_cfg = prefs.get("asr", {})
    policy = audio_fallback or (asr_cfg.get("policy", "ask") if asr_cfg.get("enabled", True) else "never")
    result: dict = {"source_input": source, "user_lang": user_lang, "fetched_at":
                    datetime.now(timezone.utc).isoformat(timespec="seconds"), "range": {"start": start, "end": end}}
    segments, track, secondary = None, None, None

    if is_url(source):
        from watchless import proxy
        result["proxy"] = proxy.ensure(prefs)
        tmp_key = "url-" + hashlib.sha1(source.encode()).hexdigest()[:10]
        d = run_dir(prefs, tmp_key)
        info = url_info(prefs, source, d)
        key = slugify(f"{info.get('extractor_key', 'web')}-{info.get('id') or tmp_key}")
        if key != tmp_key:
            nd = run_dir(prefs, key)
            for f in d.iterdir():
                f.replace(nd / f.name)
            d.rmdir()
            d = nd
        meta = summarize_info(info, source)
        track = cap.select_track(info, user_lang, lang)
        if track:
            f = download_track(prefs, source, d, track)
            segments = cap.parse_subtitles(f.read_text(errors="ignore"))
            track["file"] = f.name
            if not segments:
                track = None
        if with_user_lang:
            sec = cap.user_lang_track(info, user_lang, track)
            if sec:
                try:
                    try:
                        f2 = download_track(prefs, source, d, sec)
                    except FetchError as e:  # translated tracks are often throttled; retry once
                        if e.code != "rate_limited":
                            raise
                        time.sleep(6)
                        f2 = download_track(prefs, source, d, sec)
                    sec_segs = cap.clip(cap.parse_subtitles(f2.read_text(errors="ignore")), start, end)
                    secondary = {**sec, "file": f2.name, "segment_count": len(sec_segs)}
                    (d / f"transcript.{sec['language']}.json").write_text(
                        json.dumps({"track": secondary, "segments": sec_segs}, ensure_ascii=False, indent=1))
                except FetchError as e:
                    secondary = {**sec, "error": str(e)[:200]}
        if not segments:
            if policy == "never":
                raise FetchError("no_captions", "No usable captions and audio fallback is disabled "
                                 "(--audio-fallback never / prefs.asr.enabled=false).")
            if policy == "ask":
                dur = meta.get("duration") or 0
                raise NeedsConfirmation(json.dumps({
                    "status": "needs_confirmation", "reason": "no_captions",
                    "message": (f"No captions found. Speech-to-text needs an audio download (~{max(1, int(dur / 60))} min "
                                f"of audio) and local transcription with model '{asr_cfg.get('model', 'small')}'. "
                                "Re-run with --audio-fallback auto to proceed."),
                    "metadata": meta, "output_dir": str(d)}, ensure_ascii=False))
            audio = download_audio(prefs, source, d, start, end)
            segments, track = _asr(str(audio), asr_cfg, lang, None, None)
            if start:  # sectioned download starts at `start`
                segments = [{**x, "start": round(x["start"] + start, 3), "end": round(x["end"] + start, 3)}
                            for x in segments]
            start_clip = end_clip = None
        else:
            start_clip, end_clip = start, end
    else:
        p = Path(source).expanduser().resolve()
        if not p.exists():
            raise FetchError("unavailable", f"File not found: {p}")
        d = run_dir(prefs, slugify("file-" + p.stem) + "-" + hashlib.sha1(str(p).encode()).hexdigest()[:6])
        pr = ffprobe(str(p))
        fmt = pr.get("format", {})
        meta = {"title": (fmt.get("tags") or {}).get("title") or p.name, "url": str(p), "id": None,
                "duration": float(fmt.get("duration") or 0) or None, "channel": None, "chapters": [],
                "language": None, "description": "", "tags": []}
        start_clip, end_clip = start, end
        for sc in local_sidecars(p):
            segments = cap.parse_subtitles(sc.read_text(errors="ignore"))
            if segments:
                lg = cap.base_lang(sc.stem[len(p.stem):].strip(".")) or None
                track = {"key": sc.name, "language": lg, "kind": "manual", "source": "manual",
                         "confidence": "high", "reason": "sidecar subtitle file", "file": str(sc)}
                break
        if not segments:
            for s in pr.get("streams", []):
                if s.get("codec_type") == "subtitle" and s.get("codec_name") not in ("hdmv_pgs_subtitle", "dvd_subtitle"):
                    f = extract_embedded_sub(str(p), s["index"], d)
                    if f:
                        segments = cap.parse_subtitles(f.read_text(errors="ignore"))
                        if segments:
                            lg = cap.base_lang((s.get("tags") or {}).get("language")) or None
                            track = {"key": f"stream{s['index']}", "language": lg, "kind": "manual",
                                     "source": "manual", "confidence": "high",
                                     "reason": "embedded subtitle stream", "file": f.name}
                            break
        if not segments:
            if policy == "never":
                raise FetchError("no_captions", "No subtitles in or next to the file and ASR is disabled.")
            segments, track = _asr(str(p), asr_cfg, lang, start, end)
            start_clip = end_clip = None

    segments = cap.clip(segments, start_clip, end_clip)
    warnings = []
    if not segments:
        warnings.append("no_speech: the transcript is empty (music-only or silent video?). "
                        "Try `watchless frames` for visual content.")
    elif track["source"] == "asr" and (track.get("language_probability") or 0) < 0.6:
        warnings.append("low_language_confidence: pass --lang <code> to force the spoken language.")
    result["warnings"] = warnings
    result.update({"metadata": meta, "track": track, "user_lang_track": secondary,
                   "source": track["source"], "confidence": track["confidence"],
                   "language": track.get("language"), "segment_count": len(segments),
                   "chunks": cap.chunk(segments), "output_dir": str(d)})
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    write_outputs(d, result, segments)
    result["files"] = {"json": str(d / "transcript.json"), "md": str(d / "transcript.md")}
    return result


def _asr(path: str, asr_cfg: dict, lang: str, start, end):
    from watchless import asr
    r = asr.transcribe(path, model=asr_cfg.get("model", "small"),
                       language=None if lang in (None, "", "auto") else cap.base_lang(lang), start=start, end=end)
    track = {"key": "asr", "language": r["language"], "kind": "asr", "source": "asr", "confidence": r["confidence"],
             "reason": f"speech-to-text (faster-whisper {r['model']})", "model": r["model"],
             "language_probability": r["language_probability"], "asr_seconds": r["elapsed_seconds"],
             "audio_seconds": r["audio_seconds"]}
    return r["segments"], track


def write_outputs(d: Path, result: dict, segments: list[dict]) -> None:
    (d / "transcript.json").write_text(json.dumps({**result, "segments": segments}, ensure_ascii=False, indent=1))
    m, t = result["metadata"], result["track"]
    lines = ["---", f"title: {json.dumps(m.get('title'), ensure_ascii=False)}",
             f"channel: {json.dumps(m.get('channel'), ensure_ascii=False)}", f"url: {m.get('url')}",
             f"duration: {cap.fmt_ts(m['duration']) if m.get('duration') else 'unknown'}",
             f"language: {t.get('language') or 'unknown'}", f"transcript_source: {t['source']}",
             f"confidence: {t['confidence']}", f"track: {t.get('key')}", f"fetched_at: {result['fetched_at']}",
             "generator: watchless", "---", "", f"# {m.get('title')}", ""]
    notes = {"auto": "Auto-generated captions: expect misheard words and no punctuation.",
             "auto-translated": "Machine-translated captions: meaning may drift; verify against the original.",
             "asr": "Speech-to-text transcript: names and technical terms may be wrong."}
    if t["source"] in notes:
        lines += [f"> Note: {notes[t['source']]}", ""]
    if m.get("chapters"):
        lines += ["## Chapters", ""] + [f"- [{cap.fmt_ts(c['start'] or 0)}] {c['title']}" for c in m["chapters"]] + [""]
    lines += ["## Transcript", ""]
    for p in cap.paragraphs(segments):
        lines += [f"**[{cap.fmt_ts(p['start'])}]** {p['text']}", ""]
    (d / "transcript.md").write_text("\n".join(lines))
