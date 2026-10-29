#!/usr/bin/env python3
"""edit — cut re-voiced scenes into one finished video per part.

    edit <folder> [--out DIR] [--only "part 2"]

Reads <folder>/revoiced (the output of `revoice <folder>`) and groups the scenes by the
"part N ... scene M" in their file names. For each part, in scene order:
  - every silent stretch where the picture just sits (Codex working, slow typing) is
    fast-forwarded, with a small "▶▶ N×" badge while it runs; speech is never sped up
    or cut, and each line keeps ~1 s of normal speed on either side
  - Codex waits shrink to ~3-4 s; silent typing plays at >= 3x (at most ~6 s per gap)
  - every scene goes into a 1920x1080 frame, centred, with a blurred copy of itself
    filling the sides (the scenes come in different widths)
  - scenes are joined with hard cuts, the audio is rebuilt sample-exact and checked
Writes <out>/<Ep ... part N>.mp4 plus <out>/.reports/<part>.md listing every fast-forward.
"""
import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import media  # noqa: E402

W, H, FPS, SR = 1920, 1080, 60, 48000
KEEP = 1.0          # seconds of normal speed kept on each side of speech
MIN_FF = 3.0        # a silent stretch shorter than this (after KEEP) is left alone
WAIT_OUT = 3.5      # a Codex wait plays for about this long
TYPE_SPEED = 3.0    # silent typing plays at least this fast...
TYPE_OUT = 6.0      # ...and never longer than this per gap
NICE = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40]
BADGE_X, BADGE_Y = W - 150, 24   # sits in the blurred side strip, off the picture


def slug(name):
    return "".join(c if c.isalnum() else "_" for c in os.path.splitext(name)[0]).strip("_")[:80]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(cmd[:6]) + " ...\n" + r.stderr[-2000:])
    return r


def part_key(name):
    m = re.search(r"part\s*(\d+)", name, re.I)
    s = re.search(r"scene?\s*(\d+)", name, re.I)
    return (int(m.group(1)) if m else 0, int(s.group(1)) if s else 0)


def nice_speed(x):
    """The smallest round speed >= x (so a sped stretch never runs longer than planned)."""
    for n in NICE:
        if n >= x - 1e-6:
            return n
    return NICE[-1]


def speech_spans(words):
    spans = []
    for w in words:
        if spans and w["start"] - spans[-1][1] < 0.05:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    return spans


def is_typing(a, b, script):
    """Is the silent stretch a-b somebody typing (vs. Codex working / idle)?"""
    for e in script.get("events", []):
        if a - 1 <= e["t"] <= b + 1 and re.search(r"\btyp", e["what"], re.I):
            return True
    lines = script["lines"]
    prev = [l for l in lines if l["at"] <= a]
    nxt = [l for l in lines if l["at"] >= b]
    return bool((prev and prev[-1].get("typing")) or (nxt and nxt[0].get("typing") and nxt[0]["at"] - b < 3))


def plan(dur, words, script):
    """Piecewise speed map: [(src_a, src_b, speed, kind)] covering 0..dur."""
    spans = speech_spans(words)
    gaps, p = [], 0.0
    for a, b in spans:
        gaps.append((p, a, p == 0.0))
        p = b
    gaps.append((p, dur, False))
    segs, cur = [], 0.0
    for a, b, first in gaps:
        lo = a + (0.6 if first else KEEP)       # clip start: keep a short beat before the first line
        hi = b - KEEP if b < dur else b - 0.6   # clip end: a short beat after the last line
        if hi - lo < MIN_FF:
            continue
        m = hi - lo
        if is_typing(lo, hi, script):
            kind, speed = "typing", nice_speed(max(TYPE_SPEED, m / TYPE_OUT))
        else:
            kind, speed = "waiting", nice_speed(max(2.0, m / WAIT_OUT))
        if lo > cur:
            segs.append((cur, lo, 1, "normal"))
        segs.append((lo, hi, speed, kind))
        cur = hi
    if cur < dur:
        segs.append((cur, dur, 1, "normal"))
    return segs


