You wrote a narration script for the screen-recording tutorial {{video}} ({{duration}} s).
It was checked mechanically and failed some rules. Fix ONLY what is needed; keep every line
that passed as it is, and keep the timing tied to the on-screen events you found.

The frames are still in {{frames}} (sheets, keyframes) if you need to re-check a moment, and
you can grab more with:
`ffmpeg -v error -y -ss T -i "{{video}}" -frames:v 1 -vf scale=1400:-1 "{{frames}}/x_T.jpg"`

The rules (unchanged): complete sentences only, never fragments; in typing sections exactly
ONE self-contained sentence per line, no And/But/So/Or/Then/Also/Plus openers; a line that
describes an action finishes before the next action; at least 0.3 s between lines; the voice
speaks ~2.6 words/s plus 0.4 s between sentences; no comma before a quoted name; never speak
names, emails, usernames or company/workspace names ({{never_say}}).

To fix a line that runs too long: shorten the wording, or move its start EARLIER if the
action allows, or move the NEXT line later if its action allows. Never just delete teaching.

Keep the opening and closing lines that connect this part to the rest of the episode.
Episode brief (if any):
{{series}}

Current script (index, at, measured length in s when known, typing, text):
{{script}}

Problems to fix:
{{problems}}

Return the full corrected script in the same structure (events, lines, corrections,
pii_on_screen), with corrections/pii carried over from before plus anything new.
