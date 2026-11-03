"""Frame timing, compile modes, traces and ANRs, summarised for an agent.

The usual loop is: open a page, `nolook perf frames --label A`, change one
thing, run it again with `--label B`, then `nolook perf compare`.
"""

from __future__ import annotations

import json
import math
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

# Counters `compare` judges each label on, against the first.
VERDICT = ["janky", "p90", "p99", "gpu_p90"]

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


def is_debuggable(d: Device, pkg: str) -> bool:
    out = d._shell(["dumpsys", "package", pkg])
    m = re.search(r"pkgFlags=\[([^\]]*)\]", out)
    return bool(m and "DEBUGGABLE" in m.group(1))


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("nolook")
    except Exception:  # noqa: BLE001 - a source checkout has no metadata
        return "dev"


def check_build(d: Device, pkg: str, allow_debuggable: bool) -> bool:
    """Refuse a debuggable build: its frame times run several times slower and mislead."""
    dbg = is_debuggable(d, pkg)
    if dbg and not allow_debuggable:
        raise NolookError(
            f"{pkg} is a debuggable build: ART runs it with checks that slow it down, so its frame times "
            "do not say how the release behaves. Install a release (or benchmark) build, "
            "or pass --allow-debuggable to measure anyway."
        )
    return dbg


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
    runs: int = 3,
    allow_debuggable: bool = False,
) -> dict:
    """Measure `runs` times (reset, fling `rounds` times, read) and keep every run.

    One run is one sample: frame timing on a phone varies by several points
    between identical runs, so a single number invites a wrong conclusion.
    """
    pkg = _pkg(d, package)
    dbg = check_build(d, pkg, allow_debuggable)
    est_ms = runs * (int(rounds * 2 * ((duration or 0.3) + gap) * 1000) + 1500) + 3000
    tracer = _start_trace(d, pkg, est_ms) if trace else None
    samples = []
    gesture: dict = {}
    for _ in range(max(1, runs)):
        reset(d, pkg)
        gesture = swipes(d, rounds, within, duration, gap, axis, speed)
        time.sleep(0.5)
        samples.append(read(d, pkg))
    rec = {
        "label": label or "",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "serial": d.serial,
        "package": pkg,
        "nolook": _version(),
        **gesture,
        "rounds": rounds,
        "compile": compile_status(d, pkg),
        "debuggable": dbg,
        **summarise(samples),
    }
    if tracer:
        rec["trace"] = _stop_trace(d, tracer, trace)
    with (d.dir / "perf.jsonl").open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def summarise(samples: list[dict]) -> dict:
    """Median of each counter at the top level (as one run's record has them), plus every run's values."""
    values = {k: [s[k] for s in samples if k in s] for k, _ in _FIELDS}
    values = {k: v for k, v in values.items() if v}
    out: dict = {"runs": len(samples), "values": values}
    for k, v in values.items():
        out[k] = _median(v)
    return out


def _median(v: list) -> float:
    v = sorted(v)
    m = len(v) // 2
    mid = v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2
    return round(mid, 2) if isinstance(mid, float) else mid


def _unit(k: str) -> str:
    return "%" if k in ("janky", "janky_legacy") else "ms" if (k.startswith("p") or k.startswith("gpu_")) else ""


def format_record(rec: dict, keys: list[str] = SHORT) -> str:
    vals = rec.get("values") or {}

    def fmt(k: str) -> str:
        v = rec.get(k)
        if v is None:
            return f"{k} -"
        spread = vals.get(k, [])
        rng = f" ({min(spread)}–{max(spread)})" if len(spread) > 1 and min(spread) != max(spread) else ""
        return f"{k} {v}{_unit(k)}{rng}"

    runs = rec.get("runs", 1)
    head = (f"[{rec['label']}] " if rec.get("label") else "") + (f"median of {runs} runs · " if runs > 1 else "1 run · ")
    tail = f" · compile {rec['compile']}" if rec.get("compile") else ""
    if rec.get("debuggable"):
        tail += " · ⚠ DEBUGGABLE build"
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


