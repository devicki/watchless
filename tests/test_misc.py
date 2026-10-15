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


def test_text_command(tmp_path, capsys):
    import pytest
    from watchless import cli
    segs = [{"start": i * 10, "end": i * 10 + 9, "text": f"line {i}."} for i in range(30)]
    (tmp_path / "transcript.json").write_text(json.dumps({"segments": segs, "track": {"source": "manual"}}))
    with pytest.raises(SystemExit) as e:
        cli.main(["text", str(tmp_path), "--chunk", "1", "--max-chars", "100"])
    assert e.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["chunk_count"] > 1 and not out["text"].startswith("[00:00]")


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _prefs(**kw):
    return {"proxy_url": "http://127.0.0.1:9", "proxy_upstream": "socks5://x", **kw}


def test_proxy_ok_first_try():
    from watchless import proxy
    r = proxy.ensure(_prefs(), _probe=lambda u, t: (True, "HTTP 204"), _forwarder=lambda u, up: "up")
    assert r["status"] == "ok" and r["probe_attempts"] == 1 and not r["warmed_up"]


def test_proxy_warmup_runs_up_command_and_polls():
    from watchless import proxy
    clock, calls = FakeClock(), {"probe": 0, "up": 0, "fwd": 0}

    def probe(u, t):
        calls["probe"] += 1
        assert t <= 4  # short probes only
        clock.t += t / 4
        return (calls["probe"] >= 4, "HTTP 204" if calls["probe"] >= 4 else "timeout")

    def fwd(u, up):
        calls["fwd"] += 1
        return "started" if calls["fwd"] == 1 else "up"

    def up(cmd):
        calls["up"] += 1
        return "exit 0"
    r = proxy.ensure(_prefs(proxy_up_command="up.sh"), _probe=probe, _forwarder=fwd, _up=up,
                     _sleep=clock.sleep, _now=clock.now)
    assert r["status"] == "ok" and r["warmed_up"] and calls["up"] == 1 and r["probe_attempts"] == 4
    assert r["forwarder"] == "started" and r["up_command"] == "exit 0"


def test_proxy_gives_up_within_deadline():
    import pytest
    from watchless import proxy
    clock = FakeClock()

    def probe(u, t):
        clock.t += t
        return False, "timeout"
    with pytest.raises(proxy.ProxyError) as e:
        proxy.ensure(_prefs(), wait_seconds=20, _probe=probe, _forwarder=lambda u, up: "up",
                     _sleep=clock.sleep, _now=clock.now)
    assert clock.t < 30 and "warm-up" in str(e.value)
