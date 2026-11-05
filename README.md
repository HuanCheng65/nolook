<h1 align="center">nolook</h1>

<h3 align="center">All of the screen. None of the pixels.</h3>

<p align="center">UI automation for coding agents, on Android and the web.</p>

<p align="center">
  <a href="https://pypi.org/project/nolook/"><img alt="PyPI" src="https://img.shields.io/pypi/v/nolook?color=2b2b2b"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-2b2b2b">
  <a href="https://github.com/HuanCheng65/nolook#claude-code"><img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin-2b2b2b"></a>
  <a href="https://github.com/HuanCheng65/nolook/blob/main/LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-2b2b2b"></a>
</p>

<p align="center">
  English · <a href="https://github.com/HuanCheng65/nolook/blob/main/README.zh-CN.md">中文</a>
</p>

An agent that checks an app usually works in a loop: take a screenshot,
read it, tap, take another. Each full screenshot costs about 1,500 tokens
and stays in the context window for the rest of the session. Twenty steps
in, tens of thousands of tokens of the window hold pictures the agent no
longer needs.

nolook shows the agent the screen as a few lines of text, and keeps every
later output just as small: what changed after a tap, one line of result for
a whole flow, a page of numbers for an animation. It takes a screenshot only
when the question is how something looks, and then only a crop, which the
agent reads only if it must.

```
$ nolook snap
com.example.notes · 1080x2400 · 9 elements
@e1  btn    "Back" (63,140)
@e2  text   "Notes" (300,140)
@e3  scroll #note_list (540,1250)
  @e4  btn    "Groceries · Milk, eggs, bread" (540,420)
  @e5  btn    "Trip · Tickets booked" (540,610)
@e6  btn    "New note" #fab_new [covered] (960,2180)
@e7  btn    "Search" (1000,140)

$ nolook tap Groceries
tap @e4 btn "Groceries · Milk, eggs, bread" (540,420) · settled 0.42s
screen changed (6 elements):
@e8  btn    "Back" (63,140)
@e9  input  "Groceries" #title (540,300)
@e10 input  "Milk, eggs, bread" #body (540,700)
…
```

<sub>A 1080×2400 screenshot at Claude's default image size is scaled to
706×1568; at width × height / 750 that is about 1,476 tokens. The `snap`
above is under 200.</sub>

## Where the context goes

| The agent wants to | The screenshot way | nolook |
|---|---|---|
| See the screen | a full screenshot | `snap`: one line per element, with a ref |
| Know what a tap did | another screenshot | only the elements that appeared and disappeared |
| Walk through five screens | five turns, five images | one flow, one turn, one line of JSON |
| Check how something looks | a full screenshot | a crop of one element, saved to a file; only its path and token cost are printed |
| Judge an animation | dozens of frames | duration, curve, overshoot and stalls as text, plus one sheet of key frames |
| Find jank | a raw trace | a one-page summary of the trace |
| Let you watch | screenshots in the chat | a live mirror in a pane beside the chat, for you only (Claude Code) |

The same commands drive a page in Chromium over the DevTools protocol.

## Get started

Requirements: Python 3.11+, `adb` on `PATH`, a device or emulator with USB
debugging, and `ffmpeg` for `motion`. On first connect nolook pushes
uiautomator2's test server to the device; nothing is installed in your app.
For web pages, Chrome or Chromium instead of adb and a device.

```sh
uv tool install nolook          # or: pipx install nolook
nolook doctor                   # checks adb, the device, ffmpeg, Perfetto
nolook snap                     # what is on screen
nolook tap "Network & internet" # tap by text; prints what changed
```

### Claude Code

```
/plugin marketplace add HuanCheng65/nolook
/plugin install nolook@nolook
```

The plugin adds the `nolook` skill, a `nolook-driver` agent for multi-step
device work that reports back in text, and the `/device` pane.

### Other agents

