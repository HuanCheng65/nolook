"""A live view of the device for the Claude Code pane: frames and the action journal, as JSON lines.

`nolook screen --stream` keeps one connection open and prints, one JSON
object per line:

  {"device": serial, "size": [w, h]}            on connecting
  {"app": package}                             when the app in front changes
  {"frame": path, "w": .., "h": .., "v": n}    a new PNG; paths alternate
  {"action": {...}}                            each new line of actions.jsonl
  {"busy": "perf frames" | null}               a measurement holds it off the device
  {"error": text}                              it retries every few seconds

A frame is written only when the screen or the marker over the last tapped
element changed. Polling slows down while nothing moves. The control file
the pane writes holds {"live": bool, "refresh": n}: paused, it captures only
after an action or a refresh.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .config import Config, follow_session
from .device import Device, NolookError

MARK_FOR = 2.0  # seconds the last tapped element stays outlined
MARK_COLOR = (255, 140, 0)
SLOWEST = 1.0  # seconds between polls once nothing moves


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class Journal:
    """New lines of actions.jsonl since the last read."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.pos = path.stat().st_size
        except OSError:
            self.pos = 0

    def new(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.pos:  # trimmed
            self.pos = 0
        if size == self.pos:
            return []
        with self.path.open() as f:
            f.seek(self.pos)
            chunk = f.read()
        # Leave a line still being written for the next read.
        end = chunk.rfind("\n") + 1
        self.pos += len(chunk[:end].encode())
        out = []
        for ln in chunk[:end].splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out


def busy(dirpath: Path) -> str | None:
    """What measurement holds the device, if its process is still alive."""
    try:
        rec = json.loads((dirpath / "busy").read_text())
        os.kill(int(rec["pid"]), 0)
        return str(rec.get("what") or "measuring")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def mark_box(action: dict | None, now: float) -> list[int] | None:
    if not action or now - action.get("at", 0) > MARK_FOR or action.get("error"):
        return None
    if action.get("verb") not in ("tap", "long-press", "type"):
        return None
    t = action.get("target")
    if t and t.get("bounds"):
        return list(t["bounds"])
    p = action.get("point")
    if p:
        return [p[0] - 30, p[1] - 30, p[0] + 30, p[1] + 30]
    return None


def thumb(img) -> bytes:
    return img.convert("L").resize((54, 120)).tobytes()


def compose(img, box: list[int] | None, scale: float):
    if box is None:
        return img
    from PIL import ImageDraw

    out = img.copy()
    l, t, r, b = (round(v * scale) for v in box)
    ImageDraw.Draw(out).rounded_rectangle((l, t, r, b), radius=6, outline=MARK_COLOR, width=3)
    return out


def read_control(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def stream(cfg: Config, session: str | None, width: int = 360, fps: float = 5.0, control: str | None = None) -> int:
    # This process only watches: it must not become the device the session drove last.
    os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    ctl_path = Path(control) if control else None
    fastest = 1 / max(fps, 0.5)
    while True:
        try:
            if session:
                follow_session(cfg, session)
            d = Device(cfg)
        except NolookError as e:
            emit({"error": str(e).splitlines()[0]})
            time.sleep(3)
            continue
        try:
            _watch(d, cfg, session, width, fastest, ctl_path)
        except BrokenPipeError:
            return 0
        except Exception as e:  # noqa: BLE001 - the device went away; connect again
            emit({"error": f"{type(e).__name__}: {e}".splitlines()[0][:200]})
            time.sleep(2)


def _watch(d: Device, cfg: Config, session: str | None, width: int, fastest: float, ctl_path: Path | None) -> None:
    emit({"device": d.serial, "size": list(d.size)})
    # Each pane its own frames: two sessions may mirror one device.
    out_dir = d.dir / "pane" / re.sub(r"[^\w-]", "_", session or str(os.getpid()))
    out_dir.mkdir(parents=True, exist_ok=True)
    journal = Journal(d.dir / "actions.jsonl")
    last_action: dict | None = None
    prev_thumb: bytes | None = None
    prev_box: list[int] | None = None
    v = 0
    interval = fastest
    app, app_at = None, 0.0
    held: str | None = None
    refresh_seen = read_control(ctl_path).get("refresh", 0)
    pending = True  # capture once on start
    serial_at = time.monotonic()
    scale = width / d.size[0]

    while True:
        now = time.time()
        for a in journal.new():
            emit({"action": a})
            last_action = a
            pending = True
        ctl = read_control(ctl_path)
        live = ctl.get("live", True)
        if ctl.get("refresh", 0) != refresh_seen:
            refresh_seen = ctl.get("refresh", 0)
            pending = True

        what = busy(d.dir)
        if what != held:
            held = what
            emit({"busy": what})
        if what:
            time.sleep(0.5)
            continue

        if time.monotonic() - app_at > 3:
            app_at = time.monotonic()
            cur = d.android.u2.app_current().get("package")
            if cur != app:
                app = cur
                emit({"app": app})

        if session and time.monotonic() - serial_at > 3:
            serial_at = time.monotonic()
            before = cfg.serial
            follow_session(cfg, session)
            if cfg.serial and cfg.serial != before and cfg.serial != d.serial:
                return  # the session moved to another device: connect to it

        box = mark_box(last_action, now)
        if live or pending or box != prev_box:
            img = d.backend.screenshot()
            img = img.resize((width, round(img.height * scale)))
            th = thumb(img)
            if th != prev_thumb or box != prev_box or pending:
                v += 1
                path = out_dir / f"frame-{v % 2}.png"
                compose(img, box, scale).save(path, "PNG", compress_level=1)
                emit({"frame": str(path), "w": img.width, "h": img.height, "v": v, "at": round(now, 2)})
                interval = fastest if (th != prev_thumb or pending) else interval
            else:
                interval = min(SLOWEST, interval * 1.5)
            prev_thumb, prev_box, pending = th, box, False
        time.sleep(interval if live else 0.25)
