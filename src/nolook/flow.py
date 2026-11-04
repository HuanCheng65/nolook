"""Flows: several steps, with branches, in one call.

A flow is a Python file run with `d` (a Flow) and `args` in scope:

    \"\"\"Open a note and check the editor.\"\"\"
    with d.dismissing("Rate us", tap="Not now"):
        d.launch(fresh=True)
        d.go("Notes", "Groceries")
        d("#toolbar").tap("Edit")
        assert d.sees("#body")
        d.shot("#toolbar")

Actions wait until their target can be acted on, so a flow rarely needs an
explicit wait. Each step prints one line. On failure the flow stops and prints
the step, the flow's line, the screen at that moment and a small screenshot's
path.
"""

from __future__ import annotations

import ast
import contextlib
import linecache
import sys
import time
import traceback
from pathlib import Path

from . import tree
from .config import Config
from .device import Device, NolookError, Outcome
from .tree import Ambiguous, Node, NoMatch, QueryError

DEFAULT_TIMEOUT = 5.0


class FlowFailed(Exception):
    pass


class Step:
    """What one action did: its target, and what appeared and disappeared.

    Attributes it does not have are the target's, so `d.tap(q).ref` still works.
    """

    def __init__(self, o: Outcome):
        self.target = o.target
        self.before = o.before
        self.after = o.after
        self.settled = o.settled
        self.guard_hits = o.guard_hits
        _, self.changed = tree.delta(o.before, o.after)
        before, after = tree.listed(o.before), tree.listed(o.after)
        old = {n.ref for n in before}
        new = {n.ref for n in after}
        self.added = [n for n in after if n.ref not in old]
        self.removed = [n for n in before if n.ref not in new]

    def shows(self, q: str) -> bool:
        """Whether the screen after the action has an element matching q."""
        return bool(tree.match(self.after, q))

    def __getattr__(self, name: str):
        target = self.__dict__.get("target")
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)


class Locator:
    """An element described by a query, found again each time it is used.

    `d("#sheet")` is one; `d("#sheet")("OK")` or `d("#sheet").tap("OK")` looks
    only inside it. `.first()`, `[i]` and `.all()` pick among several matches.
    """

    def __init__(self, flow: Flow, q: str, scope: Locator | None = None, nth: int | None = None):
        self._flow, self.q, self.scope, self.nth = flow, q, scope, nth

    def __repr__(self) -> str:
        pick = f"[{self.nth}]" if self.nth is not None else ""
        return (f"{self.scope!r} › " if self.scope else "") + f"{self.q!r}{pick}"

    # Finding

    def __call__(self, q: str) -> Locator:
        return Locator(self._flow, q, scope=self)

    def first(self) -> Locator:
        return Locator(self._flow, self.q, self.scope, 0)

    def __getitem__(self, i: int) -> Locator:
        return Locator(self._flow, self.q, self.scope, i)

    def all(self) -> list[Locator]:
        """One locator per current match, each pinned to that element's ref."""
        return [Locator(self._flow, n.ref, self.scope) for n in self._matches(self._flow._d.nodes())]

    def count(self) -> int:
        return len(self._matches(self._flow._d.nodes()))

    def exists(self) -> bool:
        try:
            self._resolve(self._flow._d.nodes())
            return True
        except QueryError:
            return False

    def _pool(self, ns: list[Node]) -> list[Node]:
        if self.scope is None:
            return ns
        return tree.within(ns, self.scope._resolve(ns))

    def _matches(self, ns: list[Node]) -> list[Node]:
        try:
            return tree.match(self._pool(ns), self.q)
        except NoMatch:
            return []

    def _resolve(self, ns: list[Node]) -> Node:
        pool = self._pool(ns)
        if self.nth is None:
            return tree.resolve(pool, self.q)
        hits = tree.match(pool, self.q)
        if self.nth >= len(hits):
            raise NoMatch(f"{self!r}: {len(hits)} match, no #{self.nth}")
        return hits[self.nth]

    # Reading

    @property
    def node(self) -> Node:
        n, _ = self._flow._locate(self)
        return n

    @property
    def text(self) -> str:
        n = self.node
        return n.text or n.desc or n.label

    @property
    def label(self) -> str:
        return self.node.label

    # Acting

    def tap(self, q: str | None = None, confirm: bool = False, force: bool = False, timeout: float | None = None) -> Step:
        if q is not None:
            return self(q).tap(confirm=confirm, force=force, timeout=timeout)
        n, _ = self._flow._locate(self, timeout, uncovered=not force)
        waited = self._flow._waited
        return self._flow._step(f"tap {self!r}", self._flow._d.tap(n.ref, force=force, confirm=confirm), waited)

    def long_tap(self, confirm: bool = False, timeout: float | None = None) -> Step:
        n, _ = self._flow._locate(self, timeout)
        waited = self._flow._waited
        return self._flow._step(f"long-press {self!r}", self._flow._d.tap(n.ref, long=True, confirm=confirm), waited)

    def type(self, text: str, append: bool = False, timeout: float | None = None, enter: bool = False) -> Step:
        n, _ = self._flow._locate(self, timeout)
        waited = self._flow._waited
        step = self._flow._step(f"type {text[:20]!r} into {self!r}", self._flow._d.type(text, n.ref, append=append), waited)
        return self._flow.key("enter") if enter else step

    def scroll(self, direction: str = "down", until: str | None = None, max_swipes: int = 10) -> Step:
        n, _ = self._flow._locate(self)
        verb = f"scroll {self!r} {direction}" + (f" until {until!r}" if until else "")
        return self._flow._step(verb, self._flow._d.scroll(direction, n.ref, until, max_swipes))

    def fling(self, times: int = 1, speed: float = 10000, gap: float = 0.8) -> None:
        """Fling this list forward and back `times` times, as perf frames does."""
        from . import perf

        n, _ = self._flow._locate(self)
        g = perf.swipes(self._flow._d, times, n.ref, None, gap, speed=speed)
        self._flow._log(f"fling {self!r} ×{times}", extra=f"({g['swipe_px']}px in {g['swipe_ms']}ms)")

    def wait(self, timeout: float | None = None) -> Locator:
        n, _ = self._flow._locate(self, timeout, uncovered=False)
        self._flow._log(f"wait {self!r}", extra=f"→ {n.ref} ({self._flow._waited:.1f}s)")
        return self

    def gone(self, timeout: float | None = None) -> None:
        flow = self._flow
        timeout = flow.timeout if timeout is None else timeout
        start = time.monotonic()
        while self.exists():
            if time.monotonic() - start > timeout:
                raise NolookError(f"{self!r} still there after {timeout:g}s")
            time.sleep(0.15)
        flow._log(f"gone {self!r}", extra=f"({round(time.monotonic() - start, 2)}s)")

    def shot(self, width: int | None = None, full: bool = False) -> str:
        n, _ = self._flow._locate(self, uncovered=False)
        return self._flow.shot(n.ref, width=width, full=full)


