from pathlib import Path

from nolook import flow as flow_mod
from nolook import tree
from nolook.device import NolookError, Outcome

from .test_tree import SCREEN, node, screen


def nodes_of(*labels):
    xml = screen(*(node((0, 200 * i, 1080, 200 * i + 150), clickable=True, desc=lab) for i, lab in enumerate(labels)))
    return tree.parse(xml, SCREEN)


class FakeDevice:
    """Screens by name; a tap on a label moves to the screen `links` names."""

    def __init__(self, screens, links, start):
        self.screens, self.links, self.cur = screens, links, start
        self.prev, self.counter, self.taps = [], 0, []

    def nodes(self):
        ns = nodes_of(*self.screens[self.cur])
        self.counter = tree.assign_refs(ns, self.prev, self.counter)
        self.prev = tree.to_state(ns)
        return ns

    def find(self, q, nth=None):
        ns = self.nodes()
        return tree.resolve(ns, q, nth), ns

    def exists(self, q):
        return bool(tree.match(self.nodes(), q))

    def tap(self, q, nth=None, long=False, force=False, confirm=False, settle=3.0):
        n, before = self.find(q, nth)
        self.taps.append(n.label)
        self.cur = self.links.get(n.label, self.cur)
        return Outcome(n, before, self.nodes(), 0.1, [])

    def wait(self, q, gone=False, timeout=10.0):
        ns = self.nodes()
        if bool(tree.match(ns, q)) == gone:
            raise NolookError(f"{q!r} never appeared")
        return ns, 0.0


def test_tap_returns_what_changed():
    d = FakeDevice({"list": ["Notes", "Groceries"], "note": ["Back", "Groceries", "Save"]}, {"Groceries": "note"}, "list")
    f = flow_mod.Flow(d)
    step = f.tap("Groceries")
    # Groceries is on both screens, so it keeps its ref and is not "added".
    assert [n.label for n in step.added] == ["Back", "Save"]
    assert [n.label for n in step.removed] == ["Notes"]
    assert step.shows("Save")
    assert not step.shows("Notes")
    assert step.label == "Groceries"  # attributes fall through to the target


def test_dismiss_when_closes_a_dialog_after_an_action():
    d = FakeDevice(
        {"list": ["Open"], "dialog": ["New version", "Later"], "page": ["Page"]},
        {"Open": "dialog", "Later": "page"},
        "list",
    )
    f = flow_mod.Flow(d)
    f.dismiss_when("New version", tap="Later")
    step = f.tap("Open")
    assert d.taps == ["Open", "Later"]
    assert step.shows("Page")


def test_dismiss_when_works_while_waiting():
    d = FakeDevice({"dialog": ["Rate us", "Not now"], "page": ["Page"]}, {"Not now": "page"}, "dialog")
    f = flow_mod.Flow(d)
    f.dismiss_when("Rate us", tap="Not now")
    f.wait("Page", timeout=1)
    assert d.taps == ["Not now"]


def test_results_are_printed_on_success_and_failure(tmp_path: Path, capsys, monkeypatch):
    d = FakeDevice({"s": ["Likes · 915"]}, {}, "s")
    monkeypatch.setattr(flow_mod, "Device", lambda cfg: d)
    monkeypatch.setattr(flow_mod, "_dump_failure", lambda dev: None)
    cfg = type("C", (), {"flows_dir": tmp_path})()
    good = tmp_path / "good.py"
    good.write_text('d.result(likes=int(d.text("~Likes").split(" · ")[1]))\n')
    assert flow_mod.run_flow(cfg, str(good), []) == 0
    assert 'result {"likes": 915}' in capsys.readouterr().out
    bad = tmp_path / "bad.py"
    bad.write_text('d.timeout = 0.3\nd.result(seen="list")\nd.tap("Missing")\n')
    assert flow_mod.run_flow(cfg, str(bad), []) == 1
    out = capsys.readouterr().out
    assert "✗ step 1 (line 3)" in out and 'result {"seen": "list"}' in out


class SlowDevice(FakeDevice):
    """The screen becomes `later` after `polls` reads, like a page still loading."""

    def __init__(self, screens, links, start, later, polls):
        super().__init__(screens, links, start)
        self.later, self.polls = later, polls

    def nodes(self):
        self.polls -= 1
        if self.polls == 0:
            self.cur = self.later
        return super().nodes()


def test_tap_waits_for_its_target():
    d = SlowDevice({"loading": [], "page": ["Open"], "next": ["Done"]}, {"Open": "next"}, "loading", "page", 3)
    f = flow_mod.Flow(d)
    assert f.tap("Open").shows("Done")


def test_ambiguous_query_fails_at_once():
    import time

    import pytest

    d = FakeDevice({"s": ["Reply · 1", "Reply · 2"]}, {}, "s")
    f = flow_mod.Flow(d)
    start = time.monotonic()
    with pytest.raises(tree.Ambiguous):
        f.tap("~Reply")
    assert time.monotonic() - start < 1
    assert f("~Reply").count() == 2
    assert f("~Reply").first().label == "Reply · 1"


def test_locator_scopes_to_a_container():
    xml = screen(
        node((0, 0, 1080, 1000), rid="list", children=node((0, 100, 1080, 200), clickable=True, desc="OK")),
        node((0, 1500, 1080, 2400), rid="sheet", children=node((0, 1600, 1080, 1700), clickable=True, desc="OK")),
    )

    class D(FakeDevice):
        def nodes(self):
            ns = tree.parse(xml, SCREEN)
            self.counter = tree.assign_refs(ns, self.prev, self.counter)
            self.prev = tree.to_state(ns)
            return ns

    d = D({}, {}, None)
    f = flow_mod.Flow(d)
    assert f("#sheet")("OK").node.center[1] == 1650
    assert f("#list")("OK").node.center[1] == 150
    assert f("btn:OK").count() == 2


def test_go_taps_in_order():
    d = FakeDevice({"a": ["Me"], "b": ["Settings"], "c": ["Display"]}, {"Me": "b", "Settings": "c"}, "a")
    f = flow_mod.Flow(d)
    assert f.go("Me", "Settings").shows("Display")
    assert d.taps == ["Me", "Settings"]


def test_bare_assert_reports_its_line(tmp_path: Path, capsys, monkeypatch):
    d = FakeDevice({"s": ["Home"]}, {}, "s")
    monkeypatch.setattr(flow_mod, "Device", lambda cfg: d)
    monkeypatch.setattr(flow_mod, "_dump_failure", lambda dev: None)
    cfg = type("C", (), {"flows_dir": tmp_path})()
    f = tmp_path / "a.py"
    f.write_text('assert d.sees("Home")\nassert d.sees("Saved")\n')
    assert flow_mod.run_flow(cfg, str(f), []) == 1
    assert 'line 2): assert failed: assert d.sees("Saved")' in capsys.readouterr().out