def out_times(segs):
    o, t = [], 0.0
    for a, b, s, _ in segs:
        o.append(t)
        t += (b - a) / s
    return o, t


def pts_expr(segs, starts):
    """setpts expression mapping source time T to output time, piecewise-linear."""
    expr = f"({starts[-1]:.6f}+(T-{segs[-1][0]:.6f})/{segs[-1][2]})"
    for (a, b, s, _), o in zip(reversed(segs[:-1]), reversed(starts[:-1])):
        expr = f"if(lt(T,{b:.6f}),{o:.6f}+(T-{a:.6f})/{s},{expr})"
    return expr


def badge(speed, path):
    img = Image.new("RGBA", (128, 46), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 127, 45), radius=12, fill=(12, 16, 28, 200))
    f = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 23)
    t = f"▶▶ {speed}×"
    tw = d.textlength(t, font=f)
    d.text(((128 - tw) / 2, 9), t, font=f, fill=(255, 255, 255, 255))
    img.save(path)


def render_scene(src, segs, work, out_video):
    starts, total = out_times(segs)
    speeds = sorted({s for _, _, s, k in segs if s > 1})
    inputs, chain = ["-i", src], []
    chain.append(f"[0:v]setpts='({pts_expr(segs, starts)})/TB',fps={FPS},split[a][b]")
    chain.append(f"[a]scale=480:270:force_original_aspect_ratio=increase,crop=480:270,"
                 f"gblur=sigma=10,eq=brightness=-0.06,scale={W}:{H}[bg]")
    chain.append(f"[b]scale=-2:{H}[fg]")
    chain.append("[bg][fg]overlay=(W-w)/2:0[v0]")
    last = "v0"
    for i, s in enumerate(speeds):
        png = os.path.join(work, f"badge_{s}.png")
        badge(s, png)
        inputs += ["-loop", "1", "-i", png]
        on = "+".join(f"between(t,{o + 0.05:.3f},{o + (b - a) / s - 0.05:.3f})"
                      for (a, b, sp, _), o in zip(segs, starts) if sp == s)
        chain.append(f"[{last}][{i + 1}:v]overlay={BADGE_X}:{BADGE_Y}:shortest=1:enable='{on}'[v{i + 1}]")
        last = f"v{i + 1}"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chain),
         "-map", f"[{last}]", "-t", f"{total:.6f}", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-r", str(FPS), out_video])
    return starts, total


def scene_audio(revoiced, segs, starts, total, work):
    wav = os.path.join(work, "voice48k.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", revoiced, "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", wav])
    x, _ = sf.read(wav, dtype="float32", always_2d=True)
    out = np.zeros((int(round(total * SR)), 2), dtype="float32")
    dropped = 0.0
    for (a, b, s, _), o in zip(segs, starts):
        ia, ib = int(round(a * SR)), int(round(b * SR))
        oa = int(round(o * SR))
        if s == 1:
            chunk = x[ia:ib]
            out[oa:oa + len(chunk)] = chunk[:len(out) - oa]
        else:
            dropped = max(dropped, float(np.abs(x[ia:ib]).max()) if ib > ia else 0.0)
    return out, dropped


