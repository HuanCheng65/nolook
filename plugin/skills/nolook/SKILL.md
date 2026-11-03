---
name: nolook
description: Drive and inspect an Android app on a device or emulator through its UI tree instead of screenshots — see what is on screen, tap by text or id, type, scroll, wait, run multi-step Python flows, take small cropped screenshots for visual checks, measure animations and frame timing. Use whenever a task involves a connected Android device or emulator — operating the app, checking or describing what a screen shows or how it looks, verifying a change, debugging, or performance — and before reaching for adb screencap, input tap or uiautomator dump.
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
nolook tap Save             # act; prints what changed
nolook wait Saved           # when the result takes time
```

A `snap` line looks like:

```
@e12  btn    "New note" #fab_new (960,2180)
  @e30  btn    "Groceries · Milk, eggs" [covered] (540,2221)
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
| `#fab_new` | resource id or Compose test tag |
| `Save` | text, description, or one part of a row's label, exactly |
| `~Sav` | label contains |
| `/^Item \d+$/` | label regex |
| `btn:Save` | any of the above, limited to a role: btn input text img scroll check switch |

Labels are matched as the app shows them, in any language: `tap 保存`
works the same way.

A query that matches nothing lists the closest elements; one that matches
several lists them. Pick a ref, add a role, or pass `--nth N`. Never fall back
to coordinates because a query failed; `tap --xy` is for canvases and
WebViews the tree cannot see.

## Commands

```
nolook snap [--all] [--in Q]            list elements (80 lines unless --all)
nolook get Q                            one element in full: text, id, bounds, flags
nolook tap Q [--nth N] [--long]         tap (see Safety for --confirm and --force)
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
"""Opening a note shows the editor, with or without a saved draft."""
d.open("notes://note/42")
d.wait("Groceries")
d.tap("#edit")
if d.exists("Discard draft?"):
    d.tap("Keep")
d.check(d.exists("#body"), "editor open")
d.shot("#toolbar")
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

## Animations

You can't watch a video; `nolook motion` measures one for you.

```
nolook motion --tap "#fab_new" --label open          # record, tap, measure
nolook motion --key back --label close
nolook motion --after "Saved" --for 2                 # record until a state shows, then measure
nolook motion --el "#sheet" --tap … --slow 4           # measure one element; animations 4× slower
nolook motion-compare                                  # recorded runs side by side
```

It reports, for the area that changed: how long the motion runs until it
is within 1% of its end, when it reaches 50% and 90%, the shape of its
progress curve (a sparkline plus ease-out / ease-in / linear …), whether
it reverses (overshoot, bounce) or leads in the other way first, where
frames stalled, and the renderer's jank for the same window. It also saves
one sheet of key frames (0/25/50/75/100% and any reversal) and prints its
path; read it when the question is what it looks like, not how it moves.

To check an animation against its code, work out the expected times from
the springs or tweens and compare: a spring with damping ζ and stiffness k
settles to 1% in about ln(100)/(ζ√k) seconds when underdamped. Use `--slow`
when the device records few frames (emulators record 15–30 fps). If the
report says it was still moving when recording stopped, raise `--for`.

## Performance

```
nolook perf frames --label A       # reset gfxinfo, swipe the list back and forth, summarise
nolook perf frames --trace out/    # same, with a Perfetto trace saved to out/
nolook perf compare [A B …]        # table of recorded runs
nolook perf compile speed|verify|speed-profile|reset   # then relaunch the app
nolook perf reset … nolook perf read                   # measure your own interaction
nolook perf anr                    # last ANR: header and main-thread stack
nolook trace summary out/x.pftrace # read a trace: jank types, worst frames, thread time, GC, binder
nolook trace sql out/x.pftrace "select name, sum(dur) from slice group by name order by 2 desc limit 10"
```

Never open a trace file yourself; `trace summary` reads it with Perfetto's
trace processor (fetched on first use) and prints a page. It tells whether
jank is the app missing deadlines (main or render thread) or the pipeline
stuffed (render thread / GPU behind), and what ran inside the worst frames.
Durations in `sql` are nanoseconds.

Swipes fling at `--speed` px/s (default 10000, a quick flick); `--duration
0.25` reproduces a 250 ms `input swipe`. The summary prints the speed the
device actually reached, which is lower when it paces the steps slower.

The summary is janky % (new and legacy), p50/p90/p99 frame time, GPU p90,
slow UI thread and missed-deadline counts, plus the compile mode. Measure on
a real device; emulator frame times say little. Change one thing between
labelled runs.

## Safety

The app may be logged in to a real account. Taps are by query, so they hit
what you meant or fail; still, read what an action changed before the next
one.

- A tap on a label that looks like a write action (like, follow, send, post,
  delete, …; the project can set its own `danger` list) is refused. Pass
  `--confirm` only when the user asked for that action. Never use it to get
  past a refusal while testing something else.
- A tap on a `[covered]` element is refused. Scroll it into view; pass
  `--force` only when tapping what covers it is the point.
- If the project lists `guard` patterns, any matching log line after an
  action (a like, a post) is printed as `⚠ guard:`. Stop and tell the user
  when you see one you did not intend.
- Test what a query resolves to with `nolook get Q`, which does not act;
  never with `tap`.

## Setup

If `nolook` is not on PATH, it installs with `uv tool install nolook` (or
`pipx install nolook`). `nolook doctor` checks adb, the device, ffmpeg and
Perfetto, and says what to fix; run it first when something fails to
connect.

A `.nolook.toml` at the project root picks the device and app:

```toml
serial = "emulator-5554"
adb_port = 5037          # if the device is on a non-default adb server
package = "com.example.notes"
flows = "tools/flows"
guard = ["/api/like", "/api/post"]
danger = ["Like", "Send", "Delete"]
```

Compose apps: set `Modifier.semantics { testTagsAsResourceId = true }` near
the root (debug builds are enough) so `testTag`s show up as `#ids`. Give icon
buttons a `contentDescription`; without one they show up as bare `btn` lines.
