# Web backend: plan

Rules are in [web-backend.md](web-backend.md). Each step ends usable.

Status 2026-11-04: steps 1–3 done (`tests/test_web.py`), checked by hand on
a signed-in production web app and on a large public video site. Settling
on pages with carousels runs to its timeout; that is expected.

1. **Connect and list.** `web start | attach | stop`, `WebBackend.dump()`
   with rules 1–5, `snap`. Done when `snap` prints a usable list for a local
   page and for a page built from `div`s with pointer cursors.
2. **Act.** `tap`, `type`, `key`, `scroll`, `wait`, `open`, `launch`, `shot`;
   covered check by paint order; rule 6 (`[invisible]`).
3. **Long listings.** Rule 7 in the shared listing code.
4. **Profiles and devices.** Persistent profiles, `device` presets through
   `Emulation.setDeviceMetricsOverride` with touch, proxy, `doctor`.
5. **Guard and log.** Network watch between action and settle; console log
   file. Verify first that `--enable-logging=stderr` carries console
   messages in headless mode; if not, record them while attached and say so.
6. **Interruptions.** Rule 8: retry on network errors, detect bot checks,
   error pages and dialogs.

## Tests

Synthetic HTML fixtures, one per case, run in headless Chromium the way
`tests/test_tree.py` runs saved dumps:

- cards built from `div`s with a pointer cursor and nested text;
- icon-font buttons with private-use characters and no name;
- a select whose label is a sibling, not a `<label>`;
- a list whose click listener sits on the container;
- a button revealed only on row hover (transparent until then);
- a page under an `aria-hidden` scrim with a modal dialog;
- wrappers nested three deep around one button;
- a z-indexed bar over a scrolled list (covered).

## Measured

- `snap` on a large public video site's home page: about 0.25 s per call,
  85 elements.
- Dump on a 2,750-node page: snapshot 30 ms, accessibility tree 50 ms.
