"""The web backend against a real headless Chromium, one page per rule.

Skipped when no Chrome or Chromium is installed.
"""

import os
import time

import pytest

from nolook import tree, web
from nolook.backend import NolookError
from nolook.config import Config
from nolook.device import Device


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    old = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(cache)
    cfg = Config(root=cache, web={"profile": "test"})
    try:
        web.find_browser(cfg)
    except NolookError:
        pytest.skip("no Chrome or Chromium installed")
    web.start(cfg)
    yield cfg
    web.stop(cfg)
    if old is None:
        os.environ.pop("XDG_CACHE_HOME", None)
    else:
        os.environ["XDG_CACHE_HOME"] = old


@pytest.fixture
def site(tmp_path):
    """Serve tmp_path over HTTP: the browser may not see this process's files."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def page(browser, tmp_path, site):
    """Load HTML into the browser's page; returns its backend."""

    def load(html: str) -> web.WebBackend:
        f = tmp_path / "page.html"
        f.write_text("<!doctype html><meta charset=utf-8><style>body{margin:0;font:14px sans-serif}</style>" + html)
        b = web.WebBackend(browser)
        b.open_uri(site + "page.html", None)
        deadline = time.monotonic() + 5
        while b.cdp.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)["result"]["value"] != "complete":
            if time.monotonic() > deadline:
                raise AssertionError("page did not load")
            time.sleep(0.05)
        return b

    return load


def listed(b):
    return [(n.role, n.label) for n in tree.listed(b.dump())]


def test_pointer_divs_are_controls_named_by_their_text(page):
    b = page('<div style="cursor:pointer" onclick="0"><p>Title one</p><p><span></span><span>Uploader</span></p></div>')
    assert ("btn", "Title one · Uploader") in listed(b)


def test_icon_font_glyphs_are_dropped_and_a_nameless_button_stays_listed(page):
    b = page("<button><span></span></button><button title=Search>\U000f0349</button>")
    assert listed(b) == [("btn", ""), ("btn", "Search")]


def test_a_field_without_a_name_takes_the_label_beside_it(page):
    b = page('<div><span>Model</span><div role=combobox tabindex=0>gpt</div></div>')
    n = next(n for n in b.dump() if n.role == "btn")
    assert n.label == "Model · gpt" and n.flags["inferred"]
    assert ' btn ~"Model · gpt"' in tree.format_node(n, compact=True)


def test_a_label_for_a_field_is_not_listed_again(page):
    b = page('<label for=u>User name</label><input id=u><span id=pl>Password</span><input aria-labelledby=pl type=password>')
    assert listed(b) == [("input", "User name"), ("input", "Password")]


def test_a_styled_wrapper_around_a_button_is_one_control(page):
    b = page('<div style="cursor:pointer"><div><button>Deep</button></div></div>')
    assert listed(b) == [("btn", "Deep")]


def test_a_clickable_row_with_its_own_button_lists_both(page):
    b = page('<div style="cursor:pointer;display:flex;gap:20px"><span>General</span><button aria-label="More">⋯</button></div>')
    assert listed(b) == [("btn", "General"), ("btn", "More")]


def test_a_transparent_control_is_marked_invisible(page):
    b = page('<div class=row>Row<button style="opacity:0">Delete</button></div>')
    n = next(n for n in b.dump() if n.label == "Delete")
    assert n.flags["invisible"] and "[invisible]" in tree.format_node(n)


def test_aria_hidden_content_behind_a_dialog_is_not_listed(page):
    b = page('<main aria-hidden=true><button>Behind</button></main><div role=dialog><button>Close</button></div>')
    assert listed(b) == [("btn", "Close")]


def test_a_fixed_bar_covers_what_scrolled_under_it(page):
    b = page(
        '<div style="position:fixed;top:0;left:0;right:0;height:60px;background:#fff;z-index:2"><button>Bar</button></div>'
        '<div style="height:200px"></div><button id=under style="height:40px">Under</button><div style="height:2000px"></div>'
    )
    b.cdp.call("Runtime.evaluate", expression="scrollTo(0, 190)")
    time.sleep(0.1)
    under = next(n for n in b.dump() if n.label == "Under")
    # The bar's own box is on top, and it is not a listed element itself.
    assert under.covered


def test_content_clipped_by_a_scroller_does_not_cover_the_header(page):
    b = page(
        '<header style="height:50px"><button>Title</button></header>'
        '<div id=s style="height:200px;overflow:auto"><div style="height:1000px">'
        + "".join(f"<p>line {i}</p>" for i in range(40))
        + "</div></div>"
    )
    b.cdp.call("Runtime.evaluate", expression="document.getElementById('s').scrollTop = 400")
    time.sleep(0.1)
    title = next(n for n in b.dump() if n.label == "Title")
    assert not title.covered


def test_rich_text_editor_is_one_field(page):
    b = page('<div contenteditable=true aria-label="Message"><p>hello</p><p>world</p></div>')
    assert [r for r, _ in listed(b)] == ["input"]


def test_text_reads_once_per_block(page):
    b = page("<p>Some <b>bold</b> and <i>italic</i> words</p>")
    assert listed(b) == [("text", "Some bold and italic words")]


def test_tap_and_type_through_device(page, browser):
    b = page(
        '<input id=q aria-label=Query><div id=out></div>'
        '<button onclick="document.getElementById(\'out\').textContent = \'got \' + document.getElementById(\'q\').value">Go</button>'
    )
    d = Device(browser, b)
    d.type("cheese", "Query")
    o = d.tap("Go")
    assert ("text", "got cheese") in [(n.role, n.label) for n in o.after]


def test_settle_waits_for_the_response_an_action_caused(page, browser):
    b = page(
        '<div id=out>idle</div>'
        "<button onclick=\"fetch('http://127.0.0.1:9/never').catch(()=>{}).finally(()=>setTimeout(()=>out.textContent='done',50))\">Load</button>"
    )
    o = Device(browser, b).tap("Load")
    assert ("text", "done") in [(n.role, n.label) for n in o.after]


def test_open_resolves_a_path_against_the_base_url(page, tmp_path, site):
    (tmp_path / "other.html").write_text("<p>Other page</p>")
    b = page("<p>first</p>")
    b.base = site
    b.open_uri("other.html", None)
    time.sleep(0.3)
    assert listed(b) == [("text", "Other page")]
