# Flow API

Everything `d` offers inside a flow. SKILL.md has the patterns; this is the
full list. `args` holds the words after the flow's name on the command line.

## Acting

Every action waits for its target (up to `d.timeout`, 5 s by default) until it
is on screen, matches exactly once and is not covered, then acts, waits for
the screen to settle, prints one line, and returns a **Step**.

| Call | Does |
|---|---|
| `d.tap(q, nth=None, confirm=False, force=False, timeout=None)` | tap |
| `d.long_tap(q)` | long press |
| `d.type(text, into=q, append=False, enter=False)` | set a field's text |
| `d.key(name)`, `d.back()` | a key; back (on web: history back) |
| `d.go(*labels)` | tap each label in turn: `d.go("Settings", "Display")` |
| `d.scroll(dir="down", within=q, until=q, max_swipes=10)` | scroll; with `until`, until it shows |
| `d.fling(times=1, within=q)` | quick flings (lists, for perf) |
| `d.open(uri)` | deep link (Android) or URL / path under `url` (web) |
| `d.launch(fresh=False)` | start the app (web: open `url`) |
| `d.tap_xy(x, y)` | coordinates; only for canvases and views the tree cannot see |

A **Step** is what the action did:

| | |
|---|---|
| `step.shows(q)` | the screen after the action has q |
| `step.added`, `step.removed` | nodes that appeared / disappeared |
| `step.changed` | whether the whole screen changed |
| `step.settled` | seconds to settle, or None if it never did |
| `step.label`, `step.ref`, … | the target's own attributes |

## Waiting

- `d.wait(q, timeout=10)` returns the node once q shows; `d.gone(q)` waits for
  it to leave. Use them for a result that is not the next thing you act on.
- Each action prints how long it waited for its target when that took
  noticeable time and marks waits over `d.slow_wait` (2 s) with ⚠; the last
  line counts them. A page that got slower still shows up.
- `d.sleep(s)` exists for real time-based behaviour (a debounce, a timer the
  test is about). Settling already waits for the screen and, on web, for the
  requests an action started.

## Reading (no output line)

`d.sees(q)` (on screen and not covered), `d.exists(q)`, `d.find(q)` (all
matching nodes), `d.text(q)`, `d.snap()` (every listed node). A node has
`.label`, `.text`, `.desc`, `.rid`, `.role`, `.bounds`, `.center`, `.flags`,
`.covered`.

## Locators

`d(q)` names an element and finds it again every time it is used, so it
survives the screen changing between uses.

- Scope: `d("#sheet")("OK")` or `d("#sheet").tap("OK")` looks only inside
  `#sheet`. A container with an id (on web, `data-testid` or `id`) works as
  a scope even though `snap` does not list it.
- Pick: `.first()`, `[i]`, `.all()` (a list of locators), `.count()`.
- Read: `.text`, `.label`, `.node`, `.exists()`.
- Act: `.tap()`, `.long_tap()`, `.type(t)`, `.scroll(dir, until=q)`,
  `.fling(n)`, `.wait()`, `.gone()`, `.shot()`.

## Interruptions

- `with d.dismissing("Rate us", tap="Not now"):` closes that dialog whenever
  it shows inside the block; `d.dismiss_when(q, tap=…)` for the whole flow.
- The write-action guard applies to what these tap too.

## Results and checks

- `d.result(key=value, …)` collects values; they print as one
  `result {…}` JSON line at the end, also when the flow fails. This is the
  flow's answer: put what you were asked to find out here, not in `print`.
- `assert …` or `d.check(cond, "what")` (prints a ✓ line); `d.fail(msg)`.
- `d.note(msg)` prints a `·` line.
- On failure the flow prints the failing step and line, the screen at that
  moment, and the path of a screenshot it saved (not read).

## Measuring (Android)

`d.frames(label=…)`, `with d.measure("label"):`, `d.motion(tap=q, label=…)`,
`with d.slow(4):`, `d.compile("speed")`. Each prints a summary line and
returns a dict; see performance.md and motion.md.

## Files

- One-off: `nolook run - <<'EOF' … EOF`.
- Kept: a `.py` file in the project's flows directory (`flows` in
  `.nolook.toml`); `nolook flows` lists them by their docstring's first
  line, `nolook run NAME args…` runs one. A flow can import helpers from the
  files beside it (`from common import sign_in`).
