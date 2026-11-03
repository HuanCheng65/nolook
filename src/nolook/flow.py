"""Flows: several steps, with branches, in one call.

A flow is a Python file run with `d` (a Flow) and `args` in scope:

    \"\"\"Open a note and check the editor.\"\"\"
    d.open("notes://note/42")
    d.wait("Groceries")
    if d.exists("Discard draft?"):
        d.tap("Keep")
    d.tap("#edit")
    d.shot("#toolbar")

Each step prints one line. On failure the flow stops and prints the step, the
screen at that moment and a small screenshot's path.
"""

from __future__ import annotations

import ast
import sys
import time
import traceback
from pathlib import Path

from . import tree
from .config import Config
from .device import Device, NolookError, Outcome
from .tree import Node, QueryError


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
        lines, self.changed = tree.delta(o.before, o.after)
        old = {n.ref for n in o.before}
        new = {n.ref for n in o.after}
        self.added = [n for n in o.after if n.ref not in old]
        self.removed = [n for n in o.before if n.ref not in new]

    def shows(self, q: str) -> bool:
        """Whether the screen after the action has an element matching q."""
        return bool(tree.match(self.after, q))

    def __getattr__(self, name: str):
        target = self.__dict__.get("target")
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)


class Measure:
    """`with d.measure("label") as m:`; m.result holds the frame summary afterwards."""

    def __init__(self, flow: "Flow", label: str | None):
        self._flow = flow
        self.label = label
        self.result: dict = {}

    def __enter__(self) -> "Measure":
        from . import perf

        perf.reset(self._flow._d)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            return
        from . import perf

        rec = {"label": self.label or "", **perf.read(self._flow._d)}
        self._flow._d._record_perf(rec)
        self.result = rec
        self._flow._log(f"measure {self.label or ''}".strip(), extra="→ " + perf.format_record(rec))


