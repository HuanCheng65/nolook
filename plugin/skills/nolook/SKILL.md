---
name: nolook
description: Drive and inspect an Android app (device or emulator) or a web page (local dev server or live site) through its UI tree instead of screenshots — see what is on screen, tap by text or id, type, scroll, and run whole multi-step checks as one Python flow; small cropped screenshots for visual checks; animation and frame-timing measurements on Android. Use it whenever a task involves operating, checking, reproducing a bug in, or verifying a change to a running app or web page, and before reaching for adb screencap / input tap / uiautomator dump, or for a one-off Playwright, Puppeteer or Selenium script.
---

# nolook

`nolook` reads the screen as text and acts on elements by what they say or
their id. The same commands drive an Android app and a web page. You rarely
need a screenshot; when you do, take a small crop.

## Two ways to work, and when to switch

**Commands** (`nolook snap`, `nolook tap Save`) are for finding your way:
what is on this screen, what happens when I tap this. Each one is a full turn
of yours, which costs seconds and re-sends the whole conversation.

**Flows** run many steps in one turn: a short Python file, piped to
`nolook run -`, that taps, types, waits, branches, loops and returns a JSON
result. Switch to a flow as soon as you know the path. That is usually after
two or three commands, and right away when:

- the task is a check (does X still work after my change?) or a repro;
- anything repeats: N times to see if it is flaky, once per item, once per
  setting value;
- you need timing (how long until the list shows);
- the next step depends on what the last one did (a dialog that may appear,
  a login that may already be done).

A flow that taps through five screens and checks three things costs one turn
and a dozen lines of output. The same work as commands costs ten turns.

```bash
nolook run - <<'EOF'
"""Opening a note shows the editor."""
d.launch(fresh=True)
d.go("Notes", "Groceries")              # tap each in turn
step = d("#toolbar").tap("Edit")        # only inside #toolbar
assert step.shows("#body")
d.result(title=d.text("#title"))        # the flow's answer, as JSON
EOF
```

## Reading the screen

```
nolook snap
@e12  btn    "New note" #fab_new (960,2180)
@e13  link   "Docs" (122,24)
@e14  input  ~"Model · gpt" (168,32)
  @e30  btn    "Groceries · Milk, eggs" [covered] (540,2221)
```

- `@e12` is a ref. It names that element until the element leaves the screen;
  after that, using it is an error, never a tap on whatever replaced it.
- Roles: `btn link input text img scroll check switch`. Indentation means
  "inside a scrolling list".
- `[covered]`: something is drawn on top (a floating bar), so a tap would
  land there; nolook refuses it. Scroll first.
- `~"…"`: a name nolook inferred from the text beside a field the page left
  unnamed. `[invisible]`: there, but transparent (shown on hover, or a file
  input over its button). A `btn` with no name is an unlabelled icon button;
  `nolook shot @e…` if you need to know which.
- Only what is in view is listed. A very long listing folds runs of
  look-alike rows (`… 20 more like these`); `snap --all` lists everything.

Every action prints its target, how long the screen took to settle, and what
changed (`+` appeared, `-` gone), or the whole new screen when it changed
completely. Read that instead of running `snap` again.

## Queries

| Query | Matches |
|---|---|
| `@e12` | a ref from the last output |
| `#fab_new` | resource id / Compose test tag; on web `data-testid`, else `id` |
| `Save` | text, description, or one part of a row's label, exactly |
| `~Sav` | label contains |
| `/^Item \d+$/` | label regex |
| `btn:Save` | any of the above, limited to one role |

Labels match as the app shows them, in any language (`tap 保存`). A query
that matches nothing lists the closest elements; one that matches several
lists them: pick a ref, add a role, or pass `--nth N`. Don't fall back to
coordinates because a query failed; `tap --xy` is for canvases and views the
tree cannot see.

## Commands

```
nolook snap [--all] [--in Q]            list elements
nolook get Q                            one element in full; does not act
nolook tap Q [--nth N] [--long]
nolook type TEXT [--into Q] [--append] [--enter]
nolook key back|enter|tab|escape|…      nolook back for short
nolook scroll [down|up|left|right] [--in Q] [--until Q]
nolook wait Q [--gone] [--timeout S]
nolook open URI                         deep link (Android) or URL / path (web)
nolook launch [--fresh]                 start the app (web: open the configured url)
nolook shot [Q] [--region L,T,R,B]      save a small screenshot, print its path
nolook log [-n N] [--grep RX]           app log (Android)
nolook run FLOW|- [args]                run a flow
nolook flows                            the project's saved flows
```

`-q` before the command prints only the action line.

## Writing flows

`d` is the device, `args` the words after the flow name. The full API is in
[references/flow-api.md](references/flow-api.md); these are the patterns
that matter.

**Let actions do the waiting.** Every action waits for its target (5 s by
default, `d.timeout = 15` for slow pages), then waits for the screen to
settle after acting; on web, settling also waits for the requests the action
started. So a flow needs no `time.sleep` between steps. When the next thing
you need is a result rather than something to tap, wait for the element that
shows the result: `d.wait("~Saved")`. A sleep both slows the flow and hides
when a page gets slower; the flow already reports slow waits with ⚠.

**Branch on what an action did.** Each action returns a Step:

```python
step = d.tap("Sign in")
if step.shows("~Two-factor"):
    d.result(blocked="2FA")
    d.fail("account needs a code")
new = [n.label for n in d.tap("Load more").added]
```

