"""Flows: several steps, with branches, in one call.

A flow is a Python file run with `d` (a Flow) and `args` in scope:

    \"\"\"Open a thread and check the reply bar.\"\"\"
    d.open("oolong://thread/123")
    d.wait("回复")
    if d.exists("我知道了"):
        d.tap("我知道了")
    d.tap("#reply_button")
    d.shot("#composer")

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


class Flow:
    def __init__(self, d: Device):
        self._d = d
        self.steps = 0
        self.guard_hits: list[str] = []

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

    # Actions: same names and meanings as the CLI.

    def tap(self, q: str, nth: int | None = None, force: bool = False, confirm: bool = False) -> Node:
        o = self._d.tap(q, nth, force=force, confirm=confirm)
        self._log(f"tap {q!r}", o)
        return o.target  # type: ignore[return-value]

    def long_tap(self, q: str, nth: int | None = None) -> Node:
        o = self._d.tap(q, nth, long=True)
        self._log(f"long-press {q!r}", o)
        return o.target  # type: ignore[return-value]

    def tap_xy(self, x: int, y: int) -> None:
        self._log(f"tap ({x},{y})", self._d.tap_xy(x, y))

    def type(self, text: str, into: str | None = None, append: bool = False) -> None:
        self._log(f"type {text[:20]!r}", self._d.type(text, into, append=append))

    def key(self, name: str) -> None:
        self._log(f"key {name}", self._d.key(name))

    def back(self) -> None:
        self.key("back")

    def scroll(self, direction: str = "down", within: str | None = None, until: str | None = None, max_swipes: int = 10) -> Node | None:
        o = self._d.scroll(direction, within, until, max_swipes)
        self._log(f"scroll {direction}" + (f" until {until!r}" if until else ""), o)
        return o.target

    def open(self, uri: str) -> None:
        self._log(f"open {uri}", self._d.open(uri))

    def launch(self, package: str | None = None, fresh: bool = False) -> None:
        self._log("launch" + (" fresh" if fresh else ""), self._d.launch(package, fresh))

    def wait(self, q: str, timeout: float = 10.0) -> Node:
        ns, took = self._d.wait(q, timeout=timeout)
        self._log(f"wait {q!r}", extra=f"({took}s)")
        return tree.match(ns, q)[0]

    def gone(self, q: str, timeout: float = 10.0) -> None:
        _, took = self._d.wait(q, gone=True, timeout=timeout)
        self._log(f"gone {q!r}", extra=f"({took}s)")

    def shot(self, q: str | None = None, region: tuple[int, int, int, int] | None = None, width: int | None = None, full: bool = False) -> str:
        r = self._d.shot(q, region, width, full)
        self._log(f"shot {q or ('region' if region else 'screen')}", extra=f"→ {r['path']} {r['size'][0]}x{r['size'][1]} ≈{r['tokens']} tok")
        return r["path"]

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

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def note(self, msg: str) -> None:
        print(f"  · {msg}")

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
        _dump_failure(d)
        return 1
    except Exception:  # noqa: BLE001 - a bug in the flow itself
        print("  ✗ the flow raised:")
        traceback.print_exc(file=sys.stdout)
        return 1
    took = round(time.monotonic() - start, 1)
    warn = f" · ⚠ {len(flow.guard_hits)} guard hits" if flow.guard_hits else ""
    print(f"done · {flow.steps} steps · {took}s{warn}")
    return 0


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
