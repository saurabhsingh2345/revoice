"""The script-writing step: Claude Code headless looks at the frames and writes the lines."""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEMA = {
    "type": "object",
    "properties": {
        "events": {"type": "array", "items": {"type": "object", "properties": {
            "t": {"type": "number"}, "what": {"type": "string"}}, "required": ["t", "what"]}},
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "at": {"type": "number"}, "text": {"type": "string"},
            "on_screen": {"type": "string"}, "typing": {"type": "boolean"}},
            "required": ["at", "text", "on_screen", "typing"]}},
        "corrections": {"type": "array", "items": {"type": "string"}},
        "pii_on_screen": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["events", "lines", "corrections", "pii_on_screen"],
}


def fill(template, **kv):
    t = open(os.path.join(HERE, "prompts", template)).read()
    for k, v in kv.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


def ask(cfg, prompt, work, log_path):
    cmd = [cfg.get("claude_bin", "claude"), "-p", prompt, "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA),
           "--allowedTools", "Read", "Bash(ffmpeg:*)", "Bash(ls:*)",
           "--add-dir", work]
    if cfg.get("claude_model"):
        cmd += ["--model", cfg["claude_model"]]
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    open(log_path, "w").write(r.stdout + "\n--- stderr ---\n" + r.stderr)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude returned no JSON (see {log_path})")
    out = d.get("structured_output")
    if d.get("is_error") or not out or not out.get("lines"):
        raise RuntimeError(f"claude did not return a script (see {log_path})")
    out["lines"] = sorted(out["lines"], key=lambda l: l["at"])
    return out


def write(cfg, ctx, work):
    return ask(cfg, fill("write.md", **ctx), work, os.path.join(work, "claude_write.log"))


def revise(cfg, ctx, script, durs, problems, work, round_no):
    rows = []
    for i, l in enumerate(script["lines"]):
        d = f"{durs[i]:.2f}" if durs and i < len(durs) else "?"
        rows.append(f"[{i}] at={l['at']:.2f} len={d} typing={l.get('typing', False)} {l['text']}")
    prompt = fill("revise.md", script="\n".join(rows),
                  problems="\n".join(f"- {p}" for p in problems), **ctx)
    new = ask(cfg, prompt, work, os.path.join(work, f"claude_revise{round_no}.log"))
    new["corrections"] = new.get("corrections") or script.get("corrections", [])
    new["pii_on_screen"] = new.get("pii_on_screen") or script.get("pii_on_screen", [])
    new["events"] = new.get("events") or script.get("events", [])
    return new
