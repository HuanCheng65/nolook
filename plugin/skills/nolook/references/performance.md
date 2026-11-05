# Performance (Android)

```
nolook perf frames --label A       # 3 runs: reset gfxinfo, fling the list, read; median and range
nolook perf frames --label B       # change one thing between labels
nolook perf compare A B            # pooled per label; says when a difference is within noise
nolook perf ab --a "CMD" --b "CMD" # A B B A …: runs CMD to switch before each turn, then compares
nolook perf frames --trace out/    # same, with a Perfetto trace saved to out/
nolook perf compile speed|verify|speed-profile|reset   # then relaunch the app
nolook perf reset … nolook perf read                   # measure your own interaction (one run)
nolook perf stack [--seconds 3]    # what the main thread spends CPU in, now (a hang that spins)
nolook perf anr                    # last ANR: header and main-thread stack
nolook trace summary out/x.pftrace # read a trace: jank types, worst frames, thread time, GC, binder
nolook trace sql out/x.pftrace "select name, sum(dur) from slice group by name order by 2 desc limit 10"
```

Rules that keep conclusions honest:

- **One run is not a result.** `perf frames` runs 3 times by default
  (`--runs`) and prints the median with the range in brackets. Before you
  say one setting is faster, run `perf compare`: it calls a difference
  "within noise" when the ranges overlap, and "cannot tell" with fewer than
  3 runs a side. Ranges that do not overlap at 3 against 3 still happen by
  chance 1 time in 10, so it says "likely" until each side has 5 runs; say
  "likely" too. It judges janky, p90, p99 and gpu_p90; `--keys` picks others.
- **Alternate A and B.** A phone warms up and throttles as it runs, so the
  label measured later is at a disadvantage. When a command can switch
  between A and B (a setting, `perf compile`, an `adb install`), use
  `perf ab`: it measures in the order A B B A A B, runs the command (on the
  host, with `ANDROID_SERIAL` set) before each turn, and compares. The
  command must leave the list to fling on screen. When the switch is by
  hand, measure A again after B. Records carry the battery temperature and
  CPU frequency cap, and `compare` warns when a later label ran hotter.
- **Warm up first.** A freshly started app runs slower until its code is
  compiled, so `perf frames` flings one run first and keeps nothing from it
  (`--warmup N`; `0` to skip when the app has been in use).
- **Compare like with like.** Records carry the gesture (distance, duration,
  rounds) and nolook's version; `compare` warns when they differ. Keep
  `--speed` / `--duration` / `--rounds` the same across labels.
- **Measure release builds.** nolook refuses a debuggable build (its frame
  times run several times slower); `--allow-debuggable` measures anyway and
  marks the result. Measure on a real device; emulator frame times say little.
- **Hangs.** If the app freezes, run `perf stack` while it is frozen: if the
  main thread is on the CPU all the time, the frames it lists are the loop;
  if it is mostly off the CPU, it is waiting, and `perf anr` shows on what
  once the system declares an ANR. `perf stack` needs a debuggable app, or a
  release with `<profileable android:shell="true"/>` on Android 10+.

Swipes are `input swipe` gestures timed on the device, flung at `--speed`
px/s (default 10000, a quick flick); `--duration 0.25` gives a 250 ms swipe.

Never open a trace file yourself; `trace summary` reads it with Perfetto's
trace processor (fetched on first use) and prints a page. It tells whether
jank is the app missing deadlines (main or render thread) or the pipeline
stuffed (render thread / GPU behind), and what ran inside the worst frames.
Durations in `sql` are nanoseconds.
