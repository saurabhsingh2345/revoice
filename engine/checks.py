"""The house rules as machine checks. Every rule here came from real reviewer feedback."""
import re

# Openers that only make sense glued to the line before. Across the long silences of a
# typing section they read as the middle of a sentence ("and it has a button…").
DEPENDENT = re.compile(r"^(and|but|so|or|then|also|plus|with|from|to|which)\b", re.I)
# Kokoro takes a comma before a quoted name as a hard pause: "says, internet speed tracker".
NAME_COMMA = re.compile(r"\b(says|say|said|reads|read|labelled|labeled|called|named|titled),\s", re.I)
MIN_GAP = 0.30


def words(t):
    return re.findall(r"[A-Za-z0-9']+", t)


def lint(lines, duration, never_say):
    """Hard errors in the script text itself (before any audio exists)."""
    errs = []
    pii = re.compile("|".join(re.escape(x) for x in never_say), re.I) if never_say else None
    prev = -1
    for i, l in enumerate(lines):
        t, at = l["text"].strip(), l["at"]
        tag = f"line {i} (at {at:.2f}s)"
        n_sent = len(re.findall(r"[.!?](\s|$)", t))
        if not (0 <= at < duration):
            errs.append(f"{tag}: start time is outside the video (0-{duration:.1f}s)")
        if at <= prev:
            errs.append(f"{tag}: lines must be in time order")
        prev = at
        if not t or t[-1] not in ".?!":
            errs.append(f"{tag}: must end with . ? or ! — it is a fragment: {t!r}")
        if t and t[0].islower():
            errs.append(f"{tag}: starts in lower case, i.e. mid-sentence: {t!r}")
        if NAME_COMMA.search(t):
            errs.append(f"{tag}: comma before a quoted name makes Kokoro pause — remove it: {t!r}")
        if pii and pii.search(t):
            errs.append(f"{tag}: speaks personal/company info — never say it: {t!r}")
        if n_sent > 2:
            errs.append(f"{tag}: {n_sent} sentences in one beat — max 2 (1 while typing)")
        if l.get("typing"):
            if n_sent != 1:
                errs.append(f"{tag}: typing-section lines must be exactly ONE complete sentence")
            if len(words(t)) < 5:
                errs.append(f"{tag}: typing-section sentence too short to stand alone: {t!r}")
            if DEPENDENT.match(t):
                errs.append(f"{tag}: typing-section sentence leans on the previous line "
                            f"(starts with '{DEPENDENT.match(t).group(1)}'); make it self-contained: {t!r}")
    return errs


def timing(lines, durs, duration):
    """Errors once real clip lengths are known."""
    errs = []
    for i, (l, d) in enumerate(zip(lines, durs)):
        end = l["at"] + d
        if i + 1 < len(lines):
            nxt = lines[i + 1]["at"]
            if end > nxt - MIN_GAP:
                errs.append(f"line {i} (at {l['at']:.2f}s) runs {d:.2f}s and ends at {end:.2f}s, "
                            f"but line {i+1} starts at {nxt:.2f}s (need {MIN_GAP}s gap). "
                            f"Room is {nxt - l['at'] - MIN_GAP:.2f}s: shorten it or move it.")
        elif end > duration - 0.2:
            errs.append(f"last line ends at {end:.2f}s, past the video end ({duration:.2f}s)")
    return errs


def norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def verify_transcript(lines, heard, script_has_okay=False):
    """Every line must be heard starting near its cue; the warm-up word must never leak.
    whisperX stretches the first word of a line back into the silence before it, so a
    line counts as found when any of its first three words lands within 1.2 s of the cue."""
    problems = []
    leaks = [w for w in heard if norm(w["word"]) in ("okay", "ok")]
    if leaks and not script_has_okay:
        problems.append(f"warm-up word leaked at {', '.join(f'{w['start']:.2f}s' for w in leaks[:5])}")
    for i, l in enumerate(lines):
        want = {norm(w)[:4] for w in words(l["text"])[:3]} - {""}
        near = [w for w in heard if abs(w["start"] - l["at"]) < 1.2]
        if not any(norm(w["word"])[:4] in want for w in near):
            problems.append(f"line {i} (at {l['at']:.2f}s) not heard at its cue: {l['text'][:60]!r}")
    return problems
