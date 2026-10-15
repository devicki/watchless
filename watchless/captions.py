"""Caption track selection, WebVTT/SRT parsing + YouTube rolling-caption dedupe, and chunking.

Track-selection idea (original-language manual > auto original > fallbacks, with provenance labels)
is adapted from claude-video's select_caption() (MIT, (c) 2026 Bradley Bonanno). See NOTICE.
"""
from __future__ import annotations

import html
import re

CONFIDENCE = {"manual": "high", "auto": "medium", "auto-translated": "low"}


def base_lang(key: str | None) -> str:
    if not key:
        return ""
    k = key.removesuffix("-orig").lower()
    return k.split("-")[0].split("_")[0]


def _clean_tracks(d: dict | None) -> dict:
    return {k: v for k, v in (d or {}).items() if v and k not in ("live_chat", "rechat")}


# Place/name hints commonly found in titles (e.g. "TEDxKyoto") and tags. Small and generic on purpose.
_PLACE_HINTS = {
    "ja": ["kyoto", "tokyo", "osaka", "nagoya", "sapporo", "fukuoka", "kobe", "yokohama", "hiroshima", "sendai",
           "okinawa", "japan", "japanese", "nihon", "日本"],
    "ko": ["seoul", "busan", "incheon", "daegu", "daejeon", "gwangju", "korea", "korean", "한국"],
    "zh": ["beijing", "shanghai", "shenzhen", "guangzhou", "taipei", "chinese", "mandarin", "中文"],
    "fr": ["paris", "lyon", "marseille", "toulouse", "bordeaux", "french", "français"],
    "es": ["madrid", "barcelona", "sevilla", "valencia", "spanish", "español"],
    "de": ["berlin", "munich", "münchen", "hamburg", "wien", "german", "deutsch"],
}
_MANY_AUTO = 20  # more auto tracks than this means YouTube's translation list, not real ASR tracks


def script_language(text: str) -> dict:
    """Score languages by writing system. Hangul -> ko, kana -> ja; Han-only is ambiguous (ja/zh)."""
    if not text:
        return {}
    hangul = sum("\uac00" <= ch <= "\ud7a3" or "\u1100" <= ch <= "\u11ff" for ch in text)
    kana = sum("\u3040" <= ch <= "\u30ff" for ch in text)
    han = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    thai = sum("\u0e00" <= ch <= "\u0e7f" for ch in text)
    cyr = sum("\u0400" <= ch <= "\u04ff" for ch in text)
    out = {}
    if hangul >= 2:
        out["ko"] = 4
    if kana >= 2:
        out["ja"] = 4
    elif han >= 2 and not hangul:
        out["ja"] = out.get("ja", 0) + 1
        out["zh"] = out.get("zh", 0) + 1
    if thai >= 2:
        out["th"] = 4
    if cyr >= 3:
        out["ru"] = 2
    return out


def infer_language(info: dict) -> dict:
    """Infer the spoken language from evidence and compare with metadata.

    Strongest signal: the auto-caption '*-orig' track (YouTube's ASR of the actual audio).
    Then the writing system of title/description, place/language hints in title/tags/channel,
    real (non-translation) auto tracks, manual tracks, and finally yt-dlp's metadata 'language'.
    Returns {language, metadata_language, scores, evidence, mismatch}.
    """
    scores: dict[str, float] = {}
    evidence: list[str] = []

    def add(lang, pts, why):
        if lang:
            scores[lang] = scores.get(lang, 0) + pts
            evidence.append(f"{lang}+{pts:g}: {why}")

    manual = _clean_tracks(info.get("subtitles"))
    auto = _clean_tracks(info.get("automatic_captions"))
    for k in auto:
        if k.endswith("-orig"):
            add(base_lang(k), 10, f"auto-caption original track {k}")
    if len(auto) <= _MANY_AUTO:
        for k in auto:
            if not k.endswith("-orig"):
                add(base_lang(k), 1, f"auto track {k}")
    title, desc = info.get("title") or "", (info.get("description") or "")[:1000]
    for lang, pts in script_language(title).items():
        add(lang, pts, "title script")
    for lang, pts in script_language(desc).items():
        add(lang, min(pts, 2), "description script")
    hay = " ".join([title, info.get("channel") or "", info.get("uploader") or "",
                    " ".join(info.get("tags") or [])]).lower()
    for lang, words in _PLACE_HINTS.items():
        hits = [w for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", hay)
                or ("tedx" + w) in hay.replace(" ", "")]
        if hits:
            add(lang, min(3 + len(hits) - 1, 5), "title/tag/channel hints: " + ", ".join(hits[:3]))
    for k in manual:
        add(base_lang(k), 1, f"manual track {k}")
    meta = base_lang(info.get("language")) or None
    if meta:
        add(meta, 3, "metadata language")
    if not scores:
        return {"language": None, "metadata_language": meta, "scores": {}, "evidence": [], "mismatch": False}
    best = max(sorted(scores), key=lambda k: scores[k])
    # evidence excluding the metadata vote must be strong to override metadata
    if meta and best != meta and scores[best] - (scores.get(meta, 0) - 3) < 4:
        best = meta
    if scores[best] < 2 and not meta:  # e.g. only one manual track: too weak to call
        best_lang = None
    else:
        best_lang = best
    return {"language": best_lang, "metadata_language": meta, "scores": scores, "evidence": evidence,
            "mismatch": bool(meta and best_lang and best_lang != meta)}