def main():
    ap = argparse.ArgumentParser(description="Cut re-voiced scenes into one video per part.")
    ap.add_argument("folder")
    ap.add_argument("--out", help="default: <folder>/edited")
    ap.add_argument("--only", nargs="*", help='e.g. --only "part 2"')
    a = ap.parse_args()
    folder = os.path.abspath(os.path.expanduser(a.folder))
    rv = os.path.join(folder, "revoiced")
    out = os.path.abspath(os.path.expanduser(a.out or os.path.join(folder, "edited")))
    os.makedirs(os.path.join(out, ".reports"), exist_ok=True)
    work_root = os.path.join(out, ".work")

    parts = {}
    for f in sorted(os.listdir(rv)):
        if f.lower().endswith(".mp4") and not f.startswith("."):
            parts.setdefault(part_key(f)[0], []).append(f)
    ok = True
    for p in sorted(parts):
        scenes = sorted(parts[p], key=part_key)
        title = re.sub(r"\s*part\s*\d+.*$", "", scenes[0], flags=re.I).strip() + f" part {p}"
        if a.only and not any(o.lower() == f"part {p}" for o in a.only):
            continue
        print(f"== {title}: " + " + ".join(scenes), flush=True)
        vids, auds, rep = [], [], [f"# {title}", "", "Scenes, in order: " + ", ".join(scenes), ""]
        src_total = 0.0
        for f in scenes:
            wk = os.path.join(work_root, slug(f))
            os.makedirs(wk, exist_ok=True)
            rw = os.path.join(rv, ".work", slug(f))
            words = json.load(open(os.path.join(rw, "result_words.json")))
            script = json.load(open(os.path.join(rw, "script.json")))
            dur = media.probe(os.path.join(rv, f))["duration"]
            segs = plan(dur, words, script)
            v = os.path.join(wk, "video.mp4")
            starts, total = render_scene(os.path.join(rv, f), segs, wk, v)
            audio, dropped = scene_audio(os.path.join(rv, f), segs, starts, total, wk)
            # the source's video stream ends a frame or so before its audio: match the audio
            # to the frames actually rendered (the tail is silence, so nothing audible is lost)
            n = int(run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets",
                         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", v]).stdout.strip())
            want = int(round(n / FPS * SR))
            audio = audio[:want] if len(audio) >= want else np.pad(audio, ((0, want - len(audio)), (0, 0)))
            total = n / FPS
            vids.append(v); auds.append(audio); src_total += dur
            ff = [sg for sg in segs if sg[2] > 1]
            print(f"   {f}: {dur:.1f}s -> {total:.1f}s, {len(ff)} fast-forwards"
                  + (f"  (WARNING: sound under a fast-forward, peak {dropped:.2f})" if dropped > 0.05 else ""), flush=True)
            if dropped > 0.05:
                ok = False
            rep += [f"## {f}", f"{dur / 60:.1f} min -> {total / 60:.1f} min", "",
                    "| source | plays at | kind | length now |", "|---|---|---|---|"]
            rep += [f"| {sg[0]:.1f}-{sg[1]:.1f}s | {sg[2]}x | {sg[3]} | {(sg[1] - sg[0]) / sg[2]:.1f}s |"
                    for sg, o in zip(segs, starts) if sg[2] > 1] + [""]
        # join: video by stream copy (same encoder settings), audio sample-exact
        wk = os.path.join(work_root, slug(title))
        os.makedirs(wk, exist_ok=True)
        lst = os.path.join(wk, "list.txt")
        open(lst, "w").write("".join(f"file '{v}'\n" for v in vids))
        joined = os.path.join(wk, "video.mp4")
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
        wav = os.path.join(wk, "audio.wav")
        sf.write(wav, np.concatenate(auds), SR, subtype="FLOAT")
        final = os.path.join(out, title + ".mp4")
        run(["ffmpeg", "-v", "error", "-y", "-i", joined, "-i", wav, "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", str(SR), "-movflags", "+faststart", final])
        # verify
        vd = float(run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=duration",
                        "-of", "csv=p=0", final]).stdout.strip())
        ad = float(run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=duration",
                        "-of", "csv=p=0", final]).stdout.strip())
        I = media.lufs(final)
        peak, lr = media.channel_peaks(final)
        good = abs(vd - ad) < 0.1 and abs(I + 16) <= 1.0 and peak < 0.95
        ok &= good
        rep[3:3] = [f"**{src_total / 60:.1f} min -> {vd / 60:.1f} min.** Video {vd:.2f}s, audio {ad:.2f}s, "
                    f"{I:.1f} LUFS, peak {peak:.2f}. {'All checks pass.' if good else 'CHECK FAILED.'}", ""]
        open(os.path.join(out, ".reports", title + ".md"), "w").write("\n".join(rep) + "\n")
        print(f"   -> {final}: {src_total / 60:.1f} -> {vd / 60:.1f} min, {I:.1f} LUFS, peak {peak:.2f}, "
              f"a/v {ad - vd:+.2f}s {'ok' if good else 'FAILED'}", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
