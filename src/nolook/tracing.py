"""Read a Perfetto trace into a summary an agent can act on.

Uses Perfetto's own trace processor (the `perfetto` Python package fetches a
matching trace_processor_shell on first use), so nothing needs installing by
hand, and the agent gets a page of findings instead of a 20 MB file.
"""

from __future__ import annotations

from pathlib import Path

from .device import NolookError

MS = 1_000_000

# What each frame-timeline jank type points at, after Perfetto's definitions.
JANK_NOTES = {
    "App Deadline Missed": "the app's main or render thread ran past the frame's deadline",
    "Buffer Stuffing": "frames queued behind earlier ones: the pipeline is full, usually the render thread or GPU falling behind, not one slow frame",
    "SurfaceFlinger": "the system compositor was late, not the app",
    "Prediction Error": "the frame's timing was mispredicted by the scheduler",
    "Display HAL": "the display hardware was late",
    "Unknown": "cause not attributed",
}


def _open(path: str):
    try:
        from perfetto.trace_processor import TraceProcessor
    except ImportError as e:
        raise NolookError("reading traces needs the perfetto package: uv tool install --with perfetto … (or pip install perfetto)") from e
    if not Path(path).is_file():
        raise NolookError(f"no trace at {path}")
    return TraceProcessor(trace=path)


def _rows(tp, sql: str) -> list:
    return list(tp.query(sql))


def _ms(ns) -> str:
    return "-" if ns is None else f"{ns / MS:.1f}ms"


def summary(path: str, package: str | None, top: int = 8) -> list[str]:
    tp = _open(path)
    try:
        return _summary(tp, package, top)
    finally:
        tp.close()


