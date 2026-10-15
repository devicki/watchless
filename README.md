# Watchless

> Engine for the Watchless Grok Bot template.

**Watch less, know more.** Watchless turns a video (a YouTube or other yt-dlp-supported link, or a local file) into clean, timestamped text that people and AI agents can read: `transcript.json` + `transcript.md`.

It is the engine behind the *Watchless* Grok Bot template (quick summaries from a link, study notes, agent-friendly markdown, emotional-arc analysis, translate-then-summarize). The CLI itself only fetches and normalizes; summarizing is done by the agent.

## How it works

1. **Captions first, no media download.** Metadata is read with yt-dlp, then one caption track is picked:
   manual (original language) → manual (your language) → auto captions in the original language (`*-orig`) → manual English → any manual → auto captions machine-translated into your language → none.
2. **Clean-up.** WebVTT/SRT parsing, YouTube rolling-caption de-duplication, tag/entity stripping, readable paragraphs, chunking for LLM context.
3. **Speech-to-text fallback.** No captions → (after confirmation by default) audio-only download and local [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcription. Audio is decoded by ffmpeg, which avoids PyAV version issues.
4. **Optional frames.** `frames` extracts timestamped JPEGs (uniform or scene-aware) when visuals matter.
5. **Second track in your language.** If your language differs from the video's, a track in your language (often YouTube's machine translation) is saved too, for cross-checking translations.

Every result is labeled `source = manual | auto | auto-translated | asr` with a `confidence` (high / medium / low).

## Install

Requires Python 3.10+, ffmpeg, and (for YouTube) a JS runtime: deno, or node (used automatically).

```bash
git clone https://github.com/devicki/watchless && cd watchless
python3 -m venv .venv && .venv/bin/pip install -e .        # add ".[proxy]" for the optional pproxy forwarder
.venv/bin/watchless doctor --youtube
```

## Commands (all print one JSON object)

```bash
watchless doctor [--youtube]                          # tools, prefs, proxy, optional YouTube probe
watchless fetch <url|file> [--lang auto|en|ko|ja...] [--audio-fallback ask|auto|never] [--start 1:00 --end 5:00]
watchless transcribe <file> [--model small|medium] [--lang ko] [--out result.json]
watchless frames <url|file> --detail transcript|efficient|balanced [--start --end]
watchless text <output_dir> [--chunk N] [--lang ko]          # read the transcript one chunk at a time
watchless prefs [--set lang=ko asr.model=medium]
```

Exit codes: `0` ok, `2` error (JSON has `error` + actionable `message`), `3` needs confirmation (no captions; re-run with `--audio-fallback auto`).

Outputs go to `output_dir/<site-id>/`: `transcript.json` (metadata, track, segments, chunks, warnings), `transcript.md` (front matter + chapters + timestamped paragraphs), and `transcript.<lang>.json` for the secondary track.

## Preferences — `~/.watchless/prefs.json`

See `prefs.example.json`. Override the folder with `WATCHLESS_HOME`.

| key | meaning |
|---|---|
| `lang` | your main language (`en`, `ko`, `ja`, …) |
| `default_mode`, `length` | used by the bot skills (quick / study / agent-md / emotion / translate) |
| `asr.enabled`, `asr.model`, `asr.policy` | speech-to-text fallback; model `small` (default) or `medium`; policy `ask` / `auto` / `never` |
| `frames_detail` | default for `frames` |
| `proxy_url` | optional HTTP proxy for yt-dlp; health-checked before every URL fetch |
| `proxy_upstream` | optional pproxy chain; Watchless starts a local forwarder listening at `proxy_url` |
| `proxy_up_command` | optional script run when the proxy health check fails (e.g. bring a VPN/tunnel up) |
| `cookies_file` | optional exported cookies file for yt-dlp (use a throwaway account) |
| `output_dir` | where results are written |

## YouTube on cloud machines

Datacenter IPs are often blocked by YouTube ("Sign in to confirm you're not a bot", HTTP 429). Watchless reports these as `bot_check` / `rate_limited` with fixes. Options: a home/residential proxy (`proxy_url`), a cookies file, or uploading the video file instead. Keep `yt-dlp` up to date (`pip install -U yt-dlp`).

## Speech-to-text model

Measured on CPU (8 cores, int8) with 3 minutes of Korean and Japanese talk audio, compared against the videos' captions:

| model | Korean CER | Japanese CER | speed |
|---|---|---|---|
| small | ~0.10 | ~0.08 | ~12× real time |
| medium | ~0.12 | ~0.08 | ~4× real time |

`small` is the default. `medium` is a little better on names and loanwords and much slower. Models are downloaded on first use (small ≈ 0.5 GB, medium ≈ 1.5 GB).

## Development

```bash
.venv/bin/pip install -e ".[dev]" && .venv/bin/pytest -q
```

## Credits & license

MIT, see `LICENSE`. Parts of the design (caption-track selection order with provenance, captions-first / speech fallback, frame detail levels) are adapted from **[claude-video](https://github.com/bradautomates/claude-video)** by Bradley Bonanno (MIT). Its notice is in `NOTICE`.

Please respect creators' rights: use transcripts for personal study and summaries; don't republish them.

---

## 한국어 안내

**Watchless**는 영상 링크(유튜브 등)나 영상 파일을 사람과 AI 에이전트가 읽기 쉬운 텍스트(`transcript.json`, `transcript.md`)로 바꿔 주는 도구예요. Grok Bot 템플릿 *Watchless*의 엔진이고, 요약·학습 노트·감정선 분석·번역 요약은 봇이 이 결과를 읽고 만들어요.

- **자막 우선:** 영상을 내려받지 않고 자막만 가져와요. 원어 수동 자막 → 내 언어 수동 자막 → 원어 자동 자막 → 영어 수동 자막 → 기타 → 내 언어로 기계 번역된 자동 자막 순서로 골라요.
- **음성 인식 대체:** 자막이 없으면 확인을 받은 뒤 오디오만 받아서 faster-whisper로 받아써요. 기본 모델은 `small`이고, 더 정확해야 하면 `medium`을 쓰세요(약 3배 느려요).
- **출처 표시:** 결과마다 `manual`(수동 자막), `auto`(자동 자막), `auto-translated`(기계 번역 자막), `asr`(음성 인식) 중 어디서 왔는지와 신뢰도가 붙어요.
- **설정:** `~/.watchless/prefs.json`에 주 언어(`lang`), 음성 인식, 프록시 등을 저장해요. `watchless prefs --set lang=ko` 처럼 바꿀 수 있어요.
- **클라우드 서버에서 유튜브:** 데이터센터 IP는 유튜브가 막는 경우가 많아요("봇이 아닌지 확인", 429). 집 PC 프록시(`proxy_url`), 쿠키 파일, 또는 영상 파일 직접 전달로 해결할 수 있어요. 먼저 `watchless doctor --youtube`로 확인해 보세요.

설치와 명령어는 위 영어 안내를 참고하세요.
