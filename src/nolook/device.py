"""One connected device and the actions on it.

Every action reads a fresh tree before it acts and waits for the screen to
settle after, so its caller gets back what changed without asking again.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import tree
from .config import Config, cache_dir
from .tree import Node, QueryError

SETTLE_POLL = 0.15
SETTLE_MIN = 0.3


class NolookError(Exception):
    pass


@dataclass
class Outcome:
    """What an action did: the target, the screen before and after, and how long it took to settle."""

    target: Node | None
    before: list[Node]
    after: list[Node]
    settled: float | None  # seconds; None when it never settled
    guard_hits: list[str]


class Device:
    def __init__(self, cfg: Config):
        import uiautomator2 as u2  # imported late: --help should not pay for it

        self.cfg = cfg
        try:
            self.u2 = u2.connect(cfg.serial) if cfg.serial else u2.connect()
        except Exception as e:  # noqa: BLE001 - u2 raises many kinds
            raise NolookError(f"cannot connect to {cfg.serial or 'the default device'}: {e}") from e
        self.serial = self.u2.serial
        self.size = self.u2.window_size()
        self.dir = cache_dir(self.serial)
        self._state_file = self.dir / "state.json"
        try:
            st = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            st = {}
        self._prev: list[dict] = st.get("nodes", [])
        self._counter: int = st.get("counter", 0)

    # Reading ----------------------------------------------------------------

    def nodes(self) -> list[Node]:
        # A dump can fail while the app is starting or a window is changing.
        for attempt in range(3):
            try:
                xml = self.u2.dump_hierarchy()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise NolookError(f"cannot read the UI tree: {type(e).__name__}: {e}") from e
                time.sleep(0.4)
        ns = [n for n in tree.parse(xml, self.size) if n.pkg not in self.cfg.hide]
        self._counter = tree.assign_refs(ns, self._prev, self._counter)
        self._prev = tree.to_state(ns)
        self._state_file.write_text(json.dumps({"nodes": self._prev, "counter": self._counter}))
        return ns

    def find(self, q: str, nth: int | None = None) -> tuple[Node, list[Node]]:
        ns = self.nodes()
        return tree.resolve(ns, q, nth), ns

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
        fn = (lambda: self.u2.long_click(x, y)) if long else (lambda: self.u2.click(x, y))
        return self._act(ns, n, fn, settle)

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
        return self._act(ns, None, lambda: self.u2.click(x, y), settle)

    def type(self, text: str, q: str | None = None, nth: int | None = None, append: bool = False, settle: float = 2.0) -> Outcome:
        if q is not None:
            n, ns = self.find(q, nth)
            if not n.flags.get("focused"):
                self.u2.click(*n.center)
                time.sleep(0.3)
        ns = self.nodes()
        field = next((n for n in ns if n.role == "input" and n.flags.get("focused")), None)
        if field is None:
            raise NolookError("no focused text field; give one: nolook type '<text>' --into <query>")
        el = self.u2(focused=True)
        value = (field.text + text) if append else text
        return self._act(ns, field, lambda: el.set_text(value), settle)

    def key(self, name: str, settle: float = 3.0) -> Outcome:
        ns = self.nodes()
        return self._act(ns, None, lambda: self.u2.press(name), settle)

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
            self.u2.swipe(cx - vec[0], cy - vec[1], cx + vec[0], cy + vec[1], duration=0.25)

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
        return Outcome(target, start, cur, took, self._guard(mark))

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
        cmd = ["am", "start", "-a", "android.intent.action.VIEW", "-d", uri]
        if self.cfg.package:
            cmd += ["-p", self.cfg.package]
        return self._act(ns, None, lambda: self._shell(cmd), settle)

    def launch(self, package: str | None = None, fresh: bool = False, settle: float = 6.0) -> Outcome:
        pkg = package or self.cfg.package
        if not pkg:
            raise NolookError("no package: pass one, or set package in .nolook.toml")
        comp = self._shell(["cmd", "package", "resolve-activity", "--brief", pkg]).strip().splitlines()[-1]
        if "/" not in comp:
            raise NolookError(f"{pkg} has no launcher activity")
        if fresh:
            self._shell(["am", "force-stop", pkg])
        ns = self.nodes()
        return self._act(ns, None, lambda: self._shell(["am", "start", "-n", comp]), settle)

    # Pixels -----------------------------------------------------------------

    def shot(self, q: str | None = None, region: tuple[int, int, int, int] | None = None, width: int | None = None, full: bool = False, out: str | None = None) -> dict:
        img = self.u2.screenshot()
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
        ext = "full.png" if full else "jpg"
        slug = re.sub(r"[^\w-]+", "-", label)[:24].strip("-") or "shot"
        path = Path(out) if out else shots / f"{time.strftime('%H%M%S')}-{slug}.{ext}"
        if full:
            img.save(path, "PNG")
        else:
            img.convert("RGB").save(path, "JPEG", quality=72)
        return {
            "path": str(path),
            "size": img.size,
            "kb": round(path.stat().st_size / 1024),
            "tokens": round(img.width * img.height / 750),
        }

    # Logs -------------------------------------------------------------------

    def log(self, lines: int = 40, grep: str | None = None, level: str | None = None) -> list[str]:
        cmd = ["logcat", "-d", "-t", str(max(lines * 20, 500) if grep else lines)]
        pid = self._pid()
        if pid:
            cmd.append(f"--pid={pid}")
        if level:
            cmd.append(f"*:{level.upper()}")
        out = self._shell(cmd).splitlines()
        if grep:
            rx = re.compile(grep)
            out = [ln for ln in out if rx.search(ln)]
        return out[-lines:]

    def _guard(self, since: str | None) -> list[str]:
        if not self.cfg.guard or since is None:
            return []
        out = self._shell(["logcat", "-d", "-T", since])
        rx = re.compile("|".join(f"(?:{g})" for g in self.cfg.guard))
        return [ln for ln in out.splitlines() if rx.search(ln)][:10]

    def _device_time(self) -> str | None:
        if not self.cfg.guard:
            return None
        # logcat -T takes epoch seconds; a second of slack beats a missed line.
        return f"{int(self._shell(['date', '+%s']).strip()) - 1}.000"

    def _pid(self) -> str | None:
        if not self.cfg.package:
            return None
        return self._shell(["pidof", self.cfg.package]).strip().split(" ")[0] or None

    def _shell(self, cmd: list[str], timeout: int = 60) -> str:
        # The connection to the device server drops now and then; one retry covers it.
        try:
            return self.u2.shell(cmd, timeout=timeout).output
        except (ConnectionError, OSError):
            time.sleep(0.5)
            return self.u2.shell(cmd, timeout=timeout).output


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
