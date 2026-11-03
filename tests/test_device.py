import pytest

from nolook import device as device_mod
from nolook import tree
from nolook.config import Config
from nolook.device import Device, NolookError
from tests.test_tree import BAR, LIST, SCREEN, node, screen


class FakeBackend:
    """Screens as XML, switched by what is clicked; records every input."""

    id = "fake"
    size = SCREEN

    def __init__(self, screens, on_click=None, start="main", on_swipe=None):
        self.screens, self.on_click, self.cur, self.on_swipe = screens, on_click or {}, start, on_swipe or {}
        self.inputs = []
        self.logs = ""

    def dump(self):
        return tree.parse(self.screens[self.cur], self.size)

    def click(self, x, y):
        self.inputs.append(("click", x, y))
        for (l, t, r, b), to in self.on_click.items():
            if l <= x < r and t <= y < b:
                self.cur = to

    def long_click(self, x, y):
        self.inputs.append(("long", x, y))

    def swipe(self, x1, y1, x2, y2, duration):
        self.inputs.append(("swipe", duration))
        self.cur = self.on_swipe.get(self.cur, self.cur)

    def press(self, name):
        self.inputs.append(("press", name))

    def set_text(self, value):
        self.inputs.append(("text", value))

    def log_mark(self):
        return "mark"

    def log_since(self, mark):
        return self.logs

    def interruption(self, package):
        return "the device is locked"


@pytest.fixture(autouse=True)
def quick(monkeypatch, tmp_path):
    monkeypatch.setattr(device_mod, "SETTLE_MIN", 0)
    monkeypatch.setattr(device_mod, "SETTLE_POLL", 0)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


def make(backend, **cfg):
    return Device(Config(root=None, **cfg), backend)


LIKE = screen(node((0, 0, 1080, 200), clickable=True, desc="Like · 12"), node((0, 400, 1080, 600), clickable=True, text="Open"))


def test_tap_clicks_the_center_and_settles():
    b = FakeBackend({"main": LIKE, "next": screen(node((0, 0, 1080, 200), text="Opened"))}, {(0, 400, 1080, 600): "next"})
    out = make(b).tap("Open")
    assert b.inputs == [("click", 540, 500)]
    assert [n.label for n in out.after] == ["Opened"] and out.settled is not None


def test_tap_refuses_a_write_action_without_confirm():
    b = FakeBackend({"main": LIKE})
    with pytest.raises(NolookError, match="looks like a write action"):
        make(b).tap("Like · 12")
    assert b.inputs == []
    make(b).tap("Like · 12", confirm=True)
    assert b.inputs == [("click", 540, 100)]


def test_tap_refuses_a_covered_element_without_force():
    b = FakeBackend({"main": screen(LIST, BAR)})
    with pytest.raises(NolookError, match="is covered"):
        make(b).tap("被盖住")
    make(b).tap("被盖住", force=True)
    assert b.inputs[0][0] == "click"


def test_type_needs_a_focused_field_or_a_query():
    field = node((0, 0, 1080, 200), cls="android.widget.EditText", text="ab", clickable=True)
    b = FakeBackend({"main": screen(field)})
    with pytest.raises(NolookError, match="no focused text field"):
        make(b).type("x")
    b.screens["main"] = screen(node((0, 0, 1080, 200), cls="android.widget.EditText", text="ab", clickable=True, focused=True))
    make(b).type("cd", append=True)
    assert b.inputs == [("text", "abcd")]


def test_scroll_until_stops_at_the_end():
    b = FakeBackend({"main": screen(LIST)})
    with pytest.raises(NolookError, match="reached the end"):
        make(b).scroll("down", until="不存在")
    assert b.inputs == [("swipe", 0.25)]


def test_scroll_until_finds_what_comes_into_view():
    later = screen(node((0, 0, 1080, 2400), scrollable=True, rid="list", children=node((0, 100, 1080, 300), text="标题三")))
    b = FakeBackend({"main": screen(LIST), "later": later}, on_swipe={"main": "later"})
    out = make(b).scroll("down", until="标题三")
    assert out.target.label == "标题三"


def test_guard_flags_log_lines_after_an_action():
    b = FakeBackend({"main": LIKE})
    b.logs = "I/http: GET /feed\nI/http: POST /api/like\n"
    out = make(b, guard=["/api/like"]).tap("Open")
    assert out.guard_hits == ["I/http: POST /api/like"]


def test_a_miss_says_what_is_in_the_way():
    b = FakeBackend({"main": LIKE})
    with pytest.raises(tree.NoMatch, match="likely why: the device is locked"):
        make(b).tap("nothing here")


def test_android_only_steps_refuse_another_backend():
    with pytest.raises(NolookError, match="needs an Android device"):
        make(FakeBackend({"main": LIKE})).android