def _gesture(r: dict) -> tuple:
    return (r.get("swipe_px"), r.get("swipe_ms"), r.get("rounds"))


def _groups(recs: list[dict]) -> dict[str, dict]:
    """Records with one label pooled: their runs together, if they used one gesture."""
    groups: dict[str, dict] = {}
    for r in recs:
        g = groups.setdefault(r.get("label") or "(no label)", {"values": {}, "gestures": set(), "compile": set(), "debuggable": False, "n": 0})
        vals = r.get("values") or {k: [r[k]] for k, _ in _FIELDS if k in r}
        for k, v in vals.items():
            g["values"].setdefault(k, []).extend(v)
        g["gestures"].add(_gesture(r))
        g["compile"].add(r.get("compile") or "?")
        g["debuggable"] |= bool(r.get("debuggable"))
        g["n"] += r.get("runs", 1)
    return groups


def compare_table(recs: list[dict], keys: list[str] = SHORT, verdict: list[str] = VERDICT) -> list[str]:
    """One row per label (its runs pooled): median and range; then each label against the first."""
    if not recs:
        return ["no runs recorded yet (nolook perf frames --label …)"]
    groups = _groups(recs)
    cols = ["label", "runs"] + keys + ["compile"]

    def cell(g: dict, k: str) -> str:
        v = g["values"].get(k)
        if not v:
            return "-"
        med = _median(v)
        return f"{med}" if min(v) == max(v) else f"{med} [{min(v)}–{max(v)}]"

    rows = [[name, str(g["n"]), *(cell(g, k) for k in keys), "/".join(sorted(g["compile"]))] for name, g in groups.items()]
    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]
    line = lambda cells: "  ".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths)))  # noqa: E731
    out = [line(cols)] + [line(r) for r in rows]

    names = list(groups)
    gestures = {name: g["gestures"] for name, g in groups.items()}
    all_g = set().union(*gestures.values())
    if len(all_g) > 1:
        desc = "; ".join(f"{n}: " + ", ".join(f"{px}px/{ms}ms×{rd}" for px, ms, rd in sorted(gs, key=str)) for n, gs in gestures.items())
        out.append(f"⚠ different gestures ({desc}): these rows are not comparable; measure again with one gesture")
    if any(g["debuggable"] for g in groups.values()):
        out.append("⚠ some runs are of a debuggable build; their frame times are not representative")
    if len(names) >= 2:
        base = groups[names[0]]
        out.append(f"against {names[0]}:")
        for name in names[1:]:
            parts = []
            for k in verdict:
                a, b = base["values"].get(k), groups[name]["values"].get(k)
                if not a or not b:
                    continue
                parts.append(f"{k} {_verdict(a, b)}")
            out.append(f"  {name}: " + " · ".join(parts))
    return out


def _verdict(a: list, b: list) -> str:
    """Whether b differs from a beyond the spread of either.

    With no real difference, two groups of runs still fail to overlap by
    chance: 2 in C(na+nb, na) of the orders are fully separated, 1 in 10 at
    3 against 3. A separation is reported as likely until that chance is small.
    """
    ma, mb = _median(a), _median(b)
    if len(a) < 3 or len(b) < 3:
        return f"{ma}→{mb} (fewer than 3 runs each: cannot tell)"
    if max(a) < min(b):
        word = "higher"
    elif max(b) < min(a):
        word = "lower"
    else:
        return f"{ma}→{mb} within noise (ranges overlap)"
    odds = math.comb(len(a) + len(b), len(a)) // 2
    if odds < 50:
        return f"{ma}→{mb} likely {word} (no overlap, which chance gives 1 in {odds} at {len(a)} vs {len(b)} runs: 5 runs a side to confirm)"
    return f"{ma}→{mb} {word} (no overlap; by chance under 1 in 50)"


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


# What the main thread is doing now -----------------------------------------------