class Measure:
    """`with d.measure("label") as m:`; m.result holds the frame summary afterwards."""

    def __init__(self, flow: Flow, label: str | None, allow_debuggable: bool = False):
        self._flow = flow
        self.label = label
        self.allow_debuggable = allow_debuggable
        self.result: dict = {}
        self.debuggable = False

    def __enter__(self) -> Measure:
        from . import perf

        d = self._flow._d
        self.debuggable = perf.check_build(d, perf._pkg(d, None), self.allow_debuggable)
        self._busy = d.busy("measure")
        self._busy.__enter__()
        perf.reset(d)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._busy.__exit__(None, None, None)
        if exc_type is not None:
            return
        from . import perf

        rec = {"label": self.label or "", "debuggable": self.debuggable, "nolook": perf._version(), **perf.read(self._flow._d)}
        self._flow._d._record_perf(rec)
        self.result = rec
        self._flow._log(f"measure {self.label or ''}".strip(), extra="→ " + perf.format_record(rec))


class Flow:
    def __init__(self, d: Device):
        self._d = d
        self.steps = 0
        self.guard_hits: list[str] = []
        self.results: dict = {}
        self.timeout = DEFAULT_TIMEOUT
        # A wait longer than this is flagged: auto-waiting must not hide a page that got slower.
        self.slow_wait = 2.0
        self.slow_waits: list[tuple[str, float]] = []
        self._waited = 0.0
        self._dismiss: list[tuple[str, str | None]] = []

    # Output -----------------------------------------------------------------

    def _log(self, verb: str, o: Outcome | None = None, extra: str = "") -> None:
        self.steps += 1
        line = f"  ✓ {verb}"
        if o is not None and o.target is not None:
            t = o.target
            line += f" → {t.ref} {t.role}" + (f' "{t.label[:30]}"' if t.label else "")
        if o is not None:
            line += f" ({o.settled}s)" if o.settled is not None else " (unsettled)"
            for g in o.guard_hits:
                self.guard_hits.append(g)
                line += f"\n  ⚠ guard: {g}"
        if extra:
            line += f" {extra}"
        print(line)

    def _step(self, verb: str, o: Outcome, waited: float = 0.0) -> Step:
        extra = ""
        if waited >= min(0.5, self.slow_wait):
            extra = f"· waited {waited:.1f}s for it"
            if waited >= self.slow_wait:
                extra += " ⚠ slow"
                self.slow_waits.append((verb, waited))
        self._log(verb, o, extra)
        return Step(self._watch(o))

    # Finding, with waiting ---------------------------------------------------

    def __call__(self, q: str) -> Locator:
        """A locator: `d("#sheet").tap("OK")`, `d("btn:~Reply").first().tap()`."""
        return Locator(self, q)

    def _locate(self, loc: Locator, timeout: float | None = None, uncovered: bool = True) -> tuple[Node, list[Node]]:
        # Wait until the target is there, once, and not under something; an
        # ambiguous query fails at once, since waiting will not settle it.
        timeout = self.timeout if timeout is None else timeout
        start = time.monotonic()
        while True:
            ns = self._d.nodes()
            try:
                n = loc._resolve(ns)
                if not (uncovered and n.covered):
                    self._waited = time.monotonic() - start
                    return n, ns
                why = f"{tree.format_node(n, compact=True)} is covered"
            except Ambiguous:
                raise
            except NoMatch as e:
                why = str(e)
            if self._watch_once(ns) is not None:
                continue
            if time.monotonic() - start > timeout:
                raise NoMatch(self._d.explain(f"after {timeout:g}s: {why}"))
            time.sleep(0.15)

    # Interruptions ------------------------------------------------------------

    def dismiss_when(self, q: str, tap: str | None = None) -> None:
        """For the rest of the flow: if q shows after an action or while waiting, tap `tap` (default q)."""
        self._dismiss.append((q, tap))

    @contextlib.contextmanager
    def dismissing(self, q: str, tap: str | None = None):
        """`dismiss_when` for the block only."""
        self._dismiss.append((q, tap))
        try:
            yield
        finally:
            self._dismiss.remove((q, tap))

    def _watch(self, o: Outcome) -> Outcome:
        # A dialog may stack on another; three rounds is plenty and stops a loop.
        for _ in range(3):
            hit = self._watch_once(o.after)
            if hit is None:
                return o
            o = Outcome(o.target, o.before, hit.after, hit.settled, o.guard_hits + hit.guard_hits)
        return o

    def _watch_once(self, ns: list[Node]) -> Outcome | None:
        for q, tap in self._dismiss:
            if not [n for n in tree.match(ns, q) if not n.covered]:
                continue
            target = tap or q
            hit = self._d.tap(target)
            print(f"  · dismissed {q!r} by tapping {target!r}")
            return hit
        return None

    # Actions: the CLI's, by query. Each waits for its target and returns a Step.

    def tap(self, q: str, nth: int | None = None, force: bool = False, confirm: bool = False, timeout: float | None = None) -> Step:
        return Locator(self, q, nth=None if nth is None else nth - 1).tap(confirm=confirm, force=force, timeout=timeout)

    def long_tap(self, q: str, nth: int | None = None, confirm: bool = False) -> Step:
        return Locator(self, q, nth=None if nth is None else nth - 1).long_tap(confirm=confirm)

    def go(self, *labels: str) -> Step:
        """Tap each in turn, waiting for each: `d.go("Settings", "Display", "Dark theme")`."""
        step = None
        for q in labels:
            step = self.tap(q)
        if step is None:
            raise FlowFailed("go() needs at least one place")
        return step

    def tap_xy(self, x: int, y: int) -> Step:
        return self._step(f"tap ({x},{y})", self._d.tap_xy(x, y))

    def type(self, text: str, into: str | None = None, append: bool = False, enter: bool = False) -> Step:
        if into is not None:
            return Locator(self, into).type(text, append=append, enter=enter)
        step = self._step(f"type {text[:20]!r}", self._d.type(text, None, append=append))
        return self.key("enter") if enter else step

    def key(self, name: str) -> Step:
        return self._step(f"key {name}", self._d.key(name))

    def back(self) -> Step:
        return self.key("back")

    def scroll(self, direction: str = "down", within: str | None = None, until: str | None = None, max_swipes: int = 10) -> Step:
        if within is not None:
            return Locator(self, within).scroll(direction, until, max_swipes)
        return self._step(f"scroll {direction}" + (f" until {until!r}" if until else ""), self._d.scroll(direction, None, until, max_swipes))

    def fling(self, times: int = 1, within: str | None = None, speed: float = 10000) -> None:
        from . import perf

        g = perf.swipes(self._d, times, within, None, 0.8, speed=speed)
        self._log(f"fling ×{times}", extra=f"({g['swipe_px']}px in {g['swipe_ms']}ms)")

    def open(self, uri: str) -> Step:
        return self._step(f"open {uri}", self._d.open(uri))

    def launch(self, package: str | None = None, fresh: bool = False) -> Step:
        return self._step("launch" + (" fresh" if fresh else ""), self._d.launch(package, fresh))

    def wait(self, q: str, timeout: float = 10.0) -> Node:
        n, _ = self._locate(Locator(self, q), timeout, uncovered=False)
        self._log(f"wait {q!r}", extra=f"→ {n.ref} ({self._waited:.1f}s)")
        return n

    def gone(self, q: str, timeout: float = 10.0) -> None:
        Locator(self, q).gone(timeout)

    def shot(self, q: str | None = None, region: tuple[int, int, int, int] | None = None, width: int | None = None, full: bool = False) -> str:
        r = self._d.shot(q, region, width, full)
        self._log(f"shot {q or ('region' if region else 'screen')}", extra=f"→ {r['path']} {r['size'][0]}x{r['size'][1]} ≈{r['tokens']} tok")
        return r["path"]

    # Measuring -------------------------------------------------------------------

    def frames(self, label: str | None = None, rounds: int = 4, within: str | None = None, speed: float = 10000,
               duration: float | None = None, gap: float = 0.8, axis: str = "y", trace: str | None = None,
               runs: int = 3, allow_debuggable: bool = False, warmup: int = 1) -> dict:
        """perf frames: `runs` times, fling the list back and forth; median and range of the frame timing."""
        from . import perf

        rec = perf.frames(self._d, rounds, within, duration, gap, axis, label, trace, speed=speed,
                          runs=runs, allow_debuggable=allow_debuggable, warmup=warmup)
        self._log(f"frames {label or ''}".strip(), extra="→ " + perf.format_record(rec))
        return rec

    def measure(self, label: str | None = None, allow_debuggable: bool = False) -> Measure:
        """Frame timing of whatever runs inside the `with` block (one run: repeat the block for a spread)."""
        return Measure(self, label, allow_debuggable)

    def motion(self, tap: str | None = None, key: str | None = None, after: str | None = None, el: str | None = None,
               region: tuple[int, int, int, int] | None = None, seconds: float = 1.5, slow: float | None = None,
               label: str | None = None, confirm: bool = False) -> dict:
        """nolook motion: record around a trigger and measure the animation."""
        from . import motion

        r = motion.record(self._d, tap, after, key, el, region, seconds, slow, label, confirm)
        r = {k: v for k, v in r.items() if not k.startswith("_")}
        if r.get("moved"):
            summary = f"{r['duration_ms']}ms · 90% at {r['t90_ms']}ms · {r['shape']} · " + ("monotonic" if r["monotonic"] else "reverses")
        else:
            summary = "no motion"
        self._log(f"motion {label or tap or key or after or ''}".strip(), extra=f"→ {summary} · sheet {r.get('sheet')}")
        return r

    @contextlib.contextmanager
    def slow(self, factor: float):
        """Animations `factor` times slower inside the block; restored after."""
        old = self._d.android.shell(["settings", "get", "global", "animator_duration_scale"]).strip()
        self._d.android.shell(["settings", "put", "global", "animator_duration_scale", str(factor)])
        self._log(f"slow ×{factor:g}")
        try:
            yield
        finally:
            self._d.android.shell(["settings", "put", "global", "animator_duration_scale", old if old not in ("", "null") else "1"])

    def compile(self, mode: str) -> str:
        """Set the ART compile mode (speed, verify, …) and stop the app; launch it again before measuring."""
        from . import perf

        status = perf.compile(self._d, mode)
        self._log(f"compile {mode}", extra=f"→ {status or 'unknown'}")
        return status

    # Reads: no output line, for branching and assert.

    def sees(self, q: str) -> bool:
        """Whether q is on screen now and not covered."""
        return any(not n.covered for n in tree.match(self._d.nodes(), q))

    def exists(self, q: str) -> bool:
        return self._d.exists(q)

    def find(self, q: str) -> list[Node]:
        return tree.match(self._d.nodes(), q)

    def text(self, q: str) -> str:
        return Locator(self, q).text

    def snap(self) -> list[Node]:
        return tree.listed(self._d.nodes())

    def log(self, lines: int = 40, grep: str | None = None) -> list[str]:
        return self._d.log(lines, grep)

    # Control and output ----------------------------------------------------------

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def note(self, msg: str) -> None:
        print(f"  · {msg}")

    def result(self, **values) -> None:
        """Values to hand back: printed as one JSON line when the flow ends, also on failure."""
        self.results.update(values)

    def check(self, cond: bool, msg: str) -> None:
        if not cond:
            raise FlowFailed(msg)
        self._log(f"check {msg}")

    def fail(self, msg: str) -> None:
        raise FlowFailed(msg)


