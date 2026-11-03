from nolook import perf


def rec(label, janky, gesture=(1270, 127, 4)):
    px, ms, rounds = gesture
    return {"label": label, "runs": len(janky), "values": {"janky": janky, "p90": [17] * len(janky)},
            "janky": perf._median(janky), "swipe_px": px, "swipe_ms": ms, "rounds": rounds, "compile": "speed"}


def test_summarise_keeps_runs_and_medians():
    out = perf.summarise([{"janky": 5.0, "p90": 17}, {"janky": 9.0, "p90": 19}, {"janky": 6.0, "p90": 18}])
    assert out["runs"] == 3 and out["janky"] == 6.0 and out["values"]["p90"] == [17, 19, 18]


def test_compare_calls_overlap_noise_and_separation_a_difference():
    lines = perf.compare_table([rec("glass", [40.0, 45.0, 43.0]), rec("flat", [41.0, 44.0, 42.0]), rec("none", [20.0, 22.0, 21.0])])
    text = "\n".join(lines)
    assert "flat: janky 43.0→42.0 within noise" in text
    assert "none: janky 43.0→21.0 lower (no overlap)" in text


def test_compare_warns_when_gestures_differ():
    lines = perf.compare_table([rec("old", [40.0, 41.0, 42.0], (1270, 250, 4)), rec("new", [30.0, 31.0, 32.0])])
    assert any("different gestures" in ln for ln in lines)


def test_too_few_runs_cannot_tell():
    lines = perf.compare_table([rec("a", [40.0]), rec("b", [30.0])])
    assert any("cannot tell" in ln for ln in lines)
