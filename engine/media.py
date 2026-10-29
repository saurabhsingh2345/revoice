"""Everything that touches ffmpeg / whisperX: probing, frames, transcripts, the final mux."""
import glob
import json
import os
import subprocess

import numpy as np


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, **kw)
    if r.returncode:
        err = r.stderr.decode(errors="ignore") if isinstance(r.stderr, bytes) else r.stderr
        raise RuntimeError(f"{cmd[0]} failed: {err[-800:]}")
    return r


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,width,height,avg_frame_rate",
               "-of", "json", path], text=True).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    n, den = v["avg_frame_rate"].split("/")
    return {"duration": float(d["format"]["duration"]), "w": v["width"], "h": v["height"],
            "fps": float(n) / float(den or 1),
            "has_audio": any(s["codec_type"] == "audio" for s in d["streams"])}


def to_16k(src, dst):
    run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", dst])


def transcribe(cfg, wav):
    """Word timings from whisperX: [{"word", "start", "end"}] in seconds."""
    r = run([cfg["python"], cfg["align_script"], "--audio", wav], text=True)
    words = json.loads(r.stdout)["words"]
    return [{"word": w["word"], "start": w["startMs"] / 1000, "end": w["endMs"] / 1000} for w in words]


def phrases(words, gap=0.9):
    """Group words into spoken bursts separated by >= gap seconds of silence."""
    out, cur = [], None
    for w in words:
        if cur and w["start"] - cur["end"] < gap:
            cur["end"] = w["end"]; cur["text"] += " " + w["word"]
        else:
            if cur:
                out.append(cur)
            cur = {"start": w["start"], "end": w["end"], "text": w["word"]}
    if cur:
        out.append(cur)
    return out


def scene_changes(src, thresh=0.02, merge=0.4):
    err = subprocess.run(["ffmpeg", "-i", src, "-vf", f"select='gt(scene,{thresh})',showinfo",
                          "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    ts = []
    for part in err.split("pts_time:")[1:]:
        t = float(part.split()[0])
        if not ts or t - ts[-1] > merge:
            ts.append(round(t, 2))
    return ts


def contact_sheets(src, outdir, duration, step=0.5, cols=6, rows=5, cell_w=320):
    """Sheets of frames every `step` s; returns [(path, t0, t1)]. Cells read row by row."""
    os.makedirs(outdir, exist_ok=True)
    for f in glob.glob(os.path.join(outdir, "sheet_*.jpg")):
        os.remove(f)
    run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf",
         f"fps={1/step},scale={cell_w}:-1,tile={cols}x{rows}", "-q:v", "3",
         os.path.join(outdir, "sheet_%03d.jpg")])
    per = cols * rows * step
    sheets = sorted(glob.glob(os.path.join(outdir, "sheet_*.jpg")))
    return [(p, round(i * per, 2), round(min(duration, (i + 1) * per), 2)) for i, p in enumerate(sheets)]


def keyframes(src, outdir, times, width=1400):
    os.makedirs(outdir, exist_ok=True)
    out = []
    for t in times:
        p = os.path.join(outdir, f"key_{t:08.2f}.jpg")
        if not os.path.exists(p):
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", src, "-frames:v", "1",
                 "-vf", f"scale={width}:-1", "-q:v", "3", p])
        out.append((p, t))
    return out


def lufs(path, start=None, end=None):
    cmd = ["ffmpeg", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", str(start), "-to", str(end)]
    cmd += ["-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    lines = [l for l in err.splitlines() if l.strip().startswith("I:")]
    return float(lines[-1].split()[1])


def normalize(src_wav, dst_wav, target=-16.0):
    """Two-pass loudnorm -> 48 kHz -> limiter last (loudnorm emits 192 kHz; resample first)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", src_wav, "-af",
                          f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = json.loads(err[err.rfind("{"):err.rfind("}") + 1])
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true,aresample=48000,alimiter=limit=0.841:level=false")
    run(["ffmpeg", "-v", "error", "-y", "-i", src_wav, "-af", af, "-ar", "48000",
         "-c:a", "pcm_s16le", dst_wav])


def mux(video, audio_wav, out):
    """Original picture copied untouched; the mono voice spread to both channels at -3 dB each
    (same loudness as -ac 2, but without the one-sample L/R glitch -ac 2 once produced)."""
    tmp = out + ".part.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", audio_wav, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-af", "pan=stereo|c0=0.7071*c0|c1=0.7071*c0",
         "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", tmp])
    os.replace(tmp, out)


def channel_peaks(path):
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-c:a", "pcm_f32le", "-f", "f32le", "-"]).stdout
    a = np.frombuffer(raw, "<f4").reshape(-1, 2)
    return float(np.abs(a).max()), float(np.abs(a[:, 0] - a[:, 1]).max())
