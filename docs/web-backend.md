# Web backend

nolook drives a web page the way it drives an Android app: the page as a
short text list, actions by query, a delta after each action. This document
states the rules the web backend follows. The order of work is in
[web-backend-plan.md](web-backend-plan.md).

## Process model

- The browser is the long-lived process. nolook has no daemon of its own.
- `nolook web start` launches Chromium with a persistent `--user-data-dir`
  and `--remote-debugging-port=0`, and reads the port from the
  `DevToolsActivePort` file Chromium writes into that directory.
- `nolook web attach <url>` connects to a browser the user already runs
  (their own Chrome, signed in, past bot checks) instead of starting one.
- Every other command is a short process: connect over CDP, act, print,
  disconnect. Refs persist in the cache directory, as on Android.
- While a command is attached it watches the network: settling after an
  action also waits for the requests the action started (requests older
  than 4 s count as long polls and are ignored), and for page loads.
- The port is read from `DevToolsActivePort`, or from the line Chromium
  prints at start: some sandboxes give the browser its own `/tmp`, where
  nolook cannot see the file.
- nolook speaks CDP directly over a WebSocket. It does not use Playwright:
  a Playwright client starts a driver process on every run, and nolook needs
  only evaluate, the accessibility tree, input events, screenshots and
  navigation. Waiting and the covered check already live in `Device`.
  Measured: connect plus one evaluate plus one full accessibility tree,
  about 90 ms per process.

## Where it plugs in

- `WebBackend` implements `Backend` and returns `list[Node]` from `dump()`.
- Everything that takes `list[Node]` is shared unchanged: refs, queries,
  near misses, deltas, formatting, settling, write-action checks.
- `tree.parse` and `_mark_covered` stay Android-only. Web does not fake
  uiautomator XML: DOM order is not paint order (z-index, fixed positioning),
  so draw-order covering is wrong for pages.
- `covered` comes from the snapshot's paint order: the topmost box at the
  node's centre that takes pointer events must be the node or a descendant.
- Field mapping: `text` = visible text, `desc` = accessible name or `title`,
  `rid` = `data-testid`, else `id`; `bounds` in CSS pixels of the viewport.
- Roles keep the shared vocabulary and add `link` (an element that
  navigates). Other ARIA roles map to the nearest existing one.

## What is listed

A dump is two CDP calls joined by backend node id (the Accessibility
domain stays enabled, so the tree follows a navigation at once):
`DOMSnapshot.captureSnapshot` (layout, computed styles, paint order) and
`Accessibility.getFullAXTree`. Nothing is injected into the page and the DOM
is never touched, so polling while settling cannot trigger the page's own
observers. Measured on a 2,750-node page: about 30 ms and 50 ms.

1. **Clickable** is the union of two kinds of evidence: an interactive role
   in the accessibility tree, or a pointer cursor on an element whose parent
   has none. A clickable element without an interactive role is listed as
   `btn`. A pointer-cursor element is not a control when all its text sits
   inside real controls (a styled wrapper around a button); it is when it
   has text of its own (a clickable row that also holds a "more" button).
   Click listeners (the snapshot's `isClickable`)
   are not used: on the pages measured, a listener without a pointer cursor
   was a container delegating to its children or a drag handle.
2. **Names** come in a fixed order: accessible name, `title`, `alt`,
   `placeholder`, then the element's own text, including text scrolled or
   clipped out of view. A field (text box, combobox, list box) with no
   accessible name takes the nearest text before it in its container; that
   name is inferred and printed as `~"…"`. Class names never become names.
   An element with no name is listed by role and position.
   Text that is a control's label (`<label for>`, a label wrapping its
   field, the target of `aria-labelledby`) is not listed again as text.
3. **Fields.** Text boxes, search boxes and spin buttons are `input`, and
   so is the outermost `contenteditable` element; its paragraphs are its
   value, not page text.
4. **Text is cleaned** before use: characters in the Unicode private use
   areas (icon fonts) are removed, whitespace is collapsed, labels are cut at
   the shared limit; `get` shows them in full.
5. **Not listed:** nameless leaves that are not interactive, wrappers with a
   single child (the child is listed in their place), subtrees under
   `aria-hidden="true"` or `inert`. When a modal dialog is open, only the
   dialog is listed.
6. **Invisible controls** (interactive, laid out, effective opacity near 0)
   are listed with `[invisible]`: a button shown on hover, a file input laid
   transparent over its button.
7. **Long listings fold.** Only the viewport is listed, so most pages fit.
   When a listing passes the line cap, the longest runs of same-shaped rows
   (a feed, a card grid) fold to their first three and `… N more like
   these`, longest first, until it fits; short runs such as a nav bar stay
   whole. This lives in the shared listing code and applies to Android too.
   `snap --all` lists everything.
8. **Bot checks and network errors.** A navigation that fails with a network
   error is retried once. A page that is a bot check, a browser error page
   or a JS dialog is reported as an interruption, with `web attach` named as
   the way past a bot check.

## Platform verbs

| Verb | Web |
|---|---|
| `tap` | mouse press and release at the centre (`Input.dispatchMouseEvent`) |
| `type` | focus, select all, `Input.insertText` |
| `key back` | history back; other keys through `Input.dispatchKeyEvent` |
| `scroll` | wheel events over the scroll container |
| `open` | `Page.navigate`; a path is resolved against the configured base URL |
| `launch` | open the configured start URL |
| `shot` | `Page.captureScreenshot` with a clip; same sizes as Android |
| `log` | the browser's console, read from the log file `web start` redirects to |
| guard | requests with a write method (POST, PUT, PATCH, DELETE) seen between an action and its settle, matched against `guard` |

## Configuration

```toml
[web]
url = "http://localhost:3000"   # base for `open` paths; `launch` opens it
profile = "dev"                 # user-data-dir name under the cache dir
device = "desk"                 # desk | phone | WxH
browser = ""                    # empty: find Chrome, Chromium or Playwright's build
proxy = ""                      # empty: none; "env" reads https_proxy
```

A `[web]` section selects the web backend. Sign-in state is the profile
directory; there is no separate state file. `doctor` checks the browser
path, the profile and the connection.

## Open questions

- New tabs (`target="_blank"`, `window.open`): follow them, or report them.
- Iframes: list same-origin frames inline; cross-origin frames need their
  own target.
- A proxy that needs credentials: Chromium takes none on its command line,
  and CDP proxy auth (`Fetch.authRequired`) works only while a nolook
  process is attached, not for loads that finish after it exits.
- Whether `motion` and `perf` get web versions (screencast frames, trace
  events) at all.