**Handle what may pop up**, instead of checking for it after every step:

```python
with d.dismissing("Rate us", tap="Not now"):
    d.go("Settings", "Display", "Theme")
```

**Repeat inside the flow, and return the numbers.** Not a shell loop around
`nolook run`, and not `grep` over its output:

```python
import time
runs = []
for i in range(int(args[0]) if args else 10):
    d.launch(fresh=True)
    t = time.monotonic()
    d.tap("~Feed")
    d.wait("~Recommended")
    runs.append(round(time.monotonic() - t, 2))
d.result(runs=runs, slowest=max(runs))
```

**Try every value of something in one go:**

```python
seen = {}
for style in ["Timeline", "Classic"]:
    d.go("Settings", "Appearance")
    d.scroll("down", until="~Sub-post style")
    d.tap("~Sub-post style")
    d.tap(f"btn:{style}")
    seen[style] = d.sees(f"~Sub-post style · {style}")
    d.back(); d.back()
d.result(applied=seen)
```

**Scope and walk lists** with locators, which find their element again each
time they are used:

```python
replies = d("#thread")("btn:~Reply").all()
d.result(count=len(replies), first=replies[0].label if replies else None)
d("#sheet").tap("OK")
```

**Put the answer in `d.result(...)`.** It prints as one JSON line at the end,
also when the flow fails, so you read one line instead of a log.

When a flow fails it prints the failing step and line, the screen at that
moment, and the path of a screenshot it saved (not read). Fix the flow and run
it again; it is cheap. Flows worth keeping (a check you will rerun after the
next change) go in the project's flows directory: `nolook flows` lists them,
`nolook run NAME args…` runs one.

## Web pages

The same commands drive a page in Chromium. nolook keeps one browser running
between commands, so the page, its scroll position and its sign-in stay put.

```bash
nolook web start [URL]          # headless; --headed to show the window
nolook web attach http://127.0.0.1:9222   # or drive a Chrome you started with --remote-debugging-port
nolook --web snap               # --web, or a [web] table in .nolook.toml
nolook web status / stop
```

```toml
[web]
url = "http://localhost:3000"   # what `launch` opens; `open /path` is relative to it
profile = "dev"                 # browser profile: cookies and sign-in live here
device = "desk"                 # window size: desk (1440×900) | phone (390×844) | WxH
```

- **Sign in once** with `type` and `tap` (or let the user do it in
  `web start --headed`); the profile keeps it. Use only credentials the user
  gave you for this.
- A site that shows a bot check or a QR-code check to a headless browser:
  ask the user to start their own Chrome with `--remote-debugging-port=9222`
  and `nolook web attach` to it.
- `key back` is history back; `open /settings` resolves against `url`.
- On web, `log`, `motion` and `perf` are not available yet; for console
  errors or network traffic, read them with the project's own tooling.
- Use nolook to look at and operate a page. When the task is to add a test
  to the project's own Playwright/Cypress suite, write it there instead.

## Screenshots

Take one only to judge how something looks: colour, blur, alignment,
spacing, an animation's end state. Never to find out what is on screen or
where to tap; the tree answers that in a few hundred tokens, while every
image stays in the conversation and is re-sent with every later request.

- Crop to what you are judging: `nolook shot "#top_bar"` or
  `--region 0,0,1080,400`.
- The default is a small JPEG (540 px whole screen, 900 px crops). nolook
  prints the path, size and an approximate token cost; read the file only
  if you need to see it. `--full` gives a native PNG.
- In a flow, `d.shot("#card")` returns the path; put it in `d.result`.

## Safety

The app or page may be signed in to a real account.

- A tap on a label that looks like a write action (like, follow, send,
  post, delete, …; projects add their own `danger` list) is refused. Pass
  `--confirm` (`confirm=True` in a flow) only when the user asked for that
  action, never to get past a refusal while testing something else.
- A tap on a `[covered]` element is refused. Scroll it into view; pass
  `--force` only when tapping what covers it is the point.
- With `guard` patterns in the project (Android), a matching log line after
  an action prints `⚠ guard:`. Stop and tell the user when you did not intend
  it.
- To see what a query resolves to, use `nolook get Q`, which does not act.

## Android: animations and performance

- `nolook motion --tap Q --label L` measures an animation: duration, 50/90%
  points, curve shape, overshoot, stalls, plus one sheet of key frames.
  Read [references/motion.md](references/motion.md) before using it.
- `nolook perf frames | compare | ab | stack | anr` and `nolook trace` measure
  frame timing and hangs. Read
  [references/performance.md](references/performance.md) first: one run is
  not a result, and the rules there keep conclusions honest.

## Setup

If `nolook` is not on PATH: `uv tool install nolook` (or `pipx install
nolook`). `nolook doctor` checks adb, the device, ffmpeg and Perfetto;
`nolook --web doctor` checks the browser, the site and the browser profile.

A `.nolook.toml` at the project root picks the device and app; without one,
`nolook --package PKG …` names the app:

```toml
serial = "emulator-5554"
adb_port = 5037          # if the device is on a non-default adb server
package = "com.example.notes"
flows = "tools/flows"
guard = ["/api/like", "/api/post"]
danger = ["Like", "Send", "Delete"]
```

Compose apps: set `Modifier.semantics { testTagsAsResourceId = true }` near
the root (debug builds are enough) so `testTag`s show up as `#ids`, and give
icon buttons a `contentDescription`. Web pages: a `data-testid` shows up as
`#id`, and an `aria-label` names an icon button.
