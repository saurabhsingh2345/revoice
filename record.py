#!/usr/bin/python3
"""record — drive the Mac from a plain-text script while recording the screen.

    record script.txt [--out DIR] [--region x,y,w,h] [--dry]

One line per action, `verb: argument`. Blank lines and # comments are ignored.
Every `scene:` starts a new clip, named exactly like the line, so the clips drop straight
into `revoice` and `edit` ("Ep 1 part 1 scene 2" -> Ep 1 part 1 scene 2.mp4).

    scene: Ep 1 part 1 scene 1      start a new clip (the previous one is finished)
    open: Visual Studio Code        open an app          (open -a)
    open: https://example.com       open a URL           (default browser)
    open: ~/projects/demo           open a file or folder
    wait: 2.5                       seconds to do nothing (the picture holds still)
    move: 400 300                   glide the cursor (points)
    click: 400 300                  glide, then click    (dclick: for a double click)
    type: print("hello")            type at human pace (human: 22 characters a second)
    paste: long text               paste instead of typing
    key: return                     key, or a chord: cmd+s, cmd+shift+p, ctrl+`
    scroll: -300                    scroll (negative = down)
    pace: 15                        change the typing speed (characters a second)

Coordinates are in points (a 1728x1117 laptop screen). Needs Screen Recording and Accessibility
permission for the app that launches this (Terminal). Nothing is sent anywhere.
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "recorder"))


def screen_points():
    import Quartz as Q
    b = Q.CGDisplayBounds(Q.CGMainDisplayID())
    return 0, 0, int(b.size.width), int(b.size.height)


def parse(path):
    steps = []
    for n, raw in enumerate(open(path), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\w+)\s*:\s*(.*)$", line)
        if not m:
            raise SystemExit(f"{path}:{n}: expected 'verb: argument', got: {line}")
        steps.append((n, m.group(1).lower(), m.group(2)))
    return steps


VERBS = {"scene", "open", "wait", "move", "click", "dclick", "type", "paste", "key", "scroll", "pace"}


def check(steps):
    bad = [f"line {n}: unknown verb '{v}'" for n, v, _ in steps if v not in VERBS]
    if not any(v == "scene" for _, v, _ in steps):
        bad.append("the script needs at least one 'scene: name' line")
    if steps and steps[0][1] != "scene":
        bad.append("the first line must be 'scene: name'")
    for n, v, a in steps:
        if v in ("move", "click", "dclick") and not re.match(r"^\s*-?\d+(\.\d+)?\s+-?\d+(\.\d+)?\s*$", a):
            bad.append(f"line {n}: {v} needs two numbers, e.g. '{v}: 400 300'")
        if v in ("wait", "scroll", "pace") and not re.match(r"^\s*-?\d+(\.\d+)?\s*$", a):
            bad.append(f"line {n}: {v} needs a number")
        if v == "scene" and not re.match(r"^[^/\\:]+$", a.strip()):
            bad.append(f"line {n}: scene name can't be empty or contain / \\ :")
    if bad:
        raise SystemExit("\n".join(bad))


class Recorder:
    def __init__(self, region):
        self.region, self.p, self.mov, self.name = region, None, None, None

    def begin(self, name, out):
        self.name = name
        self.mov = os.path.join(out, f".raw {name}.mov")
        x, y, w, h = self.region
        self.p = subprocess.Popen([os.path.join(HERE, "recorder", "screcord"), self.mov, str(x), str(y), str(w), str(h)],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = self.p.stdout.readline().strip()
        if not line.startswith("STARTED"):
            raise SystemExit("recorder failed to start (Screen Recording permission?): " + line)
        time.sleep(1.0)  # a still opening frame

    def end(self, out):
        if not self.p:
            return
        time.sleep(1.0)  # a still closing frame
        self.p.send_signal(2)
        self.p.communicate(timeout=60)
        final = os.path.join(out, f"{self.name}.mp4")
        # ScreenCaptureKit only writes frames when the screen changes, so rebuild a steady 30 fps
        # clock (hold the last frame) or every wait would vanish and the clip would be short.
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", self.mov, "-vf",
                            "tpad=stop_mode=clone:stop_duration=1,fps=30,format=yuv420p",
                            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-an", final],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("ffmpeg failed: " + r.stderr[-400:])
        os.remove(self.mov)
        print(f"  saved {final}", flush=True)
        self.p = None


def run(steps, out, region, dry):
    import drive
    rec = Recorder(region)
    cps = 22.0
    try:
        for n, verb, arg in steps:
            print(f"[{n}] {verb}: {arg}", flush=True)
            if dry:
                continue
            if verb == "scene":
                rec.end(out)
                rec.begin(arg.strip(), out)
            elif verb == "open":
                a = arg.strip()
                cmd = ["open", os.path.expanduser(a)] if re.match(r"^(https?://|[~/])", a) else ["open", "-a", a]
                if subprocess.run(cmd, capture_output=True).returncode:
                    raise SystemExit(f"line {n}: could not open '{a}'")
                time.sleep(2.0)
            elif verb == "wait":
                time.sleep(float(arg))
            elif verb in ("move", "click", "dclick"):
                x, y = (float(v) for v in arg.split())
                if verb == "move":
                    drive.move_to(x, y)
                else:
                    drive.click(x, y, clicks=2 if verb == "dclick" else 1)
                time.sleep(0.4)
            elif verb == "type":
                drive.type_text(arg, cps=cps)
                time.sleep(0.3)
            elif verb == "paste":
                subprocess.run("pbcopy", input=arg.encode())
                drive.key("v", ("cmd",))
                time.sleep(0.4)
            elif verb == "key":
                parts = [p for p in arg.strip().lower().split("+") if p]
                drive.key(parts[-1], tuple(parts[:-1]))
                time.sleep(0.5)
            elif verb == "scroll":
                drive.scroll(float(arg))
                time.sleep(0.5)
            elif verb == "pace":
                cps = max(3.0, float(arg))
    finally:
        rec.end(out)


def main():
    ap = argparse.ArgumentParser(description="Record the screen while a text script drives the Mac.")
    ap.add_argument("script")
    ap.add_argument("--out", help="default: the folder the script is in")
    ap.add_argument("--region", help="x,y,w,h in points (default: the whole main display)")
    ap.add_argument("--dry", action="store_true", help="check the script and list the steps, record nothing")
    a = ap.parse_args()
    steps = parse(a.script)
    check(steps)
    out = os.path.abspath(a.out or os.path.dirname(os.path.abspath(a.script)))
    os.makedirs(out, exist_ok=True)
    region = tuple(int(v) for v in a.region.split(",")) if a.region else screen_points()
    scenes = sum(1 for _, v, _ in steps if v == "scene")
    print(f"{len(steps)} steps, {scenes} scene(s), region {region}, into {out}", flush=True)
    if not a.dry:
        print("Hands off the keyboard and mouse. Starting in 5 s…", flush=True)
        time.sleep(5)
    run(steps, out, region, a.dry)
    print("RECORD DONE" if not a.dry else "script OK", flush=True)


if __name__ == "__main__":
    main()
