"""Record an animation and turn it into a report an agent can read.

An agent cannot watch a video, so this measures one instead: within a region,
when motion starts and ends, how progress runs over time (a progress curve
p(t) from 0 = first frame to 1 = last frame), whether it is monotonic, where
frames stalled, and what the renderer reported for the same window. One small
sheet of key frames comes with it, for the parts only eyes can judge.

The UI tree cannot do this: while something animates, a dump takes ~120 ms
and reports final bounds, not intermediate ones.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import tree
from .device import Device, NolookError

REMOTE = "/sdcard/nolook-motion.mp4"
SPARK = "▁▂▃▄▅▆▇█"


def _need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise NolookError(f"motion needs {tool} on this machine")


def record(
    d: Device,
    tap: str | None = None,
    after: str | None = None,
    key: str | None = None,
    el: str | None = None,
    region: tuple[int, int, int, int] | None = None,
    seconds: float = 1.5,
    slow: float | None = None,
    label: str | None = None,
    confirm: bool = False,
) -> dict:
    _need_ffmpeg()
    target_box = None
    if el is not None:
        hits = tree.match(d.nodes(), el)
        if len(hits) == 1:
            target_box = hits[0].bounds
    tap_node = None
    if tap is not None:
        tap_node, _ = d.find(tap)
        hit = d.dangerous(tap_node)
        if hit and not confirm:
            raise NolookError(f"{tree.format_node(tap_node, compact=True)} looks like a write action ({hit!r}); pass --confirm if the user asked for it")
        if tap_node.covered:
            raise NolookError(f"{tree.format_node(tap_node, compact=True)} is covered; scroll it into view first")

    old_scale = None
    if slow:
        old_scale = d.android.shell(["settings", "get", "global", "animator_duration_scale"]).strip()
        d.android.shell(["settings", "put", "global", "animator_duration_scale", str(slow)])
    try:
        pkg = d.cfg.package
        if pkg:
            d.android.shell(["dumpsys", "gfxinfo", pkg, "reset"])
        conn, t_start = _start_recording(d, seconds + 6)
        t_trigger = time.monotonic()
        if tap_node is not None:
            d.backend.click(*tap_node.center)
        elif key is not None:
            d.backend.press(key)
        elif after is not None:
            d.wait(after, timeout=seconds + 5)
        time.sleep(seconds)
        _stop_recording(d, conn)
        gfx = {}
        if pkg:
            from . import perf

            try:
                gfx = perf.read(d, pkg)
            except NolookError:
                gfx = {}
    finally:
        if old_scale is not None:
            d.android.shell(["settings", "put", "global", "animator_duration_scale", old_scale if old_scale != "null" else "1"])

    if el is not None and target_box is None:
        # The element may only exist once the animation has run.
        target_box = tree.resolve(d.nodes(), el).bounds

    work = Path(tempfile.mkdtemp(prefix="nolook-motion-"))
    video = work / "motion.mp4"
    d.android.pull(REMOTE, str(video))
    d.android.shell(["rm", "-f", REMOTE])

    box = region or target_box or (0, 0, *d.size)
    frames = _extract(video, box, d.size, work)
    if len(frames) < 2:
        raise NolookError("the recording has fewer than two frames: nothing moved, or screenrecord failed")
    skip = 0.04 if (region is None and target_box is None) else 0.0
    report = _analyse(frames, scale=slow or 1.0, trigger_at=t_trigger - t_start, skip_top=skip)
    if report.get("changed_area"):
        fw_px = frames[0][1].width
        k = (box[2] - box[0]) / fw_px
        report["changed_area"] = [round(box[0] + report["changed_area"][0] * k), round(box[1] + report["changed_area"][1] * k),
                                  round(box[0] + report["changed_area"][2] * k), round(box[1] + report["changed_area"][3] * k)]
    report.update(
        label=label or "",
        trigger=tap or after or key or "",
        region=list(box),
        slow=slow or 1,
        gfx={k: gfx[k] for k in ("frames", "janky", "p90", "p99", "slow_ui") if k in gfx},
    )
    report["sheet"] = _sheet(frames, report, d.dir / "shots")
    report["video"] = str(_keep_video(video, d.dir / "motion"))
    shutil.rmtree(work, ignore_errors=True)
    with (d.dir / "motion.jsonl").open("a") as f:
        f.write(json.dumps({k: v for k, v in report.items() if k != "curve_full"}, ensure_ascii=False) + "\n")
    return report


def _start_recording(d: Device, limit: float):
    d.android.shell(["rm", "-f", REMOTE])
    # A plain shell call ends its session and takes background children with
    # it, so screenrecord runs in the foreground of a connection held open
    # until _stop_recording.
    conn = d.android.shell_stream(["screenrecord", "--bit-rate", "12000000", "--time-limit", str(int(limit)), REMOTE])
    for _ in range(40):
        if d.android.shell(["pidof", "screenrecord"]).strip():
            break
        time.sleep(0.05)
    else:
        conn.close()
        raise NolookError("screenrecord did not start")
    # It takes a moment to start encoding after the process appears.
    time.sleep(0.6)
    return conn, time.monotonic() - 0.6


def _stop_recording(d: Device, conn) -> None:
    d.android.shell(["sh", "-c", "pkill -INT screenrecord"])
    for _ in range(60):
        if not d.android.shell(["pidof", "screenrecord"]).strip():
            break
        time.sleep(0.1)
    conn.close()


def _keep_video(video: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    olds = sorted(dest.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    for p in olds[:-20]:
        p.unlink(missing_ok=True)
    out = dest / f"{time.strftime('%H%M%S')}.mp4"
    shutil.copy(video, out)
    return out


def _extract(video: Path, box, screen, work: Path) -> list[tuple[float, Image.Image]]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-show_entries", "frame=pts_time", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    vw = info["streams"][0]["width"]
    times = [float(f["pts_time"]) for f in info.get("frames", []) if "pts_time" in f]
    k = vw / screen[0]
    l, t, r, b = (round(v * k) for v in box)
    w, h = max(2, r - l), max(2, b - t)
    out_w = min(w, 480) // 2 * 2
    out_h = max(2, round(h * out_w / w) // 2 * 2)
    # Raw frames through a pipe: one per decoded frame, so they line up with
    # ffprobe's timestamps (an image muxer drops frames that share one).
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-fps_mode", "passthrough",
         "-vf", f"crop={w}:{h}:{l}:{t},scale={out_w}:{out_h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    size = out_w * out_h * 3
    n = min(len(raw) // size, len(times))
    return [(times[i], Image.frombytes("RGB", (out_w, out_h), raw[i * size : (i + 1) * size])) for i in range(n)]


def _grey(img: Image.Image) -> np.ndarray:
    w = min(img.width, 160)
    small = img.convert("L").resize((w, max(1, round(img.height * w / img.width))))
    return np.asarray(small, dtype=np.float32)


def _progress(g: list[np.ndarray], start_i: int, first: int, end_i: int) -> tuple[list[float], str]:
    """Progress from 0 to 1 over the window, and how it was measured.

    Distance to the last frame is no measure: an element sliding clear of
    where it ends up is equally far from it all the way. Instead:

    - something moves: the weighted centroid of what differs from the first
      frame sits halfway between where the element started and where it is,
      so it moves in step with it;
    - nothing moves (a fade, a colour change): project each frame onto the
      straight line from the first frame to the last, which is linear in
      opacity.
    """
    g0, g1 = g[start_i], g[end_i]
    h, w = g0.shape
    ys, xs = np.mgrid[0:h, 0:w]

    def centroid(a: np.ndarray) -> np.ndarray | None:
        wgt = np.abs(a - g0)
        tot = wgt.sum()
        if tot < 1e-3:
            return None
        return np.array([(wgt * xs).sum() / tot, (wgt * ys).sum() / tot])

    c_first, c_end = centroid(g[first]), centroid(g1)
    span = None if c_first is None or c_end is None else c_end - c_first
    if span is not None and np.hypot(*span) >= 0.03 * np.hypot(w, h):
        u = span / float(span @ span)
        out = [0.0]
        for i in range(start_i + 1, end_i + 1):
            c = centroid(g[i])
            out.append(0.0 if c is None else float((c - c_first) @ u))
        return [min(max(v, -0.5), 1.5) for v in out], "position"
    line = (g1 - g0).ravel()
    denom = float(line @ line) or 1e-6
    out = [float(((g[i] - g0).ravel() @ line) / denom) for i in range(start_i, end_i + 1)]
    return [min(max(v, -0.5), 1.5) for v in out], "opacity/colour"


def _analyse(frames: list[tuple[float, Image.Image]], scale: float, trigger_at: float, skip_top: float = 0.0) -> dict:
    """Measure the motion between the first frame and the last.

    Start and end are judged against the end states, not frame to frame: a
    spring's tail moves too little per frame to pass any per-frame threshold
    but is still visible, and slowing animations down shrinks per-frame
    change further. Only the area that changes at all is looked at, so a small
    menu is not diluted by a whole screen of still pixels.
    """
    ts = [t for t, _ in frames]
    g = [_grey(img) for _, img in frames]
    base = {"frames_recorded": len(frames), "recorded_s": round((ts[-1] - ts[0]) / scale, 3)}
    g0, g1 = g[0], g[-1]
    h, w = g0.shape
    change = np.max([np.abs(x - g0) for x in g], axis=0)
    top = int(h * skip_top)
    change[:top] = 0  # the status bar's clock is not the animation
    mask = change > 12
    if mask.sum() < 4:
        return {**base, "moved": False}
    ys, xs = np.where(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    roi = [x[y0:y1, x0:x1] for x in g]
    r0, r1 = roi[0], roi[-1]
    d0 = [float(np.abs(x - r0).mean()) for x in roi]
    d1 = [float(np.abs(x - r1).mean()) for x in roi]
    peak0, total = max(d0) or 1e-6, max(d1) or 1e-6
    first = next(i for i, v in enumerate(d0) if v > 0.02 * peak0)
    last_far = max(i for i, v in enumerate(d1) if v > 0.01 * total)
    end_i = min(last_far + 1, len(g) - 1)
    unsettled = last_far >= len(g) - 2 and len(g) > 2 and d1[-2] > 0.01 * total
    start_i = max(first - 1, 0)

    # screenrecord emits no frames while nothing changes, so the frame before
    # the motion can be far older than the motion. It is the visual baseline;
    # the clock starts one ordinary frame interval before the first change.
    inner = [ts[i] - ts[i - 1] for i in range(first + 1, end_i + 1)]
    median = sorted(inner)[len(inner) // 2] if inner else 0.016
    origin = ts[first] - min(ts[first] - ts[start_i], median) if first > 0 else ts[0]
    window = list(range(start_i, end_i + 1))
    p, measured = _progress(roi, start_i, first, end_i)
    tw = [max(0.0, ts[i] - origin) for i in window]
    dur = tw[-1] or 1e-6

    def at_time(frac: float) -> float:
        target = frac * dur
        j = min(range(len(tw)), key=lambda k: abs(tw[k] - target))
        return round(p[j], 2)

    def time_to(level: float) -> float | None:
        for k, v in enumerate(p):
            if v >= level:
                return round(tw[k] / scale * 1000)
        return None

    # Going past the end and coming back (overshoot), or turning back on the
    # way (a bounce), are both a reversal of progress.
    # Moving off the other way first (a drop gathering at its button before it
    # flies) is a lead-in, not a reversal: only turns after real progress count.
    dips = []
    peak = p[0]
    for k, v in enumerate(p):
        if v < peak - 0.06 and peak >= 0.2:
            dips.append({"t_ms": round(tw[k] / scale * 1000), "drop": round(peak - v, 2), "peak": round(peak, 2)})
            peak = v
        peak = max(peak, v)
    lead_in = min(p[: max(1, len(p) // 2)])
    stalls = [
        {"t_ms": round((ts[i - 1] - origin) / scale * 1000), "ms": round((ts[i] - ts[i - 1]) / scale * 1000)}
        for i in range(first + 1, end_i + 1)
        if ts[i] - ts[i - 1] > max(2.2 * median, 0.03)
    ]
    trio = (at_time(0.25), at_time(0.5), at_time(0.75))
    shape = (
        "linear" if all(abs(v - e) < 0.08 for v, e in zip(trio, (0.25, 0.5, 0.75)))
        else "decelerating (ease-out)" if trio[0] > 0.33 and trio[1] > 0.58
        else "accelerating (ease-in)" if trio[1] < 0.42 and trio[2] < 0.67
        else "ease-in-out" if trio[0] < 0.25 and trio[2] > 0.75
        else "irregular"
    )
    idx = [round(k * (len(p) - 1) / 15) for k in range(16)] if len(p) > 1 else [0]
    spark = "".join(SPARK[min(7, max(0, int(min(p[k], 1.0) * 7.999)))] for k in idx)
    fw = frames[0][1].width / w
    return {
        **base,
        "moved": True,
        "changed_area": [x0 * fw, y0 * fw, x1 * fw, y1 * fw],  # in frame pixels; record() maps them to the screen
        # Not reported: when screenrecord's first frame was taken is not known on
        # this clock, so the delay from trigger to motion is off by up to a second.
        "duration_ms": round(dur / scale * 1000),
        "t50_ms": time_to(0.5),
        "t90_ms": time_to(0.9),
        "unsettled": bool(unsettled),
        "shape": shape,
        "measured_by": measured,
        "p_at_25_50_75": list(trio),
        "spark": spark,
        "monotonic": not dips,
        "lead_in": round(-lead_in, 2) if lead_in < -0.05 else 0,
        "dips": dips[:5],
        "stalls": stalls[:8],
        "frames_in_motion": len(window),
        "frame_ms_median": round(median / scale * 1000, 1),
        "_window": [start_i, end_i],
        "_p": [round(v, 3) for v in p],
        "_tw": [round(v / scale, 4) for v in tw],
    }


def _sheet(frames, rep: dict, out_dir: Path) -> str | None:
    if not rep.get("moved"):
        picks = [0, len(frames) - 1]
        labels = ["first", "last"]
    else:
        s, e = rep["_window"]
        p, tw = rep["_p"], rep["_tw"]
        picks, labels = [], []
        for level in (0.0, 0.25, 0.5, 0.75, 1.0):
            k = min(range(len(p)), key=lambda j: abs(p[j] - level) + (0 if level < 1 else (len(p) - j) * 1e-3))
            picks.append(s + k)
            labels.append(f"{round(tw[k] * 1000)}ms p{level:.2f}")
        for dip in rep.get("dips", [])[:2]:
            k = min(range(len(tw)), key=lambda j: abs(tw[j] * 1000 - dip["t_ms"]))
            picks.append(s + k)
            labels.append(f"{dip['t_ms']}ms dip")
    seen, tiles = set(), []
    for i, lab in zip(picks, labels):
        if i in seen:
            continue
        seen.add(i)
        tiles.append((frames[i][1], lab))
    tw_px = min(tiles[0][0].width, max(120, 1200 // len(tiles)))
    th_px = round(tiles[0][0].height * tw_px / tiles[0][0].width)
    if th_px > 700:
        th_px, tw_px = 700, round(tiles[0][0].width * 700 / tiles[0][0].height)
    sheet = Image.new("RGB", (tw_px * len(tiles) + 4 * (len(tiles) - 1), th_px + 22), "white")
    draw = ImageDraw.Draw(sheet)
    for n, (img, lab) in enumerate(tiles):
        x = n * (tw_px + 4)
        sheet.paste(img.resize((tw_px, th_px)), (x, 22))
        draw.text((x + 3, 4), lab, fill="black")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{time.strftime('%H%M%S')}-motion.jpg"
    sheet.save(path, "JPEG", quality=75)
    return str(path)


def format_report(r: dict) -> list[str]:
    head = f"motion{(' [' + r['label'] + ']') if r.get('label') else ''} · {r.get('trigger') or 'no trigger'}"
    head += f" · region {r['region']} · {r['frames_recorded']} frames over {r['recorded_s']}s"
    if r.get("slow", 1) != 1:
        head += f" · slowed ×{r['slow']}, times shown at normal speed"
    lines = [head]
    if not r.get("moved"):
        lines.append("no motion in the region")
    else:
        lines.append(
            f"runs {r['duration_ms']}ms to within 1% of its end · 50% at {r['t50_ms']}ms · 90% at {r['t90_ms']}ms"
            + f" · in {r['changed_area']}"
        )
        if r.get("unsettled"):
            lines.append("⚠ still moving when the recording stopped: run again with a longer --for")
        lines.append(
            f"progress {r['spark']}  {r['shape']} · by {r['measured_by']} "
            f"(p at 25/50/75% of time: {'/'.join(str(v) for v in r['p_at_25_50_75'])})"
        )
        if r.get("lead_in"):
            lines.append(f"lead-in: moves {r['lead_in']} the other way before setting off (e.g. gathering at its anchor)")
        if r["monotonic"]:
            lines.append("monotonic: yes")
        else:
            lines.append(
                "monotonic: no · "
                + ", ".join(f"reverses by {d['drop']} at {d['t_ms']}ms after reaching {d['peak']}" for d in r["dips"])
                + " (peak > 1: overshoot; < 1: turned back)"
            )
        lines.append(
            f"{r['frames_in_motion']} frames in motion, median {r['frame_ms_median']}ms apart · "
            + ("stalls: " + ", ".join(f"{s['ms']}ms at {s['t_ms']}ms" for s in r["stalls"]) if r["stalls"] else "no stalls")
        )
    g = r.get("gfx")
    if g:
        lines.append("renderer: " + " · ".join(f"{k} {v}{'%' if k == 'janky' else 'ms' if k.startswith('p') else ''}" for k, v in g.items()))
    if r.get("sheet"):
        lines.append(f"sheet (not read): {r['sheet']}")
    lines.append(f"video: {r['video']}")
    return lines


def history(d: Device, labels: list[str] | None = None, last: int = 10) -> list[dict]:
    f = d.dir / "motion.jsonl"
    if not f.is_file():
        return []
    recs = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
    if labels:
        recs = [r for r in recs if r.get("label") in labels]
    return recs[-last:]


def compare_lines(recs: list[dict]) -> list[str]:
    if not recs:
        return ["no motion runs recorded yet (nolook motion … --label X)"]
    out = [f"{'label':<12} {'trigger':<14} {'dur':>6} {'t50':>5} {'t90':>5} {'shape':<24} mono stalls curve"]
    for r in recs:
        if not r.get("moved"):
            out.append(f"{r.get('label', ''):<12} {r.get('trigger', '')[:14]:<14} (no motion)")
            continue
        out.append(
            f"{r.get('label', ''):<12} {r.get('trigger', '')[:14]:<14} {r['duration_ms']:>5}ms {r['t50_ms']:>5} {r['t90_ms']:>5} "
            f"{r['shape'][:24]:<24} {'yes' if r['monotonic'] else 'no ':<4} {len(r['stalls']):>6} {r['spark']}"
        )
    return out
