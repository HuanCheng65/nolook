import json
import os

from PIL import Image

from nolook import screen


def test_journal_reads_only_new_whole_lines(tmp_path):
    f = tmp_path / "actions.jsonl"
    f.write_text('{"verb": "old"}\n')
    j = screen.Journal(f)
    assert j.new() == []
    with f.open("a") as fh:
        fh.write('{"verb": "tap"}\n{"verb": "ke')
    assert j.new() == [{"verb": "tap"}]
    with f.open("a") as fh:
        fh.write('y"}\n')
    assert j.new() == [{"verb": "key"}]


def test_journal_starts_over_after_a_trim(tmp_path):
    f = tmp_path / "actions.jsonl"
    f.write_text('{"verb": "a"}\n' * 10)
    j = screen.Journal(f)
    f.write_text('{"verb": "b"}\n')
    assert j.new() == [{"verb": "b"}]


def test_busy_ignores_a_dead_process(tmp_path):
    (tmp_path / "busy").write_text(json.dumps({"pid": os.getpid(), "what": "perf frames"}))
    assert screen.busy(tmp_path) == "perf frames"
    (tmp_path / "busy").write_text(json.dumps({"pid": 2**22 + 12345, "what": "perf frames"}))
    assert screen.busy(tmp_path) is None


def test_the_last_tap_is_outlined_for_a_moment():
    tap = {"at": 100.0, "verb": "tap", "target": {"bounds": [10, 20, 110, 80]}}
    assert screen.mark_box(tap, 101.0) == [10, 20, 110, 80]
    assert screen.mark_box(tap, 103.0) is None
    assert screen.mark_box({**tap, "error": "covered"}, 100.5) is None
    assert screen.mark_box({"at": 100.0, "verb": "scroll down"}, 100.5) is None
    assert screen.mark_box({"at": 100.0, "verb": "tap", "point": [50, 50]}, 100.5) == [20, 20, 80, 80]


def test_compose_draws_the_outline_scaled():
    img = Image.new("RGB", (100, 200), "white")
    out = screen.compose(img, [100, 100, 500, 300], 0.1)
    assert out.getpixel((10, 15)) == screen.MARK_COLOR and img.getpixel((10, 15)) == (255, 255, 255)
