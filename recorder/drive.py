#!/usr/bin/env python3
"""Real CGEvent input driver. Coordinates are in display POINTS (1728x1117)."""
import sys, time, math
import Quartz as Q

LEFT = Q.kCGMouseButtonLeft
TAP  = Q.kCGHIDEventTap

def pos():
    e = Q.CGEventCreate(None)
    p = Q.CGEventGetLocation(e)
    return (p.x, p.y)

def _post(e):
    Q.CGEventPost(TAP, e)

def move_to(x, y, duration=0.45, steps=None):
    """Eased cursor glide - looks human on camera."""
    sx, sy = pos()
    dist = math.hypot(x - sx, y - sy)
    if steps is None:
        steps = max(2, min(90, int(dist / 8)))
    if duration <= 0 or dist < 1:
        _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (x, y), LEFT))
        return
    for i in range(1, steps + 1):
        t = i / steps
        # ease-in-out cubic
        e = 4*t*t*t if t < 0.5 else 1 - pow(-2*t + 2, 3) / 2
        cx = sx + (x - sx) * e
        cy = sy + (y - sy) * e
        _post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (cx, cy), LEFT))
        time.sleep(duration / steps)

def click(x=None, y=None, glide=0.45, clicks=1):
    if x is not None:
        move_to(x, y, glide)
        time.sleep(0.12)
    cx, cy = pos()
    for n in range(1, clicks + 1):
        d = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, (cx, cy), LEFT)
        u = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp,   (cx, cy), LEFT)
        Q.CGEventSetIntegerValueField(d, Q.kCGMouseEventClickState, n)
        Q.CGEventSetIntegerValueField(u, Q.kCGMouseEventClickState, n)
        _post(d); time.sleep(0.05); _post(u)
        if n < clicks: time.sleep(0.06)

def type_text(s, cps=28):
    """Unicode typing - handles any character, no keymap needed."""
    delay = 1.0 / cps
    for ch in s:
        for kind in (Q.kCGEventKeyDown, Q.kCGEventKeyUp):
            e = Q.CGEventCreateKeyboardEvent(None, 0, kind == Q.kCGEventKeyDown)
            Q.CGEventKeyboardSetUnicodeString(e, len(ch), ch)
            # A chord like cmd+shift+G leaves the modifiers latched; without this a
            # plain "r" arrives as cmd+shift+R (Claude's Reveal in Finder) and the
            # rest of the prompt lands in Finder.
            Q.CGEventSetFlags(e, 0)
            _post(e)
        time.sleep(delay)

KEYS = {
 'return':36,'enter':36,'tab':48,'space':49,'delete':51,'escape':53,'esc':53,
 'left':123,'right':124,'down':125,'up':126,
 'a':0,'s':1,'d':2,'f':3,'h':4,'g':5,'z':6,'x':7,'c':8,'v':9,'b':11,'q':12,
 'w':13,'e':14,'r':15,'y':16,'t':17,'o':31,'u':32,'i':34,'p':35,'l':37,
 'j':38,'k':40,'n':45,'m':46,
 '1':18,'2':19,'3':20,'4':21,'5':23,'6':22,'7':26,'8':28,'9':25,'0':29,
 # keycode 50 is the grave/backtick key. VS Code's integrated terminal is
 # ctrl+` and there was no way to press it, which cost a take on L30.
 '`':50,'grave':50,'backtick':50}
MODS = {'cmd':Q.kCGEventFlagMaskCommand,'shift':Q.kCGEventFlagMaskShift,
        'alt':Q.kCGEventFlagMaskAlternate,'opt':Q.kCGEventFlagMaskAlternate,
        'ctrl':Q.kCGEventFlagMaskControl}

def key(name, mods=()):
    kc = KEYS.get(name.lower())
    if kc is None: raise SystemExit(f"unknown key: {name}")
    flags = 0
    for m in mods: flags |= MODS.get(m.lower(), 0)
    for down in (True, False):
        e = Q.CGEventCreateKeyboardEvent(None, kc, down)
        Q.CGEventSetFlags(e, flags if down else 0)
        _post(e)
        time.sleep(0.02)

def scroll(dy, dx=0, steps=10):
    for _ in range(steps):
        e = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitPixel, 2,
                                            int(dy/steps), int(dx/steps))
        _post(e); time.sleep(0.016)

if __name__ == "__main__":
    cmd = sys.argv[1]
    a = sys.argv[2:]
    if   cmd == "move":   move_to(float(a[0]), float(a[1]), float(a[2]) if len(a)>2 else 0.45)
    elif cmd == "click":  click(float(a[0]), float(a[1]), float(a[2]) if len(a)>2 else 0.45)
    elif cmd == "dclick": click(float(a[0]), float(a[1]), float(a[2]) if len(a)>2 else 0.45, clicks=2)
    elif cmd == "clickhere": click()
    elif cmd == "type":   type_text(a[0], float(a[1]) if len(a)>1 else 28)
    elif cmd == "key":    key(a[0], a[1:])
    elif cmd == "scroll": scroll(float(a[0]), float(a[1]) if len(a)>1 else 0)
    elif cmd == "pos":    print(pos())
    else: raise SystemExit("unknown cmd")


def drag(x1, y1, x2, y2, glide=0.5, hold=0.35, steps=None, settle=0.45):
    """Press at (x1,y1), glide to (x2,y2), release.

    Needed for two Day 3 things: dragging the chat panel's sash to widen it, and
    the drag-and-drop check every one of the four builds has to pass on camera.
    HTML5 drag-and-drop and VS Code's sashes both need real intermediate move
    events while the button is down, so this posts a full eased path rather than
    a down/up pair, and holds briefly at each end so the drop target registers.
    Flags are zeroed on every event because cmd+ctrl+F leaves modifiers latched.
    """
    move_to(x1, y1, glide)
    time.sleep(0.15)
    d = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, (x1, y1), LEFT)
    Q.CGEventSetIntegerValueField(d, Q.kCGMouseEventClickState, 1)
    Q.CGEventSetFlags(d, 0)
    _post(d)
    time.sleep(hold)

    dist = math.hypot(x2 - x1, y2 - y1)
    n = steps if steps else max(12, min(120, int(dist / 6)))
    for i in range(1, n + 1):
        t = i / n
        e = 4*t*t*t if t < 0.5 else 1 - pow(-2*t + 2, 3) / 2
        cx = x1 + (x2 - x1) * e
        cy = y1 + (y2 - y1) * e
        m = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDragged, (cx, cy), LEFT)
        Q.CGEventSetFlags(m, 0)
        _post(m)
        time.sleep(glide / n)
    time.sleep(hold)

    u = Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp, (x2, y2), LEFT)
    Q.CGEventSetIntegerValueField(u, Q.kCGMouseEventClickState, 1)
    Q.CGEventSetFlags(u, 0)
    _post(u)
    time.sleep(settle)
