"""Kokoro voice: clean onsets, cached clips, and placing lines on a track.

Kokoro rushes the first sound of an utterance that starts cold, so every sentence is
spoken after a throwaway "Okay." and cut at the pause after it (word timestamps plus
the quietest 10 ms there). Each sentence is its own request so the pause between
sentences is ours (GAP), not the model's.
"""
import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.request
import wave

import numpy as np

SR = 24000
GAP = 0.40          # pause between two sentences inside one line
WARM = "Okay. "
LEAD = 0.03         # quiet kept in front of the first sound
PEAK = 0.90


def ensure_kokoro(cfg, log=print):
    url = cfg["kokoro_url"].rstrip("/") + "/v1/models"
    def up():
        try:
            return urllib.request.urlopen(url, timeout=3).status == 200
        except Exception:
            return False
    if up():
        return
    log("  Kokoro is not running; starting it")
    name, image = cfg["kokoro_container"], cfg["kokoro_image"]
    if subprocess.run(["docker", "info"], capture_output=True).returncode:
        log("  Docker is not running; starting Docker Desktop")
        subprocess.run(["open", "-a", "Docker"])
        for _ in range(60):
            time.sleep(3)
            if not subprocess.run(["docker", "info"], capture_output=True).returncode:
                break
        else:
            raise RuntimeError("Docker Desktop did not start")
    if subprocess.run(["docker", "start", name], capture_output=True).returncode:
        subprocess.run(["docker", "run", "-d", "--name", name, "-p", "8880:8880", image],
                       check=True, capture_output=True)
    for _ in range(90):
        if up():
            return
        time.sleep(4)
    raise RuntimeError("Kokoro did not come up on " + url)


def sentences(text):
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in ".!?":
            out.append(cur.strip()); cur = ""
    if cur.strip():
        out.append(cur.strip())
    return [s for s in out if s]


def _speak(cfg, text):
    body = json.dumps({"model": "kokoro", "voice": cfg["voice"], "input": WARM + text,
                       "response_format": "wav", "speed": cfg["speed"], "stream": False}).encode()
    req = urllib.request.Request(cfg["kokoro_url"].rstrip("/") + "/dev/captioned_speech", body,
                                 {"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=600))
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    raw = base64.b64decode(d["audio"])
    i = raw.find(b"data") + 8          # the header's size fields are bogus; read what follows 'data'
    a = np.frombuffer(raw[i:i + (len(raw) - i) // 2 * 2], "<i2").astype(np.float32) / 32768
    return a, d["timestamps"]


def _cut(a, ts):
    words = [t for t in ts if t["word"].strip() not in (".", ",", "")]
    if len(words) < 2:
        raise RuntimeError("Kokoro returned no words")
    fr = int(0.01 * SR)
    env = np.array([np.sqrt(np.mean(a[j:j + fr] ** 2)) for j in range(0, len(a) - fr, fr)])
    quiet = 0.02 * env.max()
    lo = int(words[0]["end_time"] * 100)
    hi = min(len(env) - 1, int(words[1]["start_time"] * 100) + 4)
    k = max([j for j in range(lo, hi + 1) if env[j] < quiet], default=None)
    if k is None:
        k = int(np.argmin(env[lo:hi + 1])) + lo
    b = a[max(0, (k + 1) * fr - int(LEAD * SR)):].copy()
    n = int(0.005 * SR); b[:n] *= np.linspace(0, 1, n)
    idx = np.where(np.abs(b) > 0.004)[0]
    b = b[:min(len(b), idx[-1] + int(0.15 * SR))] if len(idx) else b
    f = int(0.04 * SR); b[-f:] *= np.linspace(1, 0, f)
    return b


def line_clip(cfg, text, cache_dir):
    """Audio for one script line (one or two sentences), cached by text + voice + speed."""
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.sha1(f"v2|{cfg['voice']}|{cfg['speed']}|{text}".encode()).hexdigest()[:16]
    p = os.path.join(cache_dir, key + ".npy")
    if os.path.exists(p):
        return np.load(p)
    parts = []
    ss = sentences(text)
    for i, s in enumerate(ss):
        a, ts = _speak(cfg, s)
        parts.append(_cut(a, ts))
        if i < len(ss) - 1:
            parts.append(np.zeros(int(GAP * SR), np.float32))
    a = np.concatenate(parts)
    a = a * (PEAK / (np.abs(a).max() or 1))
    np.save(p, a)
    return a


def durations(cfg, lines, cache_dir):
    return [len(line_clip(cfg, l["text"], cache_dir)) / SR for l in lines]


def render_track(cfg, lines, total, cache_dir, out_wav):
    track = np.zeros(int(total * SR), np.float32)
    for l in lines:
        c = line_clip(cfg, l["text"], cache_dir)
        s0 = max(0, int((l["at"] - LEAD) * SR))     # the first SOUND lands on "at"
        seg = c[:len(track) - s0]
        track[s0:s0 + len(seg)] += seg
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype("<i2").tobytes())
