# revoice

Replaces the human voiceover of screen-recording tutorials with the Kokoro **af_heart**
voice, timed to what happens on screen. Built from the Ep 5.1 job (8 parts) and both rounds
of reviewer feedback on it.

```sh
revoice ~/Downloads/5.2                 # every video in the folder -> ~/Downloads/5.2/revoiced
revoice ~/Downloads/5.2 --out ~/Downloads/5.2-final
revoice ~/Downloads/5.2 --only "part 3" # just some of them
revoice ~/Downloads/5.2 --force         # redo from scratch, ignoring caches
revoice some/video.mp4                  # a single file
```

Finished videos keep their **original filenames**. The picture is copied untouched
(nothing is blurred), and the source files are never modified or deleted.
A report for each video goes to `<out>/.reports/<name>.md`: every check, the full script
with timings, every correction made to the original voice, and personal/company info seen
on screen (for the editors, never spoken). Working files go to `<out>/.work/` and let a
re-run resume. Delete `.work` once you're happy.

## What it does, per video

1. **look**: probe, whisperX transcript of the original voice, scene changes, contact
   sheets (one frame every 0.5 s), full-size keyframes at every scene change.
2. **write**: Claude Code headless (`claude -p`, your normal login, no API key) goes
   through the frames, builds an exact on-screen timeline, and writes the script. The
   brief is `prompts/write.md`, and all the house rules live there.
3. **lint**: hard checks (`engine/checks.py`). Complete sentences only. One
   self-contained sentence per line while typing, with no And/Then/So openers. No comma
   before a quoted name. No names, emails, usernames or company names. Failures go back
   to Claude (`prompts/revise.md`).
4. **voice**: Kokoro af_heart at 0.9. Each sentence is spoken after a throwaway "Okay.",
   which is then cut away, so the first word is always clean. The real clip lengths are
   checked against the next line (>= 0.3 s gap); overlaps go back to Claude.
5. **deliver**: two-pass loudnorm to -16 LUFS, resample to 48 kHz, limiter last, voice
   panned to both channels at -3 dB, AAC 256k, video stream copied.
6. **verify**: the result is transcribed again. Every line must be heard on its cue, and
   the warm-up word must never leak. Loudness, per-channel peak and length must match.
   Anything failing shows in the summary and the report, and the exit code is 1.

## Needs
- ffmpeg, Docker (Kokoro runs as the `coursesmith-kokoro` container on :8880, and is
  started automatically), the `claude` CLI logged in.
- whisperX and numpy: `config.json` points at courseSmith's `tools/align/.venv`.

## Tuning
`config.json`: voice, speed, target loudness, the never-say list, and the Claude model
(`null` = your default). Rules for what the narration should sound like go in
`prompts/write.md`. Rules that must never be broken go in `engine/checks.py` as well.
