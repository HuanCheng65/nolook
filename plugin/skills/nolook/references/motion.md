# Animations (Android)

You can't watch a video; `nolook motion` measures one for you.

```
nolook motion --tap "#fab_new" --label open          # record, tap, measure
nolook motion --key back --label close
nolook motion --after "Saved" --for 2                 # record until a state shows, then measure
nolook motion --el "#sheet" --tap … --slow 4           # measure one element; animations 4× slower
nolook motion-compare                                  # recorded runs side by side
```

It reports, for the area that changed: how long the motion runs until it
is within 1% of its end, when it reaches 50% and 90%, the shape of its
progress curve (a sparkline plus ease-out / ease-in / linear …), whether
it reverses (overshoot, bounce) or leads in the other way first, where
frames stalled, and the renderer's jank for the same window. It also saves
one sheet of key frames (0/25/50/75/100% and any reversal) and prints its
path; read it when the question is what it looks like, not how it moves.

To check an animation against its code, work out the expected times from
the springs or tweens and compare: a spring with damping ζ and stiffness k
settles to 1% in about ln(100)/(ζ√k) seconds when underdamped. Use `--slow`
when the device records few frames (emulators record 15–30 fps). If the
report says it was still moving when recording stopped, raise `--for`.