def resolve_flow(cfg: Config, name: str) -> Path:
    p = Path(name)
    if p.suffix == ".py" and p.is_file():
        return p
    cand = cfg.flows_dir / f"{name}.py"
    if cand.is_file():
        return cand
    raise NolookError(f"no flow {name!r} (looked for {p} and {cand}); `nolook flows` lists them")


def list_flows(cfg: Config) -> None:
    d = cfg.flows_dir
    files = sorted(d.glob("*.py")) if d.is_dir() else []
    if not files:
        print(f"no flows in {d}")
        return
    for f in files:
        doc = ast.get_docstring(ast.parse(f.read_text())) or ""
        print(f"{f.stem:<24} {doc.splitlines()[0] if doc else ''}")


def run_flow(cfg: Config, name: str, args: list[str]) -> int:
    path = resolve_flow(cfg, name)
    src = path.read_text()
    d = Device(cfg)
    flow = Flow(d)
    start = time.monotonic()
    print(f"flow {path.name}")
    # Flows import helpers from their own directory: `from common import sign_in`.
    sys.path.insert(0, str(path.resolve().parent))
    try:
        exec(compile(src, str(path), "exec"), {"d": flow, "args": args, "__name__": "__flow__"})
    except (NolookError, QueryError, FlowFailed, AssertionError) as e:
        line = _flow_line(e, path)
        msg = str(e)
        if isinstance(e, AssertionError) and not msg and line:
            # A bare assert says nothing; its source line says what was expected.
            msg = "assert failed: " + linecache.getline(str(path), line).strip()
        print(f"  ✗ step {flow.steps + 1}" + (f" (line {line})" if line else "") + f": {msg}")
        d.journal({"verb": "flow", "query": path.stem, "error": msg.splitlines()[0][:200] if msg else "failed", "steps": flow.steps})
        _print_results(flow)
        _dump_failure(d)
        return 1
    except Exception:  # noqa: BLE001 - a bug in the flow itself
        print("  ✗ the flow raised:")
        d.journal({"verb": "flow", "query": path.stem, "error": "the flow raised an exception", "steps": flow.steps})
        traceback.print_exc(file=sys.stdout)
        _print_results(flow)
        return 1
    finally:
        sys.path.remove(str(path.resolve().parent))
    took = round(time.monotonic() - start, 1)
    warn = f" · ⚠ {len(flow.guard_hits)} guard hits" if flow.guard_hits else ""
    if flow.slow_waits:
        verb, worst = max(flow.slow_waits, key=lambda x: x[1])
        warn += f" · ⚠ {len(flow.slow_waits)} steps waited over {flow.slow_wait:g}s (longest {worst:.1f}s: {verb})"
    print(f"done · {flow.steps} steps · {took}s{warn}")
    d.journal({"verb": "flow", "query": path.stem, "steps": flow.steps, "took": took, "guard": flow.guard_hits[:3] or None})
    _print_results(flow)
    return 0


def _print_results(flow: Flow) -> None:
    if flow.results:
        import json

        print("result " + json.dumps(flow.results, ensure_ascii=False, default=str))


def _flow_line(e: BaseException, path: Path) -> int | None:
    line = None
    for fr in traceback.extract_tb(e.__traceback__):
        if fr.filename == str(path):
            line = fr.lineno
    return line


def _dump_failure(d: Device) -> None:
    try:
        ns = tree.listed(d.nodes())
        print(f"screen at failure ({len(ns)} elements):")
        for n in ns[:40]:
            print("  " + tree.format_node(n))
        if len(ns) > 40:
            print(f"  … {len(ns) - 40} more")
        r = d.shot()
        print(f"screenshot (not read): {r['path']} ≈{r['tokens']} tokens")
    except Exception as e:  # noqa: BLE001
        print(f"(could not read the screen: {e})")
