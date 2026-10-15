from watchless import captions as c

T = [{"url": "x"}]


def info(manual=(), auto=(), language=None):
    return {"subtitles": {k: T for k in manual}, "automatic_captions": {k: T for k in auto}, "language": language}


def test_manual_original_first():
    t = c.select_track(info(["en", "ja", "ko"], ["en-orig", "ko"], "en"), "ko")
    assert t["key"] == "en" and t["source"] == "manual" and t["confidence"] == "high"


def test_manual_regional_variant():
    t = c.select_track(info(["en-US", "fr"], ["en-orig"], "en"), "ko")
    assert t["key"] == "en-US"


def test_manual_user_lang_when_no_original_manual():
    t = c.select_track(info(["ko"], ["ja-orig", "ja", "ko"], "ja"), "ko")
    assert t["key"] == "ko" and t["source"] == "manual"


def test_auto_orig_preferred():
    t = c.select_track(info([], ["ja", "ja-orig", "ko", "en"], "ja"), "ko")
    assert t["key"] == "ja-orig" and t["source"] == "auto" and t["confidence"] == "medium"


def test_original_from_orig_key_when_language_missing():
    t = c.select_track(info([], ["ko-orig", "en"]), "en")
    assert t["key"] == "ko-orig" and t["original_language"] == "ko"


def test_unknown_original_prefers_english_manual():
    t = c.select_track(info(["de", "en"], []), "ja")
    assert t["key"] == "en"


def test_auto_translated_label():
    t = c.select_track(info([], ["fr", "ko"], "es"), "ko")
    assert t["key"] == "ko" and t["source"] == "auto-translated" and t["confidence"] == "low"


def test_none_when_no_tracks():
    assert c.select_track(info(), "en") is None


def test_live_chat_ignored():
    i = info()
    i["subtitles"] = {"live_chat": T}
    assert c.select_track(i, "en") is None


def test_forced_language():
    i = info(["en"], ["en-orig", "ko"], "en")
    t = c.select_track(i, "en", want="ko")
    assert t["key"] == "ko" and t["source"] == "auto-translated"
    assert c.select_track(i, "en", want="th") is None


def test_user_lang_track():
    i = info(["en"], ["en-orig", "ko"], "en")
    p = c.select_track(i, "ko")
    s = c.user_lang_track(i, "ko", p)
    assert s["key"] == "ko" and s["source"] == "auto-translated"
    assert c.user_lang_track(i, "en", p) is None


ROLLING = """WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.869 align:start position:0%
 
hello<00:00:00.640><c> world</c>

00:00:02.869 --> 00:00:02.879 align:start position:0%
hello world
 

00:00:02.879 --> 00:00:05.200 align:start position:0%
hello world
this<00:00:03.200><c> is</c><00:00:03.500><c> a</c><00:00:03.800><c> test</c>

00:00:05.200 --> 00:00:05.210 align:start position:0%
this is a test
 

00:00:05.210 --> 00:00:07.000 align:start position:0%
this is a test
of &amp; captions
"""


def test_rolling_dedupe():
    segs = c.parse_subtitles(ROLLING)
    assert [s["text"] for s in segs] == ["hello world", "this is a test", "of & captions"]
    assert segs[0]["start"] == 0.16 and segs[1]["start"] == 2.879


def test_srt_and_multiline():
    srt = "1\n00:00:01,000 --> 00:00:03,500\n<i>Line one</i>\nline two\n\n2\n00:01:02,000 --> 00:01:04,000\n안녕하세요\n"
    segs = c.parse_subtitles(srt)
    assert segs[0]["text"] == "Line one line two" and segs[1]["start"] == 62.0 and segs[1]["text"] == "안녕하세요"


def test_hour_timestamps_and_fmt():
    assert c.parse_ts("01:02:03.500") == 3723.5
    assert c.fmt_ts(3723.5) == "1:02:03" and c.fmt_ts(65) == "01:05"


def test_clip():
    segs = [{"start": i * 10, "end": i * 10 + 9, "text": str(i)} for i in range(10)]
    assert [s["text"] for s in c.clip(segs, 25, 55)] == ["2", "3", "4", "5"]


