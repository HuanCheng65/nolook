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
    assert "none: janky 43.0→21.0 likely lower (no overlap, which chance gives 1 in 10 at 3 vs 3 runs" in text


def test_five_runs_a_side_make_a_separation_firm():
    lines = perf.compare_table([rec("a", [40.0, 41.0, 42.0, 43.0, 44.0]), rec("b", [30.0, 31.0, 32.0, 33.0, 34.0])])
    assert any("janky 42.0→32.0 lower (no overlap; by chance under 1 in 50)" in ln for ln in lines)


def test_compare_judges_gpu_p90_and_chosen_keys():
    a, b = rec("a", [40.0, 41.0, 42.0]), rec("b", [40.0, 41.0, 42.0])
    a["values"]["gpu_p90"], b["values"]["gpu_p90"] = [18, 19, 20], [11, 11, 12]
    text = "\n".join(perf.compare_table([a, b]))
    assert "gpu_p90 19→11 likely lower" in text
    only = perf.compare_table([a, b], verdict=["janky"])[-1]
    assert "gpu_p90" not in only and "janky" in only


def test_compare_warns_when_gestures_differ():
    lines = perf.compare_table([rec("old", [40.0, 41.0, 42.0], (1270, 250, 4)), rec("new", [30.0, 31.0, 32.0])])
    assert any("different gestures" in ln for ln in lines)


def test_too_few_runs_cannot_tell():
    lines = perf.compare_table([rec("a", [40.0]), rec("b", [30.0])])
    assert any("cannot tell" in ln for ln in lines)


def test_parse_heat_reads_temperature_status_and_cpu_cap():
    text = "  temperature: 312\nThermal Status: 1\ncap\n1900800\n1900800\n1497600\nmax\n1900800\n1900800\n2457600\n"
    assert perf.parse_heat(text) == {"temp": 31.2, "thermal": 1, "cpu_cap": 61}


def test_parse_heat_skips_what_the_device_does_not_report():
    assert perf.parse_heat("  temperature: 250\ncap\nmax\n") == {"temp": 25.0}


def hot(label, temp, cap=100):
    r = rec(label, [40.0, 41.0, 42.0])
    r["heat"] = [{"temp": temp, "cpu_cap": cap}] * 3
    return r


def test_compare_warns_when_the_later_label_ran_hotter():
    lines = perf.compare_table([hot("a", 30.0), hot("b", 33.0, cap=80)])
    warn = [ln for ln in lines if ln.startswith("⚠ b was measured after a")]
    assert warn and "3.0°C warmer" in warn[0] and "CPU capped at 80%" in warn[0] and "perf ab" in warn[0]


def test_compare_does_not_warn_when_runs_were_interleaved():
    lines = perf.compare_table([hot("a", 30.0), hot("b", 33.0), hot("a", 33.0)])
    assert not any("measured after" in ln for ln in lines)


def test_ab_alternates_abba_and_switches_before_each_turn(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(perf, "frames", lambda d, label, **kw: order.append(label) or {"label": label})

    class D:
        serial = "x"

    log = tmp_path / "switches"
    perf.ab(D(), f"echo a >> {log}", f"echo b >> {log}", cycles=3)
    assert order == ["A", "B", "B", "A", "A", "B"]
    assert log.read_text().split() == ["a", "b", "b", "a", "a", "b"]


def test_ab_stops_when_a_switch_fails(monkeypatch):
    monkeypatch.setattr(perf, "frames", lambda d, label, **kw: {"label": label})

    class D:
        serial = "x"

    try:
        perf.ab(D(), "true", "echo nope >&2; exit 3")
    except perf.NolookError as e:
        assert "switching to B failed (exit 3)" in str(e) and "nope" in str(e)
    else:
        raise AssertionError("expected a failure")
