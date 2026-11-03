# nolook

Drive Android apps from a coding agent by reading the UI tree, not screenshots.

[中文说明](README.zh-CN.md)

An agent that debugs an app on a device usually works in a loop: take a
screenshot, read it, guess the coordinates of a button, tap, take another
screenshot. Every one of those images stays in the conversation and is sent
again with every later request, so the session gets slower and more
expensive with each step, and a guessed coordinate can tap the wrong thing.

nolook gives the agent the screen as a short text list instead. It taps by
what an element says or its id, reports what changed after each action, runs
several steps in one call, and takes small cropped screenshots only when the
question is how something looks.

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

## What it does

- **Reads the screen as text.** `snap` lists interactive elements and text,
  one line each, with a short ref (`@e4`). List rows read as one line.
  Elements under a floating bar are marked `[covered]`.
- **Acts by query, safely.** `tap Groceries`, `tap #fab_new`, `tap @e4`.
  A query must match exactly one element; a ref that left the screen is an
  error, never a tap on whatever is there now. Taps on covered elements and
  on labels that look like write actions (like, follow, send, delete …) are
  refused unless asked for explicitly.
- **Reports what changed.** Every action waits for the screen to settle and
  prints the elements that appeared and disappeared, so the agent rarely
  needs to look again.
- **Runs flows.** Several steps with branches and loops, as a short Python
  file, in one call. Flows worth keeping live in the project and run by name.
- **Takes small screenshots on request.** A crop to one element, as a small
  JPEG, saved to a file; nolook prints the path and an estimate of its token
  cost, and the agent reads it only if it has to.
- **Measures animations.** It records the screen around a tap and reports
  duration, the 50% and 90% points, the shape of the progress curve,
  overshoot and stalls, with one sheet of key frames.
- **Measures performance.** Frame timing over repeated flings (gfxinfo),
  compile modes, Perfetto traces read into a one-page summary, ANR stacks.
- **Shows you the device** (Claude Code): a pane beside the chat with the
  device's screen after each command. You see it; the model does not.

## Install

Requirements: Python 3.11+, `adb` on `PATH`, a device or emulator with USB
debugging. `ffmpeg` for `motion`. On first connect nolook pushes
uiautomator2's test server to the device; nothing is installed in your app.

```sh
uv tool install nolook          # or: pipx install nolook
nolook doctor                   # checks adb, the device, ffmpeg, Perfetto
```

### Claude Code

```
/plugin marketplace add HuanCheng65/nolook
/plugin install nolook@nolook
```

The plugin adds the `nolook` skill (how to use the CLI well), a
`nolook-driver` agent for multi-step device work that reports back in text,
and the `/device` pane.

### Other agents

Any agent that can run shell commands can use the CLI. Give it
[`plugin/skills/nolook/SKILL.md`](plugin/skills/nolook/SKILL.md) as
instructions.

## Usage

```sh
nolook snap                         # what is on screen
nolook tap "Network & internet"     # tap by text; prints what changed
nolook type "hello" --into "#search"
nolook scroll down --until "About phone"
nolook wait "Saved" --timeout 5
nolook back
nolook shot "#toolbar"              # small crop; prints its path
nolook run flows/open_note.py       # several steps in one call
```

### Queries

| Query | Matches |
|---|---|
| `@e12` | a ref from the last output |
| `#title` | resource id or Compose test tag |
| `Save` | text, content description, or one part of a row's label, exactly |
| `~Sav` | label contains |
| `/^Item \d+$/` | label matches a regex |
| `btn:Save` | any of the above, limited to a role |

A query that matches nothing lists the closest elements; one that matches
several lists them and asks for a ref, a role or `--nth`.

### Flows

```python
"""Open a note and check the editor."""
d.launch(fresh=True)
d.tap("Groceries")
if d.exists("Discard draft?"):
    d.tap("Keep")
d.check(d.exists("#body"), "editor open")
d.shot("#toolbar")
```

`nolook run open_note` prints one line per step. On failure it prints the
step and line, the screen at that moment, and the path of a screenshot it
saved without reading.

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
```

`NOLOOK_SERIAL` and `NOLOOK_ADB_PORT` override it.

For Jetpack Compose, set `Modifier.semantics { testTagsAsResourceId = true }`
near the root (debug builds are enough) so `testTag`s appear as `#ids`, and
give icon buttons a `contentDescription`.

## Limits

- What the accessibility tree does not show, nolook does not see: WebView
  content (partly), canvases, games, video. Use `tap --xy` and screenshots
  there.
- Flutter apps expose little to the accessibility tree.
- `motion` judges pixels, not intent: it says how long and in what shape
  something moved, not whether it feels right. Compare against a labelled
  run of the version you trust.
- Frame timings on emulators say little; measure on a real device.
- The pane needs a terminal with the kitty graphics protocol (Ghostty,
  kitty, iTerm2 3.5+). Inside a multiplexer that answers the graphics query
  but reports another name (herdr reports `libghostty`), set
  `CLAUDE_CODE_FORCE_TERMINAL_IMAGES=1`. It is not supported on Windows.

## Compared with

- **adb `screencap` + `input tap`**: what agents fall back to; the
  screenshot loop this replaces.
- **Maestro**: YAML flows for UI tests, mature and fast. nolook's flows are
  Python (branches, loops), and its output is written for an agent's
  context: refs, deltas, refusals with next steps.
- **Appium / uiautomator2 MCP servers**: one tool call per action, raw
  results. nolook is a CLI with short, deterministic output, which keeps
  long sessions small.
- **For the web**: [playwright-cli](https://github.com/microsoft/playwright-cli)
  and [agent-browser](https://agent-browser.dev/) take the same approach for
  browsers.

nolook is built on [uiautomator2](https://github.com/openatx/uiautomator2).

## License

MIT
