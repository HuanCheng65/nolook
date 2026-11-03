---
name: nolook
description: Drive an Android app on a device or emulator through its UI tree instead of screenshots — list what is on screen, tap by text or id, type, scroll, wait, run multi-step Python flows, and take small cropped screenshots only when a visual check needs one. Use whenever you need to operate, check or debug an app on a connected Android device.
---

# nolook

`nolook` reads the screen as text and acts on elements by what they say or
their id. You rarely need a screenshot; when you do, take a small crop.

## Why this matters

Every image you read stays in the conversation and is sent again with every
later request. Ten full screenshots slow every turn of the rest of the
session. The UI tree answers almost every "what is on screen" question in a
few hundred tokens.

## The loop

```
nolook snap                 # what is on screen, one element per line
nolook tap 发送              # act; prints what changed
nolook wait 发送成功          # when the result takes time
```

A `snap` line looks like:

```
@e12  btn    "回复楼主" #reply_button (540,2210)
  @e30  btn    "标题 · 3 回复" [covered] (540,2221)
```

`@e12` is a ref. It names that element until it leaves the screen; using it
afterwards is an error, never a tap on whatever replaced it. Indentation
means "inside a scrolling list". `[covered]` means something is drawn on top
(a floating bar): a tap would land on that, so nolook refuses it; scroll
first.

Every action prints its target, how long the screen took to settle, and what
changed (`+` appeared, `-` disappeared), or the whole new screen when it
changed completely. Don't `snap` again after an action unless you need the
full list.

## Queries

| Query | Matches |
|---|---|
| `@e12` | a ref from the last output |
| `#reply_button` | resource id or Compose test tag |
| `回复` | text, description, or one part of a row's label, exactly |
| `~回复` | label contains |
| `/^第\d+楼$/` | label regex |
| `btn:回复` | any of the above, limited to a role: btn input text img scroll check switch |

A query that matches nothing lists the closest elements; one that matches
several lists them. Pick a ref, add a role, or pass `--nth N`. Never fall back
to coordinates because a query failed; `tap --xy` is for canvases and
WebViews the tree cannot see.

## Commands

```
nolook snap [--all] [--in Q]            list elements (80 lines unless --all)
nolook get Q                            one element in full: text, id, bounds, flags
nolook tap Q [--nth N] [--long]         tap; --force only when tapping what covers it is intended
nolook type TEXT [--into Q] [--append] [--enter]
nolook key back|home|enter|…            nolook back for short
nolook scroll [down|up|left|right] [--in Q] [--until Q]
nolook wait Q [--gone] [--timeout S]
nolook open URI                         deep link into the app
nolook launch [--fresh]                 start (or restart) the app
nolook shot [Q] [--region L,T,R,B]      save a screenshot, print its path
nolook log [-n N] [--grep RX] [--level E]
nolook run FLOW [args]                  run a Python flow
nolook flows                            list the project's saved flows
```

`-q` before the command prints only the action line.

## Several steps: write a flow

When you know the next steps, put them in one flow instead of one command per
turn. Branches and loops are plain Python; `d` is the device:

```python
"""Reply bar shows the risk notice on first use."""
d.open("oolong://thread/123")
d.wait("回复")
d.tap("#reply_button")
if d.exists("我知道了"):
    d.tap("我知道了")
d.check(d.exists("#composer_input"), "composer opened")
d.shot("#composer")
```

`nolook run path/to/flow.py`. Each step prints one line. On failure it prints
the failing step and line, the screen at that moment, and the path of a
screenshot it saved (not read). Flows worth keeping go in the project's flows
directory (`nolook flows` lists them, with their docstrings), so the next
session can run them by name.

Flow methods: `tap long_tap tap_xy type key back scroll open launch wait gone
shot` act and print a line; `exists find text snap log` only read, for
branching; `check(cond, msg)` and `fail(msg)` stop the flow.

## Screenshots

Take one only to judge how something looks: colour, blur, alignment,
spacing, an animation's end state. Never to find out what is on screen or
where to tap.

- Crop to what you are judging: `nolook shot "#top_bar"` or
  `--region 0,0,1080,400`. A crop costs a fraction of a full screen.
- The default is a small JPEG (540 px whole screen, 900 px crops). It prints
  the path, size and an approximate token cost. Read the file only if you
  need to see it.
- `--full` gives a native PNG; use it when the detail is the point.

## Performance

```
nolook perf frames --label A       # reset gfxinfo, swipe the list back and forth, summarise
nolook perf frames --trace out/    # same, with a Perfetto trace saved to out/
nolook perf compare [A B …]        # table of recorded runs
nolook perf compile speed|verify|speed-profile|reset   # then relaunch the app
nolook perf reset … nolook perf read                   # measure your own interaction
nolook perf anr                    # last ANR: header and main-thread stack
```

The summary is janky % (new and legacy), p50/p90/p99 frame time, GPU p90,
slow UI thread and missed-deadline counts, plus the compile mode. Measure on
a real device; emulator frame times say little. Change one thing between
labelled runs.

## Safety

The app may be logged in to a real account. Taps are by query, so they hit
what you meant or fail; still, read what an action changed before the next
one. If the project lists `guard` patterns in `.nolook.toml`, any matching
log line after an action (a like, a post) is printed as `⚠ guard:`. Stop and
tell the user when you see one you did not intend.

## Setup

A `.nolook.toml` at the project root picks the device and app:

```toml
serial = "emulator-5556"
adb_port = 5038          # if the device is on a non-default adb server
package = "com.example.app"
flows = "tools/flows"
guard = ["opAgree", "add_post"]
```

Compose apps: set `Modifier.semantics { testTagsAsResourceId = true }` near
the root (debug builds are enough) so `testTag`s show up as `#ids`. Give icon
buttons a `contentDescription`; without one they show up as bare `btn` lines.
