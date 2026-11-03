from PIL import Image, ImageDraw

from nolook import motion


def frame(x):
    img = Image.new("RGB", (200, 100), "black")
    ImageDraw.Draw(img).rectangle([x, 30, x + 40, 70], fill="white")
    return img


def clip(progress, times):
    return [(t, frame(round(10 + 140 * p))) for t, p in zip(times, progress)]


def test_ease_out_slide_with_idle_gap_and_a_stall():
    # Static, then 0.5 s with no frames (screenrecord skips unchanged ones),
    # then an ease-out slide at 60 fps with one 50 ms hole, then rest.
    ease = [1 - (1 - k / 12) ** 3 for k in range(13)]
    times = [0.0] + [0.5 + k / 60 for k in range(13)]
    times[8:] = [t + 0.05 for t in times[8:]]
    frames = clip([0.0] + ease, times) + [(times[-1] + 0.5, frame(150))]
    r = motion._analyse(frames, scale=1.0, trigger_at=0.3)
    assert r["moved"]
    assert 180 <= r["duration_ms"] <= 280
    assert r["shape"].startswith("decelerating")
    assert r["monotonic"]
    assert len(r["stalls"]) == 1 and 55 <= r["stalls"][0]["ms"] <= 75


def test_overshoot_is_not_monotonic():
    path = [0.0, 0.3, 0.6, 0.9, 1.1, 1.15, 1.05, 1.0]
    frames = clip(path, [k / 60 for k in range(len(path))]) + [(0.5, frame(150))]
    r = motion._analyse(frames, scale=1.0, trigger_at=0.0)
    assert not r["monotonic"]


def test_no_motion():
    frames = [(k / 60, frame(10)) for k in range(5)]
    assert motion._analyse(frames, scale=1.0, trigger_at=0.0)["moved"] is False


def test_linear_fade_is_measured_by_opacity():
    def faded(a):
        img = Image.new("RGB", (200, 100), "black")
        ImageDraw.Draw(img).rectangle([60, 30, 140, 70], fill=(round(255 * a),) * 3)
        return img

    frames = [(0.0, faded(0))] + [(0.5 + k / 60, faded(k / 12)) for k in range(1, 13)] + [(1.2, faded(1))]
    r = motion._analyse(frames, scale=1.0, trigger_at=0.0)
    assert r["measured_by"] == "opacity/colour"
    assert r["shape"] == "linear"


def test_spring_tail_counts_until_it_is_within_one_percent():
    import math

    # Critically damped spring, ω = 2π/0.3 s: analytically within 1% at ~318 ms.
    w = 2 * math.pi / 0.3
    pos = lambda t: 1 - (1 + w * t) * math.exp(-w * t)  # noqa: E731
    times = [0.0] + [0.4 + k / 60 for k in range(60)]
    frames = [(0.0, frame(10))] + [(t, frame(round(10 + 140 * pos(t - 0.4)))) for t in times[1:]]
    r = motion._analyse(frames, scale=1.0, trigger_at=0.4)
    assert r["moved"] and not r["unsettled"]
    assert 250 <= r["duration_ms"] <= 420, r["duration_ms"]
    assert r["monotonic"]
