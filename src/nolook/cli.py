"""nolook: drive an Android device by what is on screen, not by pixels."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import tree
from .config import find_config
from .device import Device, NolookError, Outcome
from .tree import Node, QueryError

SNAP_CAP = 80


def print_nodes(ns: list[Node], cap: int | None = SNAP_CAP) -> None:
    ns = tree.listed(ns)
    shown = ns if cap is None else ns[:cap]
    for n in shown:
        print(tree.format_node(n))
    if len(ns) > len(shown):
        print(f"… {len(ns) - len(shown)} more (nolook snap --all)")


def report(verb: str, o: Outcome, quiet: bool = False) -> None:
    head = verb
    if o.target is not None:
        head += " " + tree.format_node(o.target, compact=True)
    head += f" · settled {o.settled}s" if o.settled is not None else " · still changing after timeout"
    print(head)
    for g in o.guard_hits:
        print(f"⚠ guard: {g}")
    if quiet:
        return
    lines, wholesale = tree.delta(o.before, o.after)
    if wholesale:
        print(f"screen changed ({len(tree.listed(o.after))} elements):")
        print_nodes(o.after, cap=40)
    elif lines:
        print("\n".join(lines))
    else:
        print("no visible change")


def parse_box(s: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected l,t,r,b")
    return tuple(parts)  # type: ignore[return-value]


def build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nolook",
        description="Drive an Android device by what is on screen. Queries: @e12 (ref), #tag (id), "
        "~part (contains), /regex/, plain text (exact); prefix a role to narrow: btn:Save.",
    )
    p.add_argument("--serial", help="device serial (default: .nolook.toml, then the only device)")
    p.add_argument("-q", "--quiet", action="store_true", help="print the action line only, no changes")
    p.add_argument("--session", help=argparse.SUPPRESS)  # the device a Claude Code session last drove, for its pane
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("snap", help="list what is on screen, with refs")
    s.add_argument("--all", action="store_true", help=f"no {SNAP_CAP}-line cap")
    s.add_argument("--in", dest="within", metavar="Q", help="only this element and what is inside it")
    s.add_argument("--json", action="store_true", help="one JSON object per element, for scripts")

    s = sub.add_parser("tap", help="tap one element")
    s.add_argument("query", nargs="?")
    s.add_argument("--nth", type=int, help="pick the nth of several matches (1-based)")
    s.add_argument("--long", action="store_true", help="long press")
    s.add_argument("--force", action="store_true", help="tap even if something covers it")
    s.add_argument("--confirm", action="store_true", help="allow a write action (like, follow, send…); only when the user asked for it")
    s.add_argument("--xy", metavar="X,Y", help="tap raw coordinates (canvas, WebView)")

    s = sub.add_parser("type", help="set the text of a field")
    s.add_argument("text")
    s.add_argument("--into", metavar="Q", help="field to focus first (default: the focused one)")
    s.add_argument("--append", action="store_true", help="add to the current text instead of replacing it")
    s.add_argument("--enter", action="store_true", help="press enter after")

    s = sub.add_parser("key", help="press a key: back, home, enter, recent, delete, volume_up …")
    s.add_argument("name")
    sub.add_parser("back", help="press back")

    s = sub.add_parser("scroll", help="scroll a list; down reveals what is below")
    s.add_argument("direction", nargs="?", default="down", choices=["down", "up", "left", "right"])
    s.add_argument("--in", dest="within", metavar="Q", help="scroll container (default: the largest)")
    s.add_argument("--until", metavar="Q", help="keep scrolling until this is in view")
    s.add_argument("--max", type=int, default=10, help="swipes before giving up (with --until)")

    s = sub.add_parser("wait", help="wait for an element to appear (or --gone)")
    s.add_argument("query")
    s.add_argument("--gone", action="store_true")
    s.add_argument("--timeout", type=float, default=10.0)

    s = sub.add_parser("get", help="everything about one element, untruncated")
    s.add_argument("query")
    s.add_argument("--nth", type=int)

    s = sub.add_parser("open", help="open a URI (deep link) in the app")
    s.add_argument("uri")

    s = sub.add_parser("launch", help="start the app")
    s.add_argument("package", nargs="?")
    s.add_argument("--fresh", action="store_true", help="force-stop first")

    s = sub.add_parser("shot", help="save a small screenshot; prints its path, never the image")
    s.add_argument("query", nargs="?", help="crop to this element")
    s.add_argument("--region", type=parse_box, metavar="L,T,R,B")
    s.add_argument("--width", type=int, help="longest edge in px (default 540 whole screen, 900 crops)")
    s.add_argument("--full", action="store_true", help="native resolution PNG")
    s.add_argument("--png", action="store_true", help="PNG at the small size instead of JPEG")
    s.add_argument("-o", "--out", metavar="PATH")

    s = sub.add_parser("log", help="recent logcat lines of the app")
    s.add_argument("-n", type=int, default=40)
    s.add_argument("--grep", metavar="REGEX")
    s.add_argument("--level", choices=list("VDIWEF"), help="minimum level")

    s = sub.add_parser("run", help="run a Python flow; see `nolook flows`")
    s.add_argument("flow", help="a .py path, or a name in the project's flows dir")
    s.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("flows", help="list the project's flows")
    sub.add_parser("doctor", help="check adb, the device, ffmpeg and Perfetto, and say what to fix")

    s = sub.add_parser("trace", help="summarise a Perfetto trace (no device needed)")
    ts = s.add_subparsers(dest="trace_cmd", required=True)
    t = ts.add_parser("summary", help="frames, jank causes, where the main thread's time went, GC, binder")
    t.add_argument("file")
    t.add_argument("--package", help="app process (default: .nolook.toml's, else the one with most frames)")
    t.add_argument("--top", type=int, default=8, help="slices per thread")
    t = ts.add_parser("sql", help="run a trace processor SQL query")
    t.add_argument("file")
    t.add_argument("query")
    t.add_argument("--limit", type=int, default=40)

    s = sub.add_parser("topng")  # for the Claude Code pane, which draws PNGs only; left out of --help below
    s.add_argument("src")
    s.add_argument("dst", nargs="?", help="default: beside src, as .png")
    s.add_argument("--width", type=int)

    s = sub.add_parser("motion", help="record an animation and report its timing, curve and stalls")
    s.add_argument("--tap", metavar="Q", help="start recording, then tap this")
    s.add_argument("--after", metavar="Q", help="start recording, then wait for this to appear")
    s.add_argument("--key", metavar="NAME", help="start recording, then press this key")
    s.add_argument("--el", metavar="Q", help="measure inside this element (default: whole screen)")
    s.add_argument("--region", type=parse_box, metavar="L,T,R,B")
    s.add_argument("--for", dest="seconds", type=float, default=1.5, help="seconds to keep recording after the trigger")
    s.add_argument("--slow", type=float, metavar="N", help="run animations N times slower while recording")
    s.add_argument("--label", help="name this run for `motion compare`")
    s.add_argument("--confirm", action="store_true", help="allow --tap on a write action")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("motion-compare", help="table of recorded motion runs")
    s.add_argument("labels", nargs="*")
    s.add_argument("--last", type=int, default=10)

    s = sub.add_parser("perf", help="frame timing, compile modes, traces, ANRs")
    ps = s.add_subparsers(dest="perf_cmd", required=True)
    f = ps.add_parser("frames", help="reset gfxinfo, swipe the list back and forth, print the frame summary")
    f.add_argument("--runs", type=int, default=3, help="measure this many times and report the median and range (default 3)")
    f.add_argument("--label", help="name this run for `perf compare`")
    f.add_argument("--trace", metavar="PATH", help="also record a Perfetto trace to this file or directory")
    _fling_args(f)
    f = ps.add_parser("ab", help="measure A and B in turns (A B B A …), running a command to switch before each")
    f.add_argument("--a", dest="switch_a", required=True, metavar="CMD", help="host shell command that sets up A and leaves the list on screen")
    f.add_argument("--b", dest="switch_b", required=True, metavar="CMD", help="the same for B")
    f.add_argument("--label-a", default="A")
    f.add_argument("--label-b", default="B")
    f.add_argument("--cycles", type=int, default=3, help="A+B pairs; each side gets this many runs (default 3; 5 to confirm a difference)")
    f.add_argument("--runs", type=int, default=1, help="runs per turn (default 1)")
    _fling_args(f)
    ps.add_parser("reset", help="reset gfxinfo counters (then act, then `perf read`)")
    r = ps.add_parser("read", help="frame summary since the last reset")
    r.add_argument("--label")
    r.add_argument("--allow-debuggable", action="store_true")
    r.add_argument("--all", action="store_true")
    r.add_argument("--json", action="store_true")
    c = ps.add_parser("compare", help="table of recorded runs")
    c.add_argument("labels", nargs="*", help="only these labels")
    c.add_argument("--last", type=int, default=10)
    c.add_argument("--all", action="store_true", help="every counter as a column")
    c.add_argument("--keys", help="counters to judge each label on, comma-separated (default janky,p90,p99,gpu_p90)")
    c = ps.add_parser("compile", help="set the ART compile mode and stop the app so it takes effect")
    c.add_argument("mode", help="speed, speed-profile, verify, quicken, or reset")
    c = ps.add_parser("stack", help="sample the main thread now: which code it spends CPU in (for hangs that spin)")
    c.add_argument("--seconds", type=float, default=3.0)
    c.add_argument("--top", type=int, default=15)
    c = ps.add_parser("anr", help="the last ANR's header and main thread stack")
    c.add_argument("-n", type=int, default=40)
    sub._choices_actions = [a for a in sub._choices_actions if a.dest != "topng"]
    return p


def _fling_args(f: argparse.ArgumentParser) -> None:
    f.add_argument("--rounds", type=int, default=4, help="forward+back swipe pairs per run (default 4)")
    f.add_argument("--warmup", type=int, default=1, help="runs to fling first and not keep, while the app's code warms up (default 1)")
    f.add_argument("--allow-debuggable", action="store_true", help="measure a debuggable build anyway (its numbers are not representative)")
    f.add_argument("--in", dest="within", metavar="Q", help="list to swipe (default: the largest)")
    f.add_argument("--speed", type=float, default=10000, help="swipe speed in px/s, which it flings at (default 10000, a quick flick)")
    f.add_argument("--duration", type=float, help="seconds per swipe instead of --speed (0.25 matches `input swipe … 250`)")
    f.add_argument("--gap", type=float, default=0.8, help="seconds between swipes")
    f.add_argument("--axis", choices=["y", "x"], default="y")
    f.add_argument("--all", action="store_true", help="print every gfxinfo counter")
    f.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    a = build().parse_args(argv)
    cfg = find_config()
    if a.session:
        from .config import follow_session

        follow_session(cfg, a.session)
    if a.serial:
        cfg.serial = a.serial

    if a.cmd == "topng":
        from PIL import Image

        img = Image.open(a.src)
        if a.width and img.width > a.width:
            img = img.resize((a.width, round(img.height * a.width / img.width)))
        dst = a.dst or str(Path(a.src).with_suffix(".pane.png"))
        img.save(dst, "PNG")
        print(dst)
        return 0

    if a.cmd == "doctor":
        from . import doctor

        return doctor.run(cfg)

    if a.cmd == "trace":
        from . import tracing

        try:
            lines = (
                tracing.summary(a.file, a.package or cfg.package, a.top)
                if a.trace_cmd == "summary"
                else tracing.sql(a.file, a.query, a.limit)
            )
        except NolookError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print("\n".join(lines))
        return 0

    if a.cmd == "flows":
        from .flow import list_flows

        list_flows(cfg)
        return 0

    try:
        if a.cmd == "run":
            from .flow import run_flow

            return run_flow(cfg, a.flow, a.args)
        d = Device(cfg)
        return dispatch(d, a)
    except (NolookError, QueryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - one line beats a traceback in an agent's context
        print(f"error: unexpected {type(e).__name__}: {e} (NOLOOK_DEBUG=1 for the traceback)", file=sys.stderr)
        if os.environ.get("NOLOOK_DEBUG"):
            raise
        return 1


def dispatch(d: Device, a: argparse.Namespace) -> int:
    q = a.quiet
    if a.cmd == "snap":
        ns = d.nodes()
        if a.within:
            box = tree.resolve(ns, a.within)
            ns = [box, *tree.within(ns, box)]
        if a.json:
            import json

            for n in ns:
                print(json.dumps({
                    "ref": n.ref, "role": n.role, "label": n.label, "text": n.text, "desc": n.desc, "id": n.rid,
                    "bounds": list(n.bounds), "center": list(n.center), "depth": n.depth, "covered": n.covered,
                    **{k: v for k, v in n.flags.items()},
                }, ensure_ascii=False))
            return 0
        pkgs = sorted({n.pkg for n in ns if n.pkg})
        print(f"{' + '.join(pkgs) or '(empty)'} · {d.size[0]}x{d.size[1]} · {len(tree.listed(ns))} elements")
        print_nodes(ns, cap=None if a.all else SNAP_CAP)
    elif a.cmd == "tap":
        if a.xy:
            x, y = (int(v) for v in a.xy.split(","))
            report(f"tap ({x},{y})", d.tap_xy(x, y), q)
        elif a.query:
            report("long-press" if a.long else "tap", d.tap(a.query, a.nth, long=a.long, force=a.force, confirm=a.confirm), q)
        else:
            raise NolookError("tap what? give a query or --xy X,Y")
    elif a.cmd == "type":
        o = d.type(a.text, a.into, append=a.append)
        if a.enter:
            o2 = d.key("enter")
            o = Outcome(o.target, o.before, o2.after, o2.settled, o.guard_hits + o2.guard_hits)
        report("type into", o, q)
    elif a.cmd in ("key", "back"):
        name = "back" if a.cmd == "back" else a.name
        report(f"key {name}", d.key(name), q)
    elif a.cmd == "scroll":
        o = d.scroll(a.direction, a.within, a.until, a.max)
        report(f"scroll {a.direction}" + (" until" if a.until else ""), o, q)
    elif a.cmd == "wait":
        ns, took = d.wait(a.query, a.gone, a.timeout)
        hit = tree.match(ns, a.query)
        what = "gone" if a.gone else tree.format_node(hit[0], compact=True)
        print(f"{what} · after {took}s")
    elif a.cmd == "get":
        n, _ = d.find(a.query, a.nth)
        print(tree.format_node(n, compact=True))
        for k, v in (("text", n.text), ("desc", n.desc), ("id", n.rid), ("class", n.cls), ("package", n.pkg)):
            if v:
                print(f"  {k}: {v}")
        print(f"  bounds: {list(n.bounds)}")
        on = [k for k, v in n.flags.items() if v and k != "enabled"]
        if on:
            print(f"  flags: {', '.join(on)}")
    elif a.cmd == "open":
        report(f"open {a.uri}", d.open(a.uri), q)
    elif a.cmd == "launch":
        report("launch", d.launch(a.package, a.fresh), q)
    elif a.cmd == "shot":
        r = d.shot(a.query, a.region, a.width, a.full, a.out, a.png)
        w, h = r["size"]
        print(f"{r['path']}  {w}x{h}  {r['kb']} KB  ≈{r['tokens']} tokens")
    elif a.cmd == "perf":
        perf_dispatch(d, a)
    elif a.cmd == "motion":
        import json

        from . import motion

        r = motion.record(d, a.tap, a.after, a.key, a.el, a.region, a.seconds, a.slow, a.label, a.confirm)
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False))
        else:
            print("\n".join(motion.format_report(r)))
    elif a.cmd == "motion-compare":
        from . import motion

        print("\n".join(motion.compare_lines(motion.history(d, a.labels or None, a.last))))
    elif a.cmd == "log":
        for ln in d.log(a.n, a.grep, a.level):
            print(ln)
    return 0


def perf_dispatch(d: Device, a: argparse.Namespace) -> None:
    import json

    from . import perf

    keys = [k for k, _ in perf._FIELDS] if getattr(a, "all", False) else perf.SHORT
    if a.perf_cmd == "frames":
        rec = perf.frames(d, a.rounds, a.within, a.duration, a.gap, a.axis, a.label, a.trace, speed=a.speed,
                          runs=a.runs, allow_debuggable=a.allow_debuggable, warmup=a.warmup)
        print(json.dumps(rec, ensure_ascii=False) if a.json else perf.format_record(rec, keys))
        if rec.get("trace") and not a.json:
            print(f"trace: {rec['trace']}  (open in ui.perfetto.dev)")
    elif a.perf_cmd == "ab":
        recs = perf.ab(d, a.switch_a, a.switch_b, a.label_a, a.label_b, a.cycles,
                       on_turn=lambda r: print(json.dumps(r, ensure_ascii=False) if a.json else perf.format_record(r, keys), flush=True),
                       rounds=a.rounds, within=a.within, duration=a.duration, gap=a.gap, axis=a.axis, speed=a.speed,
                       runs=a.runs, allow_debuggable=a.allow_debuggable, warmup=a.warmup)
        if not a.json:
            for ln in perf.compare_table(recs, keys):
                print(ln)
    elif a.perf_cmd == "reset":
        perf.reset(d)
        print("gfxinfo reset")
    elif a.perf_cmd == "read":
        pkg = perf._pkg(d, None)
        dbg = perf.check_build(d, pkg, a.allow_debuggable)
        rec = {"label": a.label or "", "debuggable": dbg, **perf.read(d)}
        print(json.dumps(rec, ensure_ascii=False) if a.json else perf.format_record(rec, keys))
    elif a.perf_cmd == "compare":
        verdict = [k.strip() for k in a.keys.split(",") if k.strip()] if a.keys else perf.VERDICT
        unknown = [k for k in verdict if k not in dict(perf._FIELDS)]
        if unknown:
            raise NolookError(f"unknown counter {', '.join(unknown)}; known: {', '.join(k for k, _ in perf._FIELDS)}")
        keys = keys + [k for k in verdict if k not in keys]
        for ln in perf.compare_table(perf.history(d, a.labels or None, a.last), keys, verdict):
            print(ln)
    elif a.perf_cmd == "compile":
        print(f"compile status: {perf.compile(d, a.mode) or 'unknown'} · app stopped; relaunch it before measuring")
    elif a.perf_cmd == "stack":
        print("\n".join(perf.stack(d, a.seconds, a.top)))
    elif a.perf_cmd == "anr":
        for ln in perf.last_anr(d, lines=a.n):
            print(ln)


if __name__ == "__main__":
    sys.exit(main())