def _summary(tp, package: str | None, top: int) -> list[str]:
    out: list[str] = []
    procs = _rows(tp, "select upid, pid, name from process where name is not null")
    if package:
        mine = [p for p in procs if p.name == package]
    else:
        # The app with the most frames, when none is named.
        counts = _rows(tp, "select upid, count(*) n from actual_frame_timeline_slice group by upid order by n desc limit 1")
        mine = [p for p in procs if counts and p.upid == counts[0].upid]
    if not mine:
        names = ", ".join(sorted({p.name for p in procs if "." in p.name})[:12])
        raise NolookError(f"no process {package!r} in the trace; apps in it: {names}")
    upid, pid, name = mine[0].upid, mine[0].pid, mine[0].name
    bounds = _rows(tp, "select start_ts s, (end_ts - start_ts) d from trace_bounds")[0]
    t0, span = bounds.s, bounds.d
    out.append(f"trace · {name} (pid {pid}) · {span / 1e9:.1f}s")

    # Frames -----------------------------------------------------------------
    frames = _rows(tp, f"""
        select count(*) n,
               sum(case when jank_type != 'None' then 1 else 0 end) janky,
               sum(case when jank_type like '%App Deadline Missed%' then 1 else 0 end) app_missed
        from actual_frame_timeline_slice where upid = {upid}""")[0]
    if frames.n:
        out.append(f"frames: {frames.n} · janky {frames.janky} ({100 * frames.janky / frames.n:.1f}%) · app deadline missed {frames.app_missed}")
        kinds = _rows(tp, f"""
            select jank_type, count(*) c, max(dur) worst from actual_frame_timeline_slice
            where upid = {upid} and jank_type != 'None' group by jank_type order by c desc""")
        for k in kinds:
            note = next((v for key, v in JANK_NOTES.items() if key in k.jank_type), "")
            out.append(f"  {k.c:>4} × {k.jank_type} (worst {_ms(k.worst)})" + (f" — {note}" if note else ""))
        pct = _rows(tp, f"""
            select dur from actual_frame_timeline_slice where upid = {upid} order by dur""")
        durs = [r.dur for r in pct]
        if durs:
            q = lambda f: durs[min(len(durs) - 1, int(f * len(durs)))]  # noqa: E731
            out.append(f"  frame duration p50 {_ms(q(0.5))} · p90 {_ms(q(0.9))} · p99 {_ms(q(0.99))} · max {_ms(durs[-1])}")
    else:
        out.append("frames: no frame timeline for this app (Android 12+ with the frametimeline data source)")

    threads = _rows(tp, f"select utid, tid, name from thread where upid = {upid}")
    main = next((t for t in threads if t.tid == pid), None)
    render = next((t for t in threads if t.name == "RenderThread"), None)

    # The worst app frames and what the main thread did in them ---------------
    worst = _rows(tp, f"""
        select ts, dur, jank_type from actual_frame_timeline_slice
        where upid = {upid} and jank_type like '%App Deadline Missed%'
        order by dur desc limit 3""")
    if worst and main is not None:
        out.append("worst app-missed frames, longest main-thread work inside each:")
        for f in worst:
            out.append(f"  frame at {(f.ts - t0) / 1e9:.2f}s, {_ms(f.dur)}:")
            inside = _rows(tp, f"""
                select s.name, s.dur, s.depth from slice s join thread_track tt on s.track_id = tt.id
                where tt.utid = {main.utid} and s.ts < {f.ts + f.dur} and s.ts + s.dur > {f.ts} and s.depth between 1 and 4
                order by s.dur desc limit 4""")
            for s in inside:
                out.append(f"    {_ms(s.dur):>8}  {'  ' * (s.depth - 1)}{s.name[:90]}")

    # Where the main thread's time went --------------------------------------
    for label, t in (("main thread", main), ("RenderThread", render)):
        if t is None:
            continue
        states = _rows(tp, f"""
            select state, sum(dur) d from thread_state where utid = {t.utid} group by state order by d desc""")
        total = sum(s.d for s in states) or 1
        names = {"Running": "running", "R": "waiting for CPU", "R+": "waiting for CPU", "S": "sleeping", "D": "blocked on IO", "DK": "blocked on IO"}
        parts = [f"{names.get(s.state, s.state)} {100 * s.d / total:.0f}%" for s in states[:5] if s.d / total >= 0.01]
        out.append(f"{label}: " + " · ".join(parts))
        heavy = _rows(tp, f"""
            select s.name, count(*) c, sum(s.dur) total, max(s.dur) worst
            from slice s join thread_track tt on s.track_id = tt.id
            where tt.utid = {t.utid} and s.depth >= 1
            group by s.name order by total desc limit {top}""")
        for h in heavy:
            out.append(f"  {_ms(h.total):>9} total · {h.c:>5}× · worst {_ms(h.worst):>8}  {h.name[:80]}")

    # GC and binder ------------------------------------------------------------
    gc = _rows(tp, f"""
        select count(*) c, sum(s.dur) total, max(s.dur) worst from slice s
        join thread_track tt on s.track_id = tt.id join thread th using (utid)
        where th.upid = {upid} and (s.name like '%GC%' and s.name not like '%GCTask%')""")[0]
    if gc.c:
        out.append(f"GC: {gc.c} slices · {_ms(gc.total)} total · worst {_ms(gc.worst)}")
    if main is not None:
        binder = _rows(tp, f"""
            select count(*) c, sum(s.dur) total, max(s.dur) worst from slice s join thread_track tt on s.track_id = tt.id
            where tt.utid = {main.utid} and s.name = 'binder transaction'""")[0]
        if binder.c:
            out.append(f"binder on main thread: {binder.c} calls · {_ms(binder.total)} total · worst {_ms(binder.worst)}")
    out.append("deeper: nolook trace sql <file> \"select …\" (tables: slice, thread_track, thread, actual_frame_timeline_slice, thread_state)")
    return out


def sql(path: str, query: str, limit: int = 40) -> list[str]:
    tp = _open(path)
    try:
        rows = _rows(tp, query)
    except Exception as e:  # noqa: BLE001 - the processor's own message is the useful part
        raise NolookError(f"query failed: {str(e).strip().splitlines()[-1][:300]}") from e
    finally:
        tp.close()
    if not rows:
        return ["(no rows)"]
    cols = list(vars(rows[0]).keys())
    shown = rows[:limit]
    cells = [[_cell(getattr(r, c)) for c in cols] for r in shown]
    w = [min(60, max(len(c), *(len(x[i]) for x in cells))) for i, c in enumerate(cols)]
    out = ["  ".join(c.ljust(w[i]) for i, c in enumerate(cols))]
    out += ["  ".join(x[i][: w[i]].ljust(w[i]) for i in range(len(cols))) for x in cells]
    if len(rows) > limit:
        out.append(f"… {len(rows) - limit} more rows (--limit)")
    return out


def _cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)