_JAVA = re.compile(r"^[\w$]+(\.[\w$<>-]+)+$")
# Frames every main-thread stack passes through; they say nothing about a hang.
_ENTRY = ("android.app.ActivityThread.", "android.os.Looper.", "android.os.Handler.", "com.android.internal.os.",
          "java.lang.reflect.", "dalvik.system.", "android.view.Choreographer$FrameDisplayEventReceiver.run",
          "android.view.Choreographer$CallbackRecord.run", "android.view.Choreographer.doCallbacks",
          "androidx.compose.ui.platform.AndroidUiDispatcher", "kotlinx.coroutines.DispatchedTask.run",
          "kotlin.coroutines.jvm.internal.BaseContinuationImpl.resumeWith")


def _cpu_ticks(d: Device, pid: str) -> int | None:
    # utime + stime of the main thread, in clock ticks (fields 14 and 15 of stat).
    stat = d._shell(["cat", f"/proc/{pid}/task/{pid}/stat"]).strip()
    if ")" not in stat:
        return None
    fields = stat.rsplit(")", 1)[1].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def stack(d: Device, seconds: float = 3.0, top: int = 15, package: str | None = None) -> list[str]:
    """Sample the main thread with simpleperf: which Java code it spent its CPU in.

    For a hang that spins (a loop, CPU at 100%) this names the code at once,
    without waiting for an ANR. It needs a debuggable or profileable app
    (Android 10+: `<profileable android:shell="true"/>`).
    """
    pkg = _pkg(d, package)
    pid = d._shell(["pidof", pkg]).strip().split(" ")[0]
    if not pid:
        raise NolookError(f"{pkg} is not running")
    data = "/data/local/tmp/nolook-stack.data"
    t0, c0 = time.monotonic(), _cpu_ticks(d, pid)
    rec = d._shell(["sh", "-c", f"simpleperf record --app {pkg} -t {pid} -g --duration {seconds:g} -o {data} 2>&1"], timeout=int(seconds) + 60)
    if "Samples recorded" not in rec:
        hint = next((ln for ln in rec.splitlines() if "E " in ln or "rror" in ln), rec.strip()[-200:])
        raise NolookError(
            f"simpleperf could not sample {pkg}: {hint.strip()}\n"
            "It needs a debuggable build, or on Android 10+ a release build with "
            '<profileable android:shell="true"/> in its manifest.'
        )
    c1, t1 = _cpu_ticks(d, pid), time.monotonic()
    report = d._shell(["sh", "-c", f"simpleperf report -i {data} --children --sort symbol --percent-limit 0.5 2>/dev/null; rm -f {data}"], timeout=120)
    m = re.search(r"Samples: (\d+)", report)
    samples = int(m.group(1)) if m else 0
    # CPU time the thread used over the wall time, from the kernel (100 ticks a second).
    busy = ((c1 - c0) / 100 / (t1 - t0)) if c0 is not None and c1 is not None and t1 > t0 else None
    rows = []
    for ln in report.splitlines():
        mm = re.match(r"\s*([\d.]+)%\s+([\d.]+)%\s+(\S.*)$", ln)
        if mm and _JAVA.match(mm.group(3).strip()):
            rows.append((float(mm.group(1)), float(mm.group(2)), mm.group(3).strip()))
    own = [r for r in rows if not r[2].startswith(_ENTRY)]
    on_cpu = f" · on CPU {min(busy, 1):.0%} of the time" if busy is not None else ""
    out = [f"main thread of {pkg} (pid {pid}) · {seconds:g}s · {samples} samples{on_cpu}"]
    if busy is not None and busy >= 0.9:
        out.append("busy all the time: if the app is stuck, it is spinning in the frames below.")
    elif busy is not None and busy < 0.25:
        out.append("mostly not on the CPU: it is not spinning. If the app is stuck, the main thread is waiting "
                   "(a lock, IO, binder); `nolook perf anr` shows what on, once the system declares an ANR.")
    if not own:
        out.append("no Java frames of the app in the samples")
        return out
    out.append("Java frames by share of main-thread CPU (including what they call):")
    for children, self_, sym in own[:top]:
        out.append(f"  {children:5.1f}%  {sym}" + (f"  (self {self_:.1f}%)" if self_ >= 1 else ""))
    return out
