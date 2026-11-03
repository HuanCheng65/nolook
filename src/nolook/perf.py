"""Frame timing, compile modes, traces and ANRs, summarised for an agent.

The usual loop is: open a page, `nolook perf frames --label A`, change one
thing, run it again with `--label B`, then `nolook perf compare`.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from pathlib import Path

from .device import Device, NolookError

# Keys printed in the summary, in order, with the gfxinfo line that holds them.
_FIELDS = [
    ("frames", r"Total frames rendered: (\d+)"),
    ("janky", r"Janky frames: \d+ \(([\d.]+)%\)"),
    ("janky_legacy", r"Janky frames \(legacy\): \d+ \(([\d.]+)%\)"),
    ("p50", r"^50th percentile: (\d+)ms"),
    ("p90", r"^90th percentile: (\d+)ms"),
    ("p95", r"^95th percentile: (\d+)ms"),
    ("p99", r"^99th percentile: (\d+)ms"),
    ("gpu_p50", r"^50th gpu percentile: (\d+)ms"),
    ("gpu_p90", r"^90th gpu percentile: (\d+)ms"),
    ("gpu_p99", r"^99th gpu percentile: (\d+)ms"),
    ("missed_vsync", r"Number Missed Vsync: (\d+)"),
    ("high_input_latency", r"Number High input latency: (\d+)"),
    ("slow_ui", r"Number Slow UI thread: (\d+)"),
    ("slow_bitmap", r"Number Slow bitmap uploads: (\d+)"),
    ("slow_draw", r"Number Slow issue draw commands: (\d+)"),
    ("deadline_missed", r"Number Frame deadline missed: (\d+)"),
]

SHORT = ["frames", "janky", "janky_legacy", "p50", "p90", "p99", "gpu_p90", "slow_ui", "deadline_missed"]

TRACE_CONFIG = """
buffers {{ size_kb: 65536 fill_policy: RING_BUFFER }}
buffers {{ size_kb: 4096 fill_policy: RING_BUFFER }}
data_sources {{ config {{ name: "linux.ftrace" target_buffer: 0 ftrace_config {{
  ftrace_events: "sched/sched_switch" ftrace_events: "sched/sched_wakeup"
  ftrace_events: "power/cpu_frequency" ftrace_events: "power/suspend_resume"
  atrace_categories: "gfx" atrace_categories: "view" atrace_categories: "input"
  atrace_categories: "am" atrace_categories: "wm" atrace_categories: "dalvik"
  atrace_categories: "binder_driver" atrace_categories: "freq" atrace_categories: "sched"
  atrace_apps: "{pkg}"
}} }} }}
data_sources {{ config {{ name: "android.surfaceflinger.frametimeline" target_buffer: 1 }} }}
data_sources {{ config {{ name: "linux.process_stats" target_buffer: 1
  process_stats_config {{ scan_all_processes_on_start: true }} }} }}