class Flow:
    def __init__(self, d: Device):
        self._d = d
        self.steps = 0
        self.guard_hits: list[str] = []
        self.results: dict = {}
        self._dismiss: list[tuple[str, str | None]] = []

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

    def _step(self, verb: str, o: Outcome) -> Step:
        self._log(verb, o)
        o = self._watch(o)
        return Step(o)

    # Interruptions ---------------------------------------------------------------

    def dismiss_when(self, q: str, tap: str | None = None) -> None:
        """After every action and while waiting: if q shows, tap `tap` (default q) to close it."""
        self._dismiss.append((q, tap))

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

    # Actions: same names and meanings as the CLI. Each returns a Step.

    def tap(self, q: str, nth: int | None = None, force: bool = False, confirm: bool = False) -> Step:
        return self._step(f"tap {q!r}", self._d.tap(q, nth, force=force, confirm=confirm))

    def long_tap(self, q: str, nth: int | None = None, confirm: bool = False) -> Step:
        return self._step(f"long-press {q!r}", self._d.tap(q, nth, long=True, confirm=confirm))

    def tap_xy(self, x: int, y: int) -> Step:
        return self._step(f"tap ({x},{y})", self._d.tap_xy(x, y))

    def type(self, text: str, into: str | None = None, append: bool = False) -> Step:
        return self._step(f"type {text[:20]!r}", self._d.type(text, into, append=append))

    def key(self, name: str) -> Step:
        return self._step(f"key {name}", self._d.key(name))

    def back(self) -> Step:
        return self.key("back")

    def scroll(self, direction: str = "down", within: str | None = None, until: str | None = None, max_swipes: int = 10) -> Step:
        return self._step(f"scroll {direction}" + (f" until {until!r}" if until else ""), self._d.scroll(direction, within, until, max_swipes))

    def open(self, uri: str) -> Step:
        return self._step(f"open {uri}", self._d.open(uri))

    def launch(self, package: str | None = None, fresh: bool = False) -> Step:
        return self._step("launch" + (" fresh" if fresh else ""), self._d.launch(package, fresh))

    def wait(self, q: str, timeout: float = 10.0) -> Node:
        ns, took = self._wait(q, gone=False, timeout=timeout)
        self._log(f"wait {q!r}", extra=f"({took}s)")
        return tree.match(ns, q)[0]

    def gone(self, q: str, timeout: float = 10.0) -> None:
        _, took = self._wait(q, gone=True, timeout=timeout)
        self._log(f"gone {q!r}", extra=f"({took}s)")

    def _wait(self, q: str, gone: bool, timeout: float) -> tuple[list[Node], float]:
        if not self._dismiss:
            return self._d.wait(q, gone=gone, timeout=timeout)
        # Poll here rather than in Device, so an interruption is closed on the way.
        start = time.monotonic()
        while True:
            ns = self._d.nodes()
            if bool(tree.match(ns, q)) != gone:
                return ns, round(time.monotonic() - start, 2)
            if self._watch_once(ns) is None and time.monotonic() - start > timeout:
                state = "still there" if gone else "never appeared"
                raise NolookError(f"{q!r} {state} after {timeout:g}s")
            time.sleep(0.15)

    def shot(self, q: str | None = None, region: tuple[int, int, int, int] | None = None, width: int | None = None, full: bool = False) -> str:
        r = self._d.shot(q, region, width, full)
        self._log(f"shot {q or ('region' if region else 'screen')}", extra=f"→ {r['path']} {r['size'][0]}x{r['size'][1]} ≈{r['tokens']} tok")
        return r["path"]

    # Measuring -------------------------------------------------------------------

    def frames(self, label: str | None = None, rounds: int = 4, within: str | None = None, speed: float = 10000,
               duration: float | None = None, gap: float = 0.8, axis: str = "y", trace: str | None = None) -> dict:
        """perf frames: fling the list back and forth and summarise frame timing."""
        from . import perf

        rec = perf.frames(self._d, rounds, within, duration, gap, axis, label, trace, speed=speed)
        self._log(f"frames {label or ''}".strip(), extra="→ " + perf.format_record(rec))
        return rec

    def measure(self, label: str | None = None) -> Measure:
        """Frame timing of whatever runs inside the `with` block."""
        return Measure(self, label)

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

    def compile(self, mode: str) -> str:
        """Set the ART compile mode (speed, verify, …) and stop the app; launch it again before measuring."""
        from . import perf

        status = perf.compile(self._d, mode)
        self._log(f"compile {mode}", extra=f"→ {status or 'unknown'}")
        return status

    # Reads: no output line, for branching.

    def exists(self, q: str) -> bool:
        return self._d.exists(q)

    def find(self, q: str) -> list[Node]:
        return tree.match(self._d.nodes(), q)

    def text(self, q: str) -> str:
        n, _ = self._d.find(q)
        return n.text or n.desc or n.label

    def snap(self) -> list[Node]:
        return self._d.nodes()

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
    try:
        exec(compile(src, str(path), "exec"), {"d": flow, "args": args, "__name__": "__flow__"})
    except (NolookError, QueryError, FlowFailed, AssertionError) as e:
        line = _flow_line(e, path)
        print(f"  ✗ step {flow.steps + 1}" + (f" (line {line})" if line else "") + f": {e}")
        _print_results(flow)
        _dump_failure(d)
        return 1
    except Exception:  # noqa: BLE001 - a bug in the flow itself
        print("  ✗ the flow raised:")
        traceback.print_exc(file=sys.stdout)
        _print_results(flow)
        return 1
    took = round(time.monotonic() - start, 1)
    warn = f" · ⚠ {len(flow.guard_hits)} guard hits" if flow.guard_hits else ""
    print(f"done · {flow.steps} steps · {took}s{warn}")
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
        ns = d.nodes()
        print(f"screen at failure ({len(ns)} elements):")
        for n in ns[:40]:
            print("  " + tree.format_node(n))
        if len(ns) > 40:
            print(f"  … {len(ns) - 40} more")
        r = d.shot()
        print(f"screenshot (not read): {r['path']} ≈{r['tokens']} tokens")
    except Exception as e:  # noqa: BLE001
        print(f"(could not read the screen: {e})")
