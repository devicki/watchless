"""Speech-to-text with faster-whisper.

Audio is decoded with ffmpeg to 16 kHz mono float32 PCM and passed as a numpy array. This sidesteps
PyAV/faster-whisper version incompatibilities (e.g. av>=15 'metadata_errors' TypeError).
"""
from __future__ import annotations

import subprocess
import time

SAMPLE_RATE = 16000


def decode_pcm(path: str, start: float | None = None, end: float | None = None):
    import numpy as np
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(path)]
    if end:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not decode audio: " + r.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(r.stdout, dtype=np.float32)


def transcribe(path: str, model: str = "small", language: str | None = None,
               start: float | None = None, end: float | None = None, compute_type: str = "int8") -> dict:
    from faster_whisper import WhisperModel
    t0 = time.time()
    audio = decode_pcm(path, start, end)
    if audio.size == 0:
        raise RuntimeError("No audio stream found.")
    m = WhisperModel(model, device="cpu", compute_type=compute_type)
    segs, info = m.transcribe(audio, language=(language or None), vad_filter=True, beam_size=5)
    offset = start or 0
    out = [{"start": round(s.start + offset, 3), "end": round(s.end + offset, 3), "text": s.text.strip()}
           for s in segs if s.text.strip()]
    prob = float(info.language_probability or 0)
    conf = "medium" if prob >= 0.8 and model not in ("tiny", "base") else "low"
    return {"segments": out, "language": info.language, "language_probability": round(prob, 3),
            "model": model, "confidence": conf, "audio_seconds": round(audio.size / SAMPLE_RATE, 1),
            "elapsed_seconds": round(time.time() - t0, 1)}