duration_ms: {duration_ms}
write_into_file: true
file_write_period_ms: 1000
"""


def _pkg(d: Device, package: str | None) -> str:
    pkg = package or d.cfg.package
    if not pkg:
        raise NolookError("no package: pass --package, or set package in .nolook.toml")
    return pkg


def parse_gfxinfo(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, rx in _FIELDS:
        m = re.search(rx, text, re.M)
        if m:
            v = float(m.group(1))
            out[key] = int(v) if v.is_integer() and key != "janky" and key != "janky_legacy" else v
    return out


def reset(d: Device, package: str | None = None) -> None:
    d._shell(["dumpsys", "gfxinfo", _pkg(d, package), "reset"])


def read(d: Device, package: str | None = None) -> dict[str, float]:
    stats = parse_gfxinfo(d._shell(["dumpsys", "gfxinfo", _pkg(d, package)]))
    if not stats:
        raise NolookError("gfxinfo has no frame data: is the app running and in front?")
    return stats


def swipes(d: Device, rounds: int, within: str | None, duration: float | None, gap: float, axis: str = "y", speed: float = 10000) -> dict:
    """Each round: one swipe forward, one back. Returns the gesture used.

    Swipes go through `input swipe`, which times the gesture on the device.
    uiautomator's own swipe paces its steps from the host and ran up to nine
    times slower than asked on an emulator, so it could not fling.
    """
    ns = d.nodes()
    box = d._scroll_box(ns, within)
    l, t, r, b = box.bounds if box else (0, 0, *d.size)
    cx, cy = (l + r) // 2, (t + b) // 2
    if axis == "y":
        half = (b - t) * 0.3
        fwd = (cx, cy + half, cx, cy - half)
    else:
        half = (r - l) * 0.3
        fwd = (cx + half, cy, cx - half, cy)
    back = (fwd[2], fwd[3], fwd[0], fwd[1])
    dist = 2 * half
    ms = max(16, round((duration if duration else dist / speed) * 1000))
    n = 0
    for _ in range(rounds):
        for x1, y1, x2, y2 in (fwd, back):
            d._shell(["input", "swipe", *(str(round(v)) for v in (x1, y1, x2, y2)), str(ms)])
            n += 1
            time.sleep(gap)
    return {"swipes": n, "swipe_px": round(dist), "swipe_ms": ms, "speed_px_s": round(dist * 1000 / ms)}


def frames(
    d: Device,
    rounds: int = 4,
    within: str | None = None,
    duration: float | None = None,
    gap: float = 0.8,
    axis: str = "y",
    label: str | None = None,
    trace: str | None = None,
    package: str | None = None,
    speed: float = 10000,
) -> dict:
    pkg = _pkg(d, package)
    est_ms = int(rounds * 2 * ((duration or 0.3) + gap) * 1000) + 3000
    tracer = _start_trace(d, pkg, est_ms) if trace else None
    reset(d, pkg)
    gesture = swipes(d, rounds, within, duration, gap, axis, speed)
    time.sleep(0.5)
    stats = read(d, pkg)
    rec = {
        "label": label or "",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "serial": d.serial,
        "package": pkg,
        **gesture,
        "compile": compile_status(d, pkg),
        **stats,
    }
    if tracer:
        rec["trace"] = _stop_trace(d, tracer, trace)
    with (d.dir / "perf.jsonl").open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def format_record(rec: dict, keys: list[str] = SHORT) -> str:
    def fmt(k: str) -> str:
        v = rec.get(k)
        if v is None:
            return f"{k} -"
        if k in ("janky", "janky_legacy"):
            return f"{k} {v}%"
        if k.startswith("p") or k.startswith("gpu_"):
            return f"{k} {v}ms"
        return f"{k} {v}"

    head = f"[{rec['label']}] " if rec.get("label") else ""
    tail = f" · compile {rec['compile']}" if rec.get("compile") else ""
    if rec.get("swipe_ms"):
        tail += f" · swipes {rec['swipe_px']}px in {rec['swipe_ms']}ms ({rec['speed_px_s']}px/s)"
    return head + " · ".join(fmt(k) for k in keys) + tail


def history(d: Device, labels: list[str] | None = None, last: int = 10) -> list[dict]:
    f = d.dir / "perf.jsonl"
    if not f.is_file():
        return []
    recs = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
    if labels:
        recs = [r for r in recs if r.get("label") in labels]
    return recs[-last:]


def compare_table(recs: list[dict], keys: list[str] = SHORT) -> list[str]:
    if not recs:
        return ["no runs recorded yet (nolook perf frames --label …)"]
    cols = ["label"] + keys + ["compile", "speed_px_s"]
    rows = [[str(r.get(c, "")) if r.get(c) is not None else "-" for c in cols] for r in recs]
    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]
    line = lambda cells: "  ".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths)))  # noqa: E731
    return [line(cols)] + [line(r) for r in rows]


# Compile modes ----------------------------------------------------------------


def compile_status(d: Device, pkg: str) -> str:
    out = d._shell(["dumpsys", "package", "dexopt"])
    # The block for this package lists "[status=speed-profile] [reason=…]".
    i = out.find(f"[{pkg}]")
    if i < 0:
        return ""
    m = re.search(r"status=([\w-]+)", out[i : i + 2000])
    return m.group(1) if m else ""


def compile(d: Device, mode: str, package: str | None = None) -> str:
    pkg = _pkg(d, package)
    if mode == "reset":
        d._shell(["cmd", "package", "compile", "--reset", pkg])
    else:
        d._shell(["cmd", "package", "compile", "-m", mode, "-f", pkg])
    # Compiled code is picked up by the next process.
    d._shell(["am", "force-stop", pkg])
    return compile_status(d, pkg)


# Traces -----------------------------------------------------------------------

_REMOTE_DIR = "/data/misc/perfetto-traces"


def _start_trace(d: Device, pkg: str, duration_ms: int) -> dict:
    remote = f"{_REMOTE_DIR}/nolook-{int(time.time())}.pftrace"
    cfg = TRACE_CONFIG.format(pkg=pkg, duration_ms=duration_ms)
    d._shell(["sh", "-c", f"cat > /data/local/tmp/nolook-trace.cfg << 'EOF'\n{cfg}\nEOF"])
    # Configs are read from stdin: perfetto may not open files under /data/local/tmp.
    out = d._shell(["sh", "-c", f"cat /data/local/tmp/nolook-trace.cfg | perfetto --txt -c - -o {shlex.quote(remote)} --background"])
    pid = next((tok for tok in out.split() if tok.isdigit()), None)
    if pid is None:
        raise NolookError(f"perfetto did not start: {out.strip()[:300]}")
    time.sleep(1.0)  # let the data sources come up before the first swipe
    return {"pid": pid, "remote": remote}


def _stop_trace(d: Device, tracer: dict, local: str) -> str:
    d._shell(["kill", "-TERM", tracer["pid"]])
    for _ in range(50):
        if d._shell(["sh", "-c", f"kill -0 {tracer['pid']} 2>/dev/null && echo alive"]).strip() != "alive":
            break
        time.sleep(0.2)
    path = Path(local)
    if path.is_dir() or local.endswith("/"):
        path = path / Path(tracer["remote"]).name
    path.parent.mkdir(parents=True, exist_ok=True)
    d.u2.pull(tracer["remote"], str(path))
    d._shell(["rm", "-f", tracer["remote"]])
    return str(path)


# ANRs -------------------------------------------------------------------------


def last_anr(d: Device, package: str | None = None, lines: int = 40) -> list[str]:
    pkg = _pkg(d, package)
    out = d._shell(["dumpsys", "dropbox", "--print", "data_app_anr"])
    entries = re.split(r"\n(?=========================================\n\d{4}-)", out)
    mine = [e for e in entries if f"Package: {pkg}" in e or f"Process: {pkg}" in e]
    if not mine:
        return [f"no ANR recorded for {pkg}"]
    e = mine[-1].splitlines()
    head = [ln for ln in e[:15] if ln.startswith(("Process:", "Subject:", "Package:")) or re.match(r"=+|\d{4}-", ln)]
    # The main thread's stack is what explains an ANR; print that, not every thread.
    try:
        i = next(k for k, ln in enumerate(e) if ln.startswith('"main"'))
    except StopIteration:
        return head + e[15 : 15 + lines]
    stack = []
    for ln in e[i:]:
        if not ln.strip() and stack:
            break
        stack.append(ln)
    return head + stack[:lines]