Any agent that can run shell commands can use nolook. Give it
[`SKILL.md`](https://github.com/HuanCheng65/nolook/blob/main/plugin/skills/nolook/SKILL.md)
as instructions.

> **For agents.** Run `nolook doctor` first and follow what it says. Use
> commands to find your way, and switch to a flow (`nolook run -`) once you
> know the path. Read a screenshot only when the question is how something
> looks. When a tap is refused, the message says why and what to do next;
> pass `--confirm` only when the user asked for that action.

## Commands

```sh
nolook snap                         # what is on screen
nolook tap "Network & internet"     # tap by text; prints what changed
nolook type "hello" --into "#search"
nolook scroll down --until "About phone"
nolook wait "Saved" --timeout 5
nolook back
nolook get @e4                      # everything about one element
nolook shot "#toolbar"              # small crop; prints its path
nolook run flows/open_note.py       # several steps in one call
nolook --help                       # every command
```

### Queries

| Query | Matches |
|---|---|
| `@e12` | a ref from the last output |
| `#title` | resource id or Compose test tag; on the web `data-testid`, else `id` |
| `Save` | text, content description, or one part of a row's label, exactly |
| `~Sav` | label contains |
| `/^Item \d+$/` | label matches a regex |
| `btn:Save` | any of the above, limited to a role |

A query that matches nothing lists the closest elements; one that matches
several lists them and asks for a ref, a role or `--nth`.

### Refusals

- A query must match exactly one element. A ref that has left the screen is
  an error, never a tap on whatever is there now.
- A tap on an element under a floating bar (`[covered]`) is refused.
- A tap on a label that looks like a write action (like, follow, send,
  delete …) is refused unless asked for with `--confirm`. Projects add their
  own labels, and logcat patterns to flag after each action.

### Flows

```python
"""Open a note and check the editor."""
with d.dismissing("Rate us", tap="Not now"):
    d.launch(fresh=True)
    d.go("Notes", "Groceries")          # each tap waits for its target
    step = d("#toolbar").tap("Edit")    # look only inside #toolbar
    assert step.shows("#body")
    d.result(title=d("#title").text)    # handed back as one JSON line
```

`nolook run open_note` prints one line per step. On failure it prints the
step and line, the screen at that moment, and the path of a screenshot it
saved without reading.

Actions wait until their target is on screen and not covered, so flows
rarely need an explicit wait. `d(q)` is a locator that finds its element
again each time and can scope a search to a container. Every action returns
what it changed (`.added`, `.removed`, `.shows(q)`), checks are plain
`assert`, and flows can measure in place (`d.frames()`,
`with d.measure("x"):`, `d.motion(…)`, `with d.slow(4):`).

### Web pages

```sh
nolook web start http://localhost:3000   # headless; --headed shows the window
nolook --web snap
nolook --web tap "Sign in"
nolook --web open /settings              # relative to the url in [web]
nolook web status                        # the browser and its pages
nolook web stop
```

The browser keeps running between commands, so a page, its scroll position
and its sign-in stay put. Settling waits for the requests an action started
as well as for the page to stop changing. `key back` is history back.
`nolook web attach` drives a Chrome you started with
`--remote-debugging-port`.

### Animations

```sh
nolook motion --tap "#fab_new" --label open
nolook motion --key back --label close --slow 4
nolook motion-compare
```

```
motion [open] · #fab_new · 61 frames over 1.2s
runs 335ms to within 1% of its end · 50% at 54ms · 90% at 137ms · in [486, 331, 1080, 1168]
progress ▁▃▅▆▆▇██████████  decelerating (ease-out) · by position (p at 25/50/75% of time: 0.73/0.99/0.99)
monotonic: yes
14 frames in motion, median 16.4ms apart · no stalls
sheet (not read): ~/.cache/nolook/…/shots/012554-motion.jpg
```

It measures pixels inside the area that changed, comparing each frame with
the first and the last, so a spring's slow tail counts and a small menu is
not lost in a still screen. `--slow N` slows animations while recording,
for devices that record few frames.

### Performance

```sh
nolook perf frames --label before        # gfxinfo reset → flings → summary
nolook perf frames --label after --trace traces/
nolook perf compare
nolook perf ab --a "adb install -r a.apk && nolook run open-feed" --b "adb install -r b.apk && nolook run open-feed"   # A B B A …
nolook trace summary traces/nolook-….pftrace
nolook perf compile speed                # ART compile mode, then relaunch
nolook perf anr
```

`trace summary` reads a trace with Perfetto's trace processor, which it
downloads on first use, and prints jank types with what each points at,
frame-time percentiles, what the main thread ran in the worst frames, and
where the main and render threads spent their time.

## Configuration

A `.nolook.toml` in the project root (or any parent directory):

```toml
serial = "emulator-5554"
adb_port = 5037                 # a non-default adb server
package = "com.example.notes"
flows = "tools/flows"
guard = ["/api/like", "/api/post"]   # logcat regexes flagged after each action
danger = ["Like", "Send", "Delete"]  # tap refuses these labels without --confirm

[web]                           # or pass --web
url = "http://localhost:3000"   # what launch opens; open /path is relative to it
profile = "dev"                 # browser profile: cookies and sign-in live here
device = "desk"                 # window size: desk (1440×900) | phone (390×844) | WxH
```

`NOLOOK_SERIAL`, `NOLOOK_ADB_PORT` and `NOLOOK_PACKAGE` override it, and so
do `--serial` and `--package` before the command:
`nolook --package com.example.notes run flow.py`.

**Make your app readable.** For Jetpack Compose, set
`Modifier.semantics { testTagsAsResourceId = true }` near the root (debug
builds are enough) so `testTag`s appear as `#ids`, and give icon buttons a
`contentDescription`. On the web a `data-testid` shows up as `#id`, and an
`aria-label` names an icon button.

## Limits

- What the accessibility tree does not show, nolook does not see: WebView
  content (partly), canvases, games, video. Use `tap --xy` and screenshots
  there.
- Flutter apps expose little to the accessibility tree.
- `motion` judges pixels, not intent: it says how long and in what shape
  something moved, not whether it feels right. Compare against a labelled
  run of the version you trust.
- Frame timings on emulators say little; measure on a real device.
- On the web, `log`, `motion` and `perf` are not available yet. Sites that
  show a bot check to a headless browser need `nolook web attach` to a Chrome
  you started yourself.
- The pane needs a terminal with the kitty graphics protocol (Ghostty,
  kitty, iTerm2 3.5+). Inside a multiplexer that answers the graphics query
  but reports another name (herdr reports `libghostty`), set
  `CLAUDE_CODE_FORCE_TERMINAL_IMAGES=1`. It is not supported on Windows.

## Compared with

| Tool | Built for | Where nolook differs |
|---|---|---|
| adb `screencap` + `input tap` | what agents fall back to | the screenshot loop this replaces |
| Maestro | UI tests as YAML flows, mature and fast | flows are Python (branches, loops); output is written for an agent's context: refs, deltas, refusals with next steps |
| Appium / uiautomator2 MCP servers | one tool call per action, raw results | a CLI with short, deterministic output, which keeps long sessions small |
| playwright-cli, agent-browser | the same text-first approach, for browsers | one set of commands and flows for an Android app and its web counterpart |

nolook drives Android through
[uiautomator2](https://github.com/openatx/uiautomator2) and web pages through
the Chrome DevTools protocol.

## License

[MIT](https://github.com/HuanCheng65/nolook/blob/main/LICENSE)