def original_language(info: dict) -> str:
    """Best guess of the spoken language (see infer_language)."""
    return infer_language(info)["language"] or ""


def _pick(tracks: dict, lang: str) -> str | None:
    """Exact key first, then any regional variant of the same base language (shortest key wins)."""
    if not lang:
        return None
    if lang in tracks:
        return lang
    cands = sorted((k for k in tracks if base_lang(k) == lang), key=lambda k: (len(k), k))
    return cands[0] if cands else None


def select_track(info: dict, user_lang: str = "en", want: str = "auto") -> dict | None:
    """Pick one caption track.

    Order: manual(original) > manual(user lang) > auto(original, prefer *-orig) > manual(en) >
    any manual > auto translated into the user's language > None (caller falls back to ASR).
    `want` forces a language ("auto" = use the order above).
    """
    manual = _clean_tracks(info.get("subtitles"))
    auto = _clean_tracks(info.get("automatic_captions"))
    inferred = infer_language(info)
    orig = inferred["language"] or ""
    user = base_lang(user_lang) or "en"
    warnings = []
    if inferred["mismatch"]:
        warnings.append(f"metadata_language_mismatch: metadata says '{inferred['metadata_language']}', "
                        f"evidence says '{orig}' ({'; '.join(inferred['evidence'][:4])}). Using '{orig}'; "
                        "pass --lang to override.")

    def res(key, kind, why):
        lang = base_lang(key)
        source = kind
        if kind == "auto" and orig and lang != orig:
            source = "auto-translated"
        return {"key": key, "language": lang, "kind": kind, "source": source,
                "confidence": CONFIDENCE[source], "reason": why, "original_language": orig or None,
                "language_evidence": inferred["evidence"][:8], "warnings": warnings}

    if want and want != "auto":
        w = base_lang(want)
        if (k := _pick(manual, w)):
            return res(k, "manual", f"requested language {w}")
        orig_key = f"{w}-orig" if f"{w}-orig" in auto else _pick(auto, w)
        if orig_key:
            return res(orig_key, "auto", f"requested language {w}")
        return None

    if (k := _pick(manual, orig)):
        return res(k, "manual", "manual captions in the original language")
    if (k := _pick(manual, user)):
        return res(k, "manual", "manual captions in your language")
    if orig:
        if f"{orig}-orig" in auto:
            return res(f"{orig}-orig", "auto", "auto captions in the original language")
        if (k := _pick(auto, orig)):
            return res(k, "auto", "auto captions in the original language")
    if (k := _pick(manual, "en")):
        return res(k, "manual", "manual English captions (original language unknown)")
    if manual:
        k = sorted(manual)[0]
        return res(k, "manual", "only available manual track")
    if (k := _pick(auto, user)):
        return res(k, "auto", "machine-translated auto captions in your language")
    if auto:
        k = sorted(auto)[0]
        return res(k, "auto", "only available auto track")
    return None


def user_lang_track(info: dict, user_lang: str, primary: dict | None) -> dict | None:
    """A secondary track in the user's language (for translate/cross-check), if different from primary."""
    user = base_lang(user_lang)
    if not user or (primary and primary["language"] == user):
        return None
    t = select_track(info, user_lang, want=user)
    return t if t and (not primary or t["key"] != primary["key"]) else None


# ---------------------------------------------------------------- parsing

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_ARROW = re.compile(r"^\s*(\S+)\s+-->\s+(\S+)")
_TAG = re.compile(r"<[^>]*>")


def parse_ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        raise ValueError(f"bad timestamp: {s}")
    h, mi, se, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000


