#!/usr/bin/env python3
"""revoice — replace the human voiceover of screen-recording tutorials with Kokoro af_heart,
timed to what happens on screen.

    revoice <folder> [--out DIR] [--jobs N] [--force] [--only NAME...]

Every video in <folder> goes through:
  1. look      probe, transcript of the original voice (whisperX), scene changes, frames
  2. write     Claude Code headless builds the on-screen timeline and writes the script
  3. lint      house rules as hard checks -> sent back to Claude until clean
  4. voice     Kokoro, clean onsets; real clip lengths -> timing checks -> revise until clean
  5. deliver   -16 LUFS, original picture copied untouched, same filename, in --out
  6. verify    transcript of the result: every line on cue, no leaked warm-up word;
               loudness, per-channel peak, length
Nothing is blurred and the source files are never modified or deleted.
Each stage is cached in <out>/.work/<video>/ so a re-run resumes where it stopped.
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import checks, media, tts, writer  # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv")
MAX_LINT_ROUNDS = 3
MAX_TIMING_ROUNDS = 4


def slug(name):
    return "".join(c if c.isalnum() else "_" for c in os.path.splitext(name)[0]).strip("_")[:80]


def series_brief(src):
    """The episode brief: <folder>/series.md, if present, tells each video where it sits in
    the episode and how it hands over to its neighbours, so the parts play as one."""
    p = os.path.join(os.path.dirname(src), "series.md")
    if not os.path.exists(p):
        return "(This video stands alone; no episode brief was given.)"
    return (f"This video is ONE PART of a longer episode, played back to back with the others. "
            f"You are writing the part whose file is: {os.path.basename(src)}\n"
            f"Read the whole brief, then follow its notes for YOUR file.\n\n" + open(p).read())


class Job:
    def __init__(self, cfg, src, out_dir, force):
        self.cfg, self.src, self.force = cfg, src, force
        self.name = os.path.basename(src)
        self.out = os.path.join(out_dir, self.name)
        self.work = os.path.join(out_dir, ".work", slug(self.name))
        self.frames = os.path.join(self.work, "frames")
        self.cache = os.path.join(out_dir, ".work", "_voice_cache")
        os.makedirs(self.frames, exist_ok=True)
        self.logf = open(os.path.join(self.work, "run.log"), "a")
        self.report = {"video": self.name, "checks": {}, "warnings": []}

    def log(self, msg):
        line = f"[{datetime.datetime.now():%H:%M:%S}] {self.name}: {msg}"
        print(line, flush=True)
        self.logf.write(line + "\n"); self.logf.flush()

    def cached(self, fname, make):
        p = os.path.join(self.work, fname)
        if os.path.exists(p) and not self.force:
            return json.load(open(p))
        v = make()
        json.dump(v, open(p, "w"), indent=1)
        return v

    # ---------------------------------------------------------------- stages
    def look(self):
        self.info = self.cached("probe.json", lambda: media.probe(self.src))
        dur = self.info["duration"]
        self.log(f"look: {dur:.1f}s {self.info['w']}x{self.info['h']}")

        def transcript():
            if not self.info["has_audio"]:
                return []
            wav = os.path.join(self.work, "orig16k.wav")
            media.to_16k(self.src, wav)
            return media.transcribe(self.cfg, wav)
        self.orig_words = self.cached("original_words.json", transcript)
        self.scenes = self.cached("scenes.json", lambda: media.scene_changes(self.src))
        sheets = self.cached("sheets.json", lambda: media.contact_sheets(self.src, self.frames, dur))
        keys = [t for t in self.scenes if t < dur - 0.1][:120]
        media.keyframes(self.src, self.frames, keys)
        self.ctx = {
            "video": self.src, "duration": f"{dur:.2f}", "width": self.info["w"],
            "height": self.info["h"], "work": self.work, "frames": self.frames,
            "cols": 6, "rows": 5, "step": 0.5,
            "sheet_index": "\n".join(f"- {os.path.basename(p)}: {a:.1f}-{b:.1f}s" for p, a, b in sheets),
            "scenes": ", ".join(f"{t:.1f}" for t in self.scenes) or "none detected",
            "original": "\n".join(f"{p['start']:.1f}-{p['end']:.1f}: {p['text']}"
                                  for p in media.phrases(self.orig_words)) or "(no voice)",
            "never_say": ", ".join(self.cfg["never_say"]),
            "series": series_brief(self.src),
        }

    def write(self):
        p = os.path.join(self.work, "script.json")
        if os.path.exists(p) and not self.force:
            self.script = json.load(open(p)); self.log("write: using cached script")
            return
        self.log("write: Claude is watching the frames and writing the script")
        self.script = writer.write(self.cfg, self.ctx, self.work)
        json.dump(self.script, open(os.path.join(self.work, "script_v0.json"), "w"), indent=1)
        self.log(f"write: {len(self.script['lines'])} lines")

    def gate(self):
        """Lint + real-length timing checks, looping back to Claude until both pass."""
        dur = self.info["duration"]
        rounds = 0
        while True:
            errs = checks.lint(self.script["lines"], dur, self.cfg["never_say"])
            durs = None
            if not errs:
                self.log("voice: rendering lines to measure real lengths")
                durs = tts.durations(self.cfg, self.script["lines"], self.cache)
                errs = checks.timing(self.script["lines"], durs, dur)
            if not errs:
                self.durs = durs
                break
            rounds += 1
            limit = MAX_LINT_ROUNDS + MAX_TIMING_ROUNDS
            self.log(f"gate: {len(errs)} problem(s), revision round {rounds}: {errs[0][:110]}")
            if rounds > limit:
                raise RuntimeError(f"script still fails after {limit} revisions: " + "; ".join(errs[:5]))
            self.script = writer.revise(self.cfg, self.ctx, self.script, durs, errs, self.work, rounds)
            json.dump(self.script, open(os.path.join(self.work, f"script_v{rounds}.json"), "w"), indent=1)
        json.dump(self.script, open(os.path.join(self.work, "script.json"), "w"), indent=1)
        self.report["checks"]["script_rules"] = "pass"
        self.report["checks"]["timing"] = f"pass ({len(self.script['lines'])} lines, >= {checks.MIN_GAP}s gaps)"
        self.report["revisions"] = rounds

    def deliver(self):
        dur = self.info["duration"]
        track = os.path.join(self.work, "voice24k.wav")
        normed = os.path.join(self.work, "voice48k_norm.wav")
        tts.render_track(self.cfg, self.script["lines"], dur, self.cache, track)
        media.normalize(track, normed, self.cfg["target_lufs"])
        media.mux(self.src, normed, self.out)
        self.log(f"deliver: wrote {self.out}")

    def verify(self):
        dur = self.info["duration"]
        got = media.probe(self.out)
        I = media.lufs(self.out)
        peak, lr = media.channel_peaks(self.out)
        wav = os.path.join(self.work, "result16k.wav")
        media.to_16k(self.out, wav)
        heard = media.transcribe(self.cfg, wav)
        json.dump(heard, open(os.path.join(self.work, "result_words.json"), "w"))
        has_okay = any("okay" in l["text"].lower() for l in self.script["lines"])
        cue = checks.verify_transcript(self.script["lines"], heard, has_okay)
        c = self.report["checks"]
        c["length"] = "pass" if abs(got["duration"] - dur) < 0.15 else f"FAIL ({got['duration']:.2f} vs {dur:.2f}s)"
        c["loudness"] = ("pass" if abs(I - self.cfg["target_lufs"]) <= 1.0 else "FAIL") + f" ({I:.1f} LUFS)"
        c["peak"] = ("pass" if peak < 0.95 and lr < 0.02 else "FAIL") + f" (peak {peak:.2f}, L-R {lr:.3f})"
        leaked = [p for p in cue if p.startswith("warm-up")]
        c["warm_up_leak"] = "FAIL: " + leaked[0] if leaked else "pass"
        missed = [p for p in cue if not p.startswith("warm-up")]
        n = len(self.script["lines"])
        c["lines_on_cue"] = f"{n - len(missed)}/{n}" + (" pass" if len(missed) <= max(1, n // 10) else " FAIL")
        self.report["warnings"] += missed
        bad = [k for k, v in c.items() if "FAIL" in str(v)]
        self.report["status"] = "FAILED CHECKS: " + ", ".join(bad) if bad else "ok"
        self.log(f"verify: {self.report['status']}  ({I:.1f} LUFS, peak {peak:.2f}, "
                 f"{n - len(missed)}/{n} lines on cue)")

    def write_report(self):
        r, s = self.report, getattr(self, "script", {"lines": []})
        md = [f"# {self.name}", "", f"**Status:** {r.get('status', 'not finished')}", ""]
        if r.get("error"):
            md += [f"**Error:** {r['error']}", ""]
        md += ["## Checks", ""] + [f"- **{k}**: {v}" for k, v in r["checks"].items()]
        md += [f"- **revision rounds**: {r.get('revisions', 0)}", ""]
        if r["warnings"]:
            md += ["## Warnings", ""] + [f"- {w}" for w in r["warnings"]] + [""]
        md += ["## Narration", "", "| at | on screen | line |", "|---|---|---|"]
        md += [f"| {l['at']:.1f} | {l.get('on_screen', '').replace('|', '/')} | "
               f"{l['text'].replace('|', '/')}{' *(typing)*' if l.get('typing') else ''} |"
               for l in s.get("lines", [])]
        md += ["", "## Corrections to the original voice", ""] + [f"- {x}" for x in s.get("corrections", [])]
        md += ["", "## Personal / company info visible on screen (not spoken; for the editors)", ""]
        md += [f"- {x}" for x in s.get("pii_on_screen", [])] or ["- none noticed"]
        path = os.path.join(os.path.dirname(self.out), ".reports", os.path.splitext(self.name)[0] + ".md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write("\n".join(md) + "\n")
        return path

    def run(self):
        try:
            if os.path.exists(self.out) and not self.force:
                self.log("already delivered (use --force to redo)")
                return {"video": self.name, "status": "skipped (exists)"}
            self.look(); self.write(); self.gate(); self.deliver(); self.verify()
        except Exception as e:
            self.report["status"] = "ERROR"
            self.report["error"] = str(e)
            self.log("ERROR " + str(e))
            self.logf.write(traceback.format_exc())
        finally:
            self.report["report"] = self.write_report()
        return self.report


def main():
    ap = argparse.ArgumentParser(description="Re-voice screen-recording tutorials with Kokoro.")
    ap.add_argument("folder", help="folder of videos (or a single video file)")
    ap.add_argument("--out", help="output folder (default: <folder>/revoiced)")
    ap.add_argument("--jobs", type=int, default=2, help="videos processed at once (default 2)")
    ap.add_argument("--force", action="store_true", help="redo everything, ignoring caches")
    ap.add_argument("--only", nargs="*", help="only videos whose name contains one of these")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, "config.json")))

    src = os.path.abspath(os.path.expanduser(a.folder))
    if os.path.isfile(src):
        vids, base = [src], os.path.dirname(src)
    else:
        base = src
        vids = sorted(os.path.join(src, f) for f in os.listdir(src)
                      if f.lower().endswith(VIDEO_EXT) and not f.startswith("."))
    if a.only:
        vids = [v for v in vids if any(o.lower() in os.path.basename(v).lower() for o in a.only)]
    out = os.path.abspath(os.path.expanduser(a.out or os.path.join(base, "revoiced")))
    vids = [v for v in vids if os.path.dirname(v) != out]
    if not vids:
        sys.exit(f"no videos found in {src}")
    for tool in ("ffmpeg", "ffprobe", "docker", cfg.get("claude_bin", "claude")):
        if not shutil.which(tool):
            sys.exit(f"missing tool on PATH: {tool}")
    os.makedirs(out, exist_ok=True)
    print(f"revoice: {len(vids)} video(s) -> {out}")
    tts.ensure_kokoro(cfg)

    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = list(ex.map(lambda v: Job(cfg, v, out, a.force).run(), vids))

    print("\n=== summary ===")
    for r in results:
        print(f"  {r['status']:<24} {r['video']}" + (f"   report: {r['report']}" if r.get("report") else ""))
    if any(r["status"] not in ("ok",) and not r["status"].startswith("skipped") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