def test_chunking_boundaries():
    segs = [{"start": i, "end": i + 1, "text": "x" * 99} for i in range(250)]
    ch = c.chunk(segs, max_chars=5000)
    assert sum(x["segment_count"] for x in ch) == 250
    assert all(x["chars"] <= 5000 for x in ch) and ch[1]["start"] == ch[0]["segment_count"]


def test_chunk_oversized_segment_alone():
    ch = c.chunk([{"start": 0, "end": 1, "text": "y" * 50}, {"start": 1, "end": 2, "text": "z" * 500}], 100)
    assert [x["segment_count"] for x in ch] == [1, 1]


def test_paragraphs_cjk_join():
    segs = [{"start": 0, "end": 2, "text": "今日は晴れです。"}, {"start": 2, "end": 4, "text": "明日は雨"},
            {"start": 4, "end": 6, "text": "でしょう"}]
    assert c.paragraphs(segs)[0]["text"] == "今日は晴れです。明日は雨 でしょう"


def test_strip_credits():
    segs = [{"start": 0, "end": 1, "text": "Translator: Gustavo Rocha Reviewer: Ariana Bleau"},
            {"start": 1, "end": 2, "text": "Hear that?"}]
    assert [s["text"] for s in c.strip_credits(segs)] == ["Hear that?"]
    j = [{"start": 0, "end": 1, "text": "翻訳: Hiroko Kawano 校正: Kyoko Kamura 皆さん"}]
    assert c.strip_credits(j)[0]["text"] == "皆さん"


def _vm7(language="fr"):
    """Mocked TEDxKyoto info (VM7Zk_J6gfQ): English title, metadata language fr, manual fr+ja."""
    return {"title": "Traditional Japanese dyes on the path to eco-friendly fashion | Toru Arakawa | TEDxKyoto",
            "channel": "TEDx Talks", "language": language,
            "tags": ["Japan", "Culture", "tedx", "Japanese", "Design"],
            "description": "This talk was given at a local TEDx event...",
            "subtitles": {"fr": T, "ja": T}, "automatic_captions": {"fr": T, "ja": T}}


def test_metadata_mismatch_trusts_evidence():
    t = c.select_track(_vm7("fr"), "ko")
    assert t["key"] == "ja" and t["source"] == "manual"
    assert any(w.startswith("metadata_language_mismatch") for w in t["warnings"])


def test_vm7_without_metadata_language():
    t = c.select_track(_vm7(None), "ko")
    assert t["key"] == "ja" and t["warnings"] == []


def test_orig_track_beats_metadata_and_hints():
    i = {"title": "東京の旅 | TEDxTokyo", "language": "en", "subtitles": {"en": T, "ja": T},
         "automatic_captions": {"ko-orig": T, **{f"x{n}": T for n in range(40)}}}
    inf = c.infer_language(i)
    assert inf["language"] == "ko" and inf["mismatch"]


def test_weak_hint_does_not_override_metadata():
    i = {"title": "My weekend in Tokyo", "language": "en", "subtitles": {"en": T, "ja": T}, "automatic_captions": {}}
    assert c.infer_language(i)["language"] == "en"
    assert c.select_track(i, "ko")["key"] == "en"


def test_script_detection():
    assert c.script_language("안녕하세요 여러분")["ko"] == 4
    assert c.script_language("こんにちは世界")["ja"] == 4
    s = c.script_language("北京大学")
    assert s["ja"] == s["zh"] == 1
    assert c.infer_language({"title": "인생을 바꾸는 글쓰기", "subtitles": {"en": T, "ko": T}})["language"] == "ko"


def test_translation_list_not_counted_as_evidence():
    auto = {f"l{n}": T for n in range(50)}
    auto["fr"] = T
    inf = c.infer_language({"title": "x", "language": None, "automatic_captions": auto})
    assert inf["language"] is None


def test_multiple_orig_tracks_resolved_by_other_evidence():
    # dubbed/multi-audio videos expose several '*-orig' tracks; script + metadata break the tie
    i = {"title": "삶의 주도권을 놓치게 되는 실수 | 세바시", "language": "ko", "subtitles": {},
         "automatic_captions": {"en-orig": T, "id-orig": T, "ko-orig": T, **{f"x{n}": T for n in range(40)}}}
    t = c.select_track(i, "en")
    assert t["key"] == "ko-orig" and t["warnings"] == []