def fmt_ts(t: float) -> str:
    t = int(t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clean_line(line: str) -> str:
    line = html.unescape(_TAG.sub("", line))
    line = line.replace("\u200b", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", line).strip()


def parse_cues(text: str) -> list[dict]:
    """Parse WebVTT or SRT into raw cues [{start,end,lines}].

    Line-based on purpose: YouTube auto-captions use whitespace-only placeholder lines inside cues,
    so splitting on "blank-looking" lines would break cues apart.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues, cur = [], None
    for line in text.split("\n"):
        m = _ARROW.match(line)
        if m:
            try:
                cur = {"start": parse_ts(m.group(1)), "end": parse_ts(m.group(2)), "lines": []}
                cues.append(cur)
            except ValueError:
                cur = None
            continue
        if cur is None:
            continue
        if line == "":
            cur = None
            continue
        c = _clean_line(line)
        if c:
            cur["lines"].append(c)
    return [c for c in cues if c["lines"]]


def dedupe_cues(cues: list[dict]) -> list[dict]:
    """Collapse YouTube rolling auto-captions: emit only lines not already shown in the previous cue(s)."""
    out: list[dict] = []
    recent: list[str] = []
    for c in cues:
        new = [ln for ln in c["lines"] if ln not in recent]
        recent = (recent + c["lines"])[-4:]
        if not new:
            if out:
                out[-1]["end"] = max(out[-1]["end"], c["end"])
            continue
        text = " ".join(new)
        if out and out[-1]["text"] == text:
            out[-1]["end"] = max(out[-1]["end"], c["end"])
            continue
        out.append({"start": round(c["start"], 3), "end": round(c["end"], 3), "text": text})
    return out


_CREDIT = re.compile(r"^\s*(?:(?:translator|reviewer|transcriber|翻訳|校正|번역|검토|자막)\s*[:：]\s*"
                     r"\S+(?:\s\S+)?\s*)+", re.I)


_LABEL = re.compile(r"(?:translator|reviewer|transcriber|翻訳|校正|번역|검토|자막)\s*[:：]", re.I)


def strip_credits(segments: list[dict]) -> list[dict]:
    """Remove subtitle translator/reviewer credits (common on TED captions) from the first segments."""
    out = []
    for i, s in enumerate(segments):
        if i < 3 and _CREDIT.match(s["text"]):
            names_only = _LABEL.sub(" ", s["text"])
            t = "" if re.fullmatch(r"[A-Za-z .'\-]*", names_only) and not re.search(r"[?!.。]", names_only) \
                else _CREDIT.sub("", s["text"]).strip()
            if not t:
                continue
            s = {**s, "text": t}
        out.append(s)
    return out


def parse_subtitles(text: str) -> list[dict]:
    return strip_credits(dedupe_cues(parse_cues(text)))


def clip(segments: list[dict], start: float | None, end: float | None) -> list[dict]:
    return [s for s in segments
            if (start is None or s["end"] > start) and (end is None or s["start"] < end)]


# ---------------------------------------------------------------- grouping / chunking

def paragraphs(segments: list[dict], max_seconds: float = 45, max_chars: int = 600) -> list[dict]:
    """Group segments into readable paragraphs, breaking at sentence ends when possible."""
    out, cur = [], None
    for s in segments:
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
            continue
        long = s["end"] - cur["start"] > max_seconds or len(cur["text"]) > max_chars
        sentence_end = cur["text"].rstrip().endswith((".", "?", "!", "。", "？", "！", "…", "다", "요"))
        if long and (sentence_end or s["end"] - cur["start"] > max_seconds * 1.5):
            out.append(cur)
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
        else:
            sep = "" if cur["text"].endswith(("。", "、", "！", "？")) else " "
            cur["text"] += sep + s["text"]
            cur["end"] = s["end"]
    if cur:
        out.append(cur)
    return out


def _cjk_join(a: str, b: str) -> bool:
    """Japanese/Chinese text has no spaces between words."""
    def cj(ch):
        return "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff"
    return bool(a and b) and cj(a[-1]) and cj(b[0])


def chunk(segments: list[dict], max_chars: int = 12000) -> list[dict]:
    """Split segments into chunks under max_chars (for LLM context), at segment boundaries."""
    chunks, cur, size = [], [], 0
    for s in segments:
        n = len(s["text"]) + 1
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += n
    if cur:
        chunks.append(cur)
    return [{"index": i, "start": c[0]["start"], "end": c[-1]["end"],
             "chars": sum(len(x["text"]) + 1 for x in c), "segment_count": len(c)}
            for i, c in enumerate(chunks)]
