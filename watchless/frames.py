"""Frame extraction with ffmpeg: efficient (uniform, cap 50) or balanced (scene-aware, cap 100)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

CAPS = {"transcript": 0, "efficient": 50, "balanced": 100}


def probe_duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def _grab(path: str, t: float, out: Path) -> bool:
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
                        "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "4", str(out)],
                       capture_output=True)
    return r.returncode == 0 and out.exists()


def scene_times(path: str, threshold: float = 0.3) -> list[float]:
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", path, "-vf",
                        f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def pick_times(duration: float, detail: str, scenes: list[float] | None = None,
               start: float = 0, end: float | None = None) -> list[float]:
    cap = CAPS[detail]
    end = min(end or duration, duration) if duration else (end or 0)
    span = max(end - start, 0)
    if not cap or span <= 0:
        return []
    n = min(cap, max(1, int(span // 5)))  # at most one frame per 5s
    uniform = [start + span * (i + 0.5) / n for i in range(n)]
    if detail == "efficient" or not scenes:
        return [round(t, 2) for t in uniform]
    sc = [t for t in scenes if start <= t <= end]
    if len(sc) >= cap:  # keep evenly spaced subset of scene cuts
        step = len(sc) / cap
        sc = [sc[int(i * step)] for i in range(cap)]
        return [round(t, 2) for t in sc]
    # fill gaps with uniform samples not within 3s of a scene cut
    fill = [t for t in uniform if all(abs(t - s) > 3 for s in sc)]
    return [round(t, 2) for t in sorted(sc + fill[: cap - len(sc)])]


def extract(path: str, out_dir: Path, detail: str = "balanced",
            start: float | None = None, end: float | None = None) -> list[dict]:
    from watchless.captions import fmt_ts
    if detail == "transcript":
        return []
    dur = probe_duration(path)
    scenes = scene_times(path) if detail == "balanced" else None
    times = pick_times(dur, detail, scenes, start or 0, end)
    fd = out_dir / "frames"
    fd.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(times, 1):
        f = fd / f"frame_{i:03d}_{fmt_ts(t).replace(':', '-')}.jpg"
        if _grab(path, t, f):
            frames.append({"time": t, "timestamp": fmt_ts(t), "path": str(f)})
    return frames
