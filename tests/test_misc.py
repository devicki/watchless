import json

from watchless import frames, prefs, ytdlp


def test_classify():
    assert ytdlp.classify("ERROR: Sign in to confirm you’re not a bot.") == "bot_check"
    assert ytdlp.classify("HTTP Error 429: Too Many Requests") == "rate_limited"
    assert ytdlp.classify("ERROR: Private video") == "unavailable"
    assert ytdlp.classify("Remote end closed connection without response") == "network"


def test_pick_times():
    assert frames.pick_times(100, "transcript") == []
    t = frames.pick_times(1000, "efficient")
    assert len(t) == 50 and t == sorted(t)
    b = frames.pick_times(600, "balanced", scenes=[10.0, 200.0, 201.0])
    assert 10.0 in b and len(b) <= 100
    many = frames.pick_times(3000, "balanced", scenes=[float(i) for i in range(500)])
    assert len(many) == 100


def test_prefs_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHLESS_HOME", str(tmp_path))
    p = prefs.load()
    assert p["lang"] == "en" and p["asr"]["model"] == "small"
    p["lang"] = "ko"
    prefs.save({"lang": "ko", "asr": {"model": "medium"}})
    q = prefs.load()
    assert q["lang"] == "ko" and q["asr"]["model"] == "medium" and q["asr"]["enabled"] is True
    assert json.loads((tmp_path / "prefs.json").read_text())["lang"] == "ko"


def test_proxy_not_configured():
    from watchless import proxy
    assert proxy.ensure({"proxy_url": ""})["status"] == "not configured"
