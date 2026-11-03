"""One connected device and the actions on it.

Every action reads a fresh tree before it acts and waits for the screen to
settle after, so its caller gets back what changed without asking again.
The platform's part, reading the screen and sending input, is a Backend.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import tree
from .backend import Backend, NolookError
from .config import Config, cache_dir, session_dir
from .tree import Node, QueryError

if TYPE_CHECKING:
    from .android import AndroidBackend

SETTLE_POLL = 0.15
SETTLE_MIN = 0.3
JOURNAL_KEEP = 300


@dataclass
class Outcome:
    """What an action did: the target, the screen before and after, and how long it took to settle."""

    target: Node | None
    before: list[Node]
    after: list[Node]
    settled: float | None  # seconds; None when it never settled
    guard_hits: list[str]


class Device:
    def __init__(self, cfg: Config, backend: Backend | None = None):
        self.cfg = cfg
        if backend is None:
            from .android import AndroidBackend

            backend = AndroidBackend(cfg.serial)
        self.backend = backend
        self.serial = backend.id
        _remember_device(self.serial)
        self.size = backend.size
        self.dir = cache_dir(self.serial)
        self._state_file = self.dir / "state.json"
        try:
            st = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            st = {}
        self._prev: list[dict] = st.get("nodes", [])
        self._counter: int = st.get("counter", 0)

    # Reading ----------------------------------------------------------------

    @property
    def android(self) -> AndroidBackend:
        """The backend, for what only Android has (adb shell, gfxinfo, screenrecord)."""
        from .android import AndroidBackend

        if not isinstance(self.backend, AndroidBackend):
            raise NolookError("this needs an Android device")
        return self.backend

    def nodes(self) -> list[Node]:
        ns = [n for n in self.backend.dump() if n.pkg not in self.cfg.hide]
        self._counter = tree.assign_refs(ns, self._prev, self._counter)
        self._prev = tree.to_state(ns)
        self._state_file.write_text(json.dumps({"nodes": self._prev, "counter": self._counter}))
        return ns

    def find(self, q: str, nth: int | None = None) -> tuple[Node, list[Node]]:
        ns = self.nodes()
        try:
            return tree.resolve(ns, q, nth), ns
        except tree.NoMatch as e:
            raise tree.NoMatch(self.explain(str(e))) from None

    def interruption(self) -> str | None:
        """Why the app may not be showing what a step expects: screen off, locked, another app in front."""
        try:
            return self.backend.interruption(self.cfg.package)
        except Exception:  # noqa: BLE001 - a diagnosis must never hide the original error
            return None

    def explain(self, msg: str) -> str:
        why = self.interruption()
        return f"{msg}\nlikely why: {why}" if why else msg

    def exists(self, q: str) -> bool:
        return bool(tree.match(self.nodes(), q))

    def settle(self, timeout: float = 3.0) -> tuple[list[Node], float | None]:
        start = time.monotonic()
        time.sleep(SETTLE_MIN)
        last = self.nodes()
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                return last, None
            time.sleep(SETTLE_POLL)
            cur = self.nodes()
            if _sig(cur) == _sig(last):
                return cur, round(time.monotonic() - start, 2)
            last = cur

    # Acting -----------------------------------------------------------------

    def _act(self, before: list[Node], target: Node | None, fn, settle: float) -> Outcome:
        mark = self._device_time()
        fn()
        after, took = self.settle(settle)
        return Outcome(target, before, after, took, self._guard(mark))

    def tap(self, q: str, nth: int | None = None, long: bool = False, force: bool = False, confirm: bool = False, settle: float = 3.0) -> Outcome:
        n, ns = self.find(q, nth)
        hit = self.dangerous(n)
        if hit and not confirm:
            raise NolookError(
                f"{tree.format_node(n, compact=True)} looks like a write action ({hit!r}); not tapped.\n"
                "Tap it only if the user asked for this action: repeat with --confirm."
            )
        if n.covered and not force:
            by = f" by {tree.format_node(n.covered_by, compact=True)}" if n.covered_by else ""
            raise NolookError(
                f"{tree.format_node(n, compact=True)} is covered{by}; a tap there lands on what is on top.\n"
                "Scroll it into view (nolook scroll … --until), or pass --force if that is intended."
            )
        x, y = n.center
        fn = (lambda: self.backend.long_click(x, y)) if long else (lambda: self.backend.click(x, y))
        return self._done("long-press" if long else "tap", self._act(ns, n, fn, settle), q)

    def dangerous(self, n: Node) -> str | None:
        words = [p.lower() for p in n.parts] + [n.rid.lower()]
        for d in self.cfg.danger:
            d = d.lower()
            # Whole parts for short CJK words ("赞 · 1267"), substrings for ids.
            if any(w == d or w.startswith(d + " ") or (len(d) > 3 and d in w) for w in words):
                return d
        return None

    def tap_xy(self, x: int, y: int, settle: float = 3.0) -> Outcome:
        ns = self.nodes()
        return self._done("tap", self._act(ns, None, lambda: self.backend.click(x, y), settle), f"({x},{y})", point=(x, y))

    def type(self, text: str, q: str | None = None, nth: int | None = None, append: bool = False, settle: float = 2.0) -> Outcome:
        if q is not None:
            n, ns = self.find(q, nth)
            if not n.flags.get("focused"):
                self.backend.click(*n.center)
                time.sleep(0.3)
        ns = self.nodes()
        field = next((n for n in ns if n.role == "input" and n.flags.get("focused")), None)
        if field is None:
            raise NolookError("no focused text field; give one: nolook type '<text>' --into <query>")
        value = (field.text + text) if append else text
        return self._done("type", self._act(ns, field, lambda: self.backend.set_text(value), settle), q)

    def key(self, name: str, settle: float = 3.0) -> Outcome:
        ns = self.nodes()
        return self._done("key", self._act(ns, None, lambda: self.backend.press(name), settle), name)

    def scroll(self, direction: str, within: str | None = None, until: str | None = None, max_swipes: int = 10, settle: float = 3.0) -> Outcome:
        ns = self.nodes()
        box = self._scroll_box(ns, within)
        l, t, r, b = box.bounds if box else (0, 0, *self.size)
        cx, cy = (l + r) // 2, (t + b) // 2
        dx, dy = (r - l) * 0.3, (b - t) * 0.3
        # "down" reveals what is further down: the finger moves up.
        vec = {"down": (0, -dy), "up": (0, dy), "right": (-dx, 0), "left": (dx, 0)}[direction]
        start = ns

        def swipe():
            self.backend.swipe(cx - vec[0], cy - vec[1], cx + vec[0], cy + vec[1], 0.25)

        mark = self._device_time()
        cur, took = ns, None
        for _ in range(max_swipes if until else 1):
            if until and _visible_hit(cur, until):
                break
            swipe()
            prev = cur
            cur, took = self.settle(settle)
            if until and _sig(cur) == _sig(prev):
                raise NolookError(f"reached the end scrolling {direction}; {until!r} never came into view")
        else:
            if until and not _visible_hit(cur, until):
                raise NolookError(f"{until!r} not in view after {max_swipes} swipes")
        target = _visible_hit(cur, until) if until else box
        return self._done(f"scroll {direction}", Outcome(target, start, cur, took, self._guard(mark)), until)

    def _scroll_box(self, ns: list[Node], within: str | None) -> Node | None:
        if within:
            return tree.resolve(ns, within)
        boxes = [n for n in ns if n.role == "scroll"]
        if not boxes:
            return None
        # The largest; of equals (a pager around a list), the innermost.
        return max(boxes, key=lambda n: ((n.bounds[2] - n.bounds[0]) * (n.bounds[3] - n.bounds[1]), n.depth))

    def wait(self, q: str, gone: bool = False, timeout: float = 10.0) -> tuple[list[Node], float]:
        start = time.monotonic()
        while True:
            ns = self.nodes()
            present = bool(tree.match(ns, q))
            if present != gone:
                return ns, round(time.monotonic() - start, 2)
            if time.monotonic() - start > timeout:
                state = "still there" if gone else "never appeared"
                raise NolookError(f"{q!r} {state} after {timeout:g}s")
            time.sleep(SETTLE_POLL)

    def open(self, uri: str, settle: float = 5.0) -> Outcome:
        ns = self.nodes()
        return self._done("open", self._act(ns, None, lambda: self.backend.open_uri(uri, self.cfg.package), settle), uri)

    def launch(self, package: str | None = None, fresh: bool = False, settle: float = 6.0) -> Outcome:
        pkg = package or self.cfg.package
        if not pkg:
            raise NolookError("no package: pass one, or set package in .nolook.toml")
        entry = self.backend.launcher(pkg)
        if fresh:
            self.backend.stop(pkg)
        ns = self.nodes()
        mark = self._device_time()
        self.backend.start(entry)
        # A starting app shows nothing for a moment, and nothing is stable too:
        # wait for the app's own elements before waiting for them to settle.
        start = time.monotonic()
        while time.monotonic() - start < settle * 2:
            if any(n.pkg == pkg for n in self.nodes()):
                break
            time.sleep(SETTLE_POLL)
        after, took = self.settle(settle)
        took = None if took is None else round(time.monotonic() - start, 2)
        return self._done("launch", Outcome(None, ns, after, took, self._guard(mark)), pkg)

    # Pixels -----------------------------------------------------------------

    def shot(self, q: str | None = None, region: tuple[int, int, int, int] | None = None, width: int | None = None, full: bool = False, out: str | None = None, png: bool = False) -> dict:
        img = self.backend.screenshot()
        label = "screen"
        if q is not None:
            n, _ = self.find(q)
            pad = 12
            l, t, r, b = n.bounds
            region = (max(l - pad, 0), max(t - pad, 0), min(r + pad, img.width), min(b + pad, img.height))
            label = n.rid or n.label or n.role
        if region is not None:
            img = img.crop(region)
            label = label if q is not None else "region"
        if not full:
            # Whole screens are capped by width, crops by their longest edge.
            if region is None:
                cap, edge = width or 540, img.width
            else:
                cap, edge = width or 900, max(img.size)
            if edge > cap:
                k = cap / edge
                img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))))
        shots = self.dir / "shots"
        shots.mkdir(exist_ok=True)
        _prune(shots, keep=200)
        ext = "full.png" if full else "png" if png else "jpg"
        slug = re.sub(r"[^\w-]+", "-", label)[:24].strip("-") or "shot"
        path = Path(out) if out else shots / f"{time.strftime('%H%M%S')}-{slug}.{ext}"
        if full or png:
            img.save(path, "PNG")
        else:
            img.convert("RGB").save(path, "JPEG", quality=72)
        self.journal({"verb": "shot", "query": q, "picture": str(path)})
        return {
            "path": str(path),
            "size": img.size,
            "kb": round(path.stat().st_size / 1024),
            "tokens": round(img.width * img.height / 750),
        }

    # The journal ---------------------------------------------------------------

    def journal(self, entry: dict) -> None:
        """One line per action in actions.jsonl, which the Claude Code pane shows."""
        f = self.dir / "actions.jsonl"
        try:
            with f.open("a") as fh:
                fh.write(json.dumps({"at": round(time.time(), 2), **entry}, ensure_ascii=False) + "\n")
            if f.stat().st_size > 256_000:
                f.write_text("\n".join(f.read_text().splitlines()[-JOURNAL_KEEP:]) + "\n")
        except OSError:
            pass

    def _done(self, verb: str, o: Outcome, query: str | None = None, point: tuple[int, int] | None = None) -> Outcome:
        e: dict = {"verb": verb, "query": query, "settled": o.settled}
        if o.target is not None:
            t = o.target
            # A field's label is its text: what was typed there before, perhaps a password.
            label = "" if verb == "type" else t.label[:60]
            e["target"] = {"role": t.role, "label": label, "id": t.rid, "bounds": list(t.bounds)}
        if point is not None:
            e["point"] = list(point)
        lines, wholesale = tree.delta(o.before, o.after)
        e["change"] = "screen" if wholesale else len(lines)
        if o.guard_hits:
            e["guard"] = o.guard_hits[:3]
        self.journal(e)
        return o

    @contextmanager
    def busy(self, what: str):
        """Holds the pane's mirror off the device while a measurement runs: its screenshots would cost frames."""
        f = self.dir / "busy"
        f.write_text(json.dumps({"pid": os.getpid(), "what": what}))
        try:
            yield
        finally:
            f.unlink(missing_ok=True)

    def _record_perf(self, rec: dict) -> None:
        with (self.dir / "perf.jsonl").open("a") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "serial": self.serial, **rec}, ensure_ascii=False) + "\n")

    # Logs -------------------------------------------------------------------

    def log(self, lines: int = 40, grep: str | None = None, level: str | None = None) -> list[str]:
        return self.backend.log(lines, grep, level, self.cfg.package)

    def _guard(self, since: str | None) -> list[str]:
        if not self.cfg.guard or since is None:
            return []
        out = self.backend.log_since(since)
        rx = re.compile("|".join(f"(?:{g})" for g in self.cfg.guard))
        return [ln for ln in out.splitlines() if rx.search(ln)][:10]

    def _device_time(self) -> str | None:
        return self.backend.log_mark() if self.cfg.guard else None


def _remember_device(serial: str) -> None:
    # Which device this Claude Code session drove last, so a pane can show the
    # same one: the agent's commands may pick it by a config or env var the
    # pane's own process never sees.
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session or not re.fullmatch(r"[\w-]{1,80}", session):
        return
    d = session_dir()
    rec = {"serial": serial, "adb_port": os.environ.get("ANDROID_ADB_SERVER_PORT"), "time": time.time()}
    try:
        (d / f"{session}.json").write_text(json.dumps(rec))
    except OSError:
        pass


def _sig(ns: list[Node]) -> list:
    return [(n.key, n.bounds) for n in ns]


def _visible_hit(ns: list[Node], q: str) -> Node | None:
    hits = [n for n in tree.match(ns, q) if not n.covered]
    return hits[0] if hits else None


def _prune(d: Path, keep: int) -> None:
    files = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime)
    for p in files[:-keep]:
        p.unlink(missing_ok=True)


__all__ = ["Device", "NolookError", "Outcome", "QueryError"]
