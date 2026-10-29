You are re-voicing a screen-recording tutorial. A human recorded the voiceover; it will be
replaced by a text-to-speech voice (Kokoro af_heart). Your job is to write the NEW narration
script, timed to the picture. The picture will not be edited in any way.

# The video
- File: {{video}}
- Duration: {{duration}} s, {{width}}x{{height}}
- Working folder (you may write extra frames here): {{work}}

# What you are given
1. Contact sheets: `{{frames}}/sheet_NNN.jpg`. Each sheet is {{cols}} columns x {{rows}} rows,
   one frame every {{step}} s, read left-to-right then top-to-bottom. The index below says
   which time range each sheet covers.
2. Full-size keyframes at every scene change: `{{frames}}/key_TTTTT.TT.jpg` (T = seconds).
3. The ORIGINAL voiceover as timed phrases (below). It tells you WHAT the lesson teaches.
   It is NOT what you should say: it is often mistimed, rambling, mis-transcribed or wrong.
4. You may grab any extra frame you need, e.g. to read small text or pin an exact moment:
   `ffmpeg -v error -y -ss 12.5 -i "{{video}}" -frames:v 1 -vf scale=1400:-1 "{{frames}}/x_12.5.jpg"`
   then Read it. Do this generously around clicks, typing, dialogs and results.

Sheet index:
{{sheet_index}}

Scene changes (s): {{scenes}}

Original voiceover (start-end s: words):
{{original}}

# The episode this video belongs to
{{series}}

When there is an episode brief: the parts must connect. Open by picking up exactly where
the previous part left off (one sentence, no greeting, no "welcome back", no long recap).
Close with a line that leads into what the next part does. Use the same names for things
in every part. The brief's per-file notes and corrections override the original voice.

# How to work
First LOOK: go through every contact sheet and build an exact timeline of what happens on
screen — clicks, menus opening, dialogs, pages loading, zooms, typing (what text appears
when), waiting/progress, results appearing. Use extra frames to pin exact times (to ~0.25 s).
Then WRITE the narration against that timeline.

# Rules for the narration (these are hard-won; follow all of them)
1. Say what is ON SCREEN, when it happens. Each line starts at the moment its action happens
   (a click, a dialog appearing, a result showing). A line that describes an action must
   finish before the NEXT action it would be describing.
2. Real pauses. Leave silence while things load or while the picture speaks for itself. Never
   pad silence with filler. During long waits, say one useful thing when the status changes.
3. Every line is made of COMPLETE sentences. Never split a sentence across lines. Never write
   a fragment ("From Start Test," / "to Start speed Test.") or a question answered by a
   fragment in the next line.
4. TYPING SECTIONS (a prompt or text being typed into a box) — the reviewers were explicit:
   narrate ONE complete, self-contained sentence at a time. One sentence per typed chunk,
   started as that chunk is being typed, spread evenly across the typing. Each sentence must
   make sense on its own after a long silence: do NOT start with And/But/So/Or/Then/Also/Plus,
   do not lean on the previous line. Good: "The page also has a button labelled start speed
   test." Bad: "And a button labelled, start speed test." Mark these lines "typing": true.
   Don't read the typed text word for word, and don't read its typos aloud — say what it means.
5. Outside typing, a line is one or at most two short sentences.
6. Fix the original voice: mis-heard or mis-said names (e.g. "codecs" → Codex, "ChargerGPT" →
   ChatGPT), wrong claims (if the voice says something the screen contradicts, say what the
   screen shows — e.g. it says "installed" but the install finishes later), grammar. Add lines
   for important on-screen steps the original skipped (permission prompts, sign-in screens,
   results appearing). List every correction you made.
7. NEVER speak personal or company information: no names of people, no email addresses, no
   usernames, no company or workspace names ({{never_say}}). Say "my account", "the workspace".
8. Write for the voice: no comma before a quoted name ("a heading that says internet speed
   tracker", not "says, internet…" — the comma causes an awkward pause). Spell file names and
   URLs the way they should be spoken ("index dot html", "chat G P T dot com slash download").
   Units spoken in full ("megabits per second", "milliseconds").
9. Friendly, clear tutorial tone. First person for actions ("I'll click Allow once."). Short
   sentences.
10. Timing budget: this voice speaks about 2.6 words per second, plus 0.4 s between two
    sentences in one line. Leave at least 0.3 s before the next line starts. The last line
    must end before {{duration}} s.
11. Cover the whole video, start to finish. Start the first line within the first ~1.5 s
    unless the picture clearly calls for silence.

# Output
Return the structured result:
- events: your on-screen timeline, [{t, what}]
- lines: the narration, in time order, [{at, text, on_screen, typing}]
  (at = seconds when the first sound should land; on_screen = what is visible then)
- corrections: every way you changed or fixed the original voice
- pii_on_screen: personal/company info visible in the picture and roughly when (for the
  editors; do not speak it)
