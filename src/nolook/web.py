"""A browser page, driven over the Chrome DevTools Protocol.

The browser is the long-lived process: `nolook web start` launches one with
a persistent profile and a debugging port, `nolook web attach` records one
the user already runs. Every other command connects, acts and disconnects.
"""

from __future__ import annotations

import contextlib
import glob
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import web_tree
from .backend import NolookError
from .config import Config
from .tree import Node

DEFAULT_PROFILE = "default"
DEVICES = {"desk": (1440, 900), "phone": (390, 844)}
KEYS = {
    "enter": ("Enter", 13, "\r"),
    "tab": ("Tab", 9, None),
    "escape": ("Escape", 27, None),
    "delete": ("Backspace", 8, None),
    "backspace": ("Backspace", 8, None),
    "space": (" ", 32, " "),
    "up": ("ArrowUp", 38, None),
    "down": ("ArrowDown", 40, None),
    "left": ("ArrowLeft", 37, None),
    "right": ("ArrowRight", 39, None),
    "pageup": ("PageUp", 33, None),
    "pagedown": ("PageDown", 34, None),
}
# A request older than this is a long poll or a stream, not something an
# action is waiting for.
REQUEST_PATIENCE = 4.0
NET_ERRORS = ("net::ERR_NETWORK_CHANGED", "net::ERR_CONNECTION_RESET", "net::ERR_CONNECTION_CLOSED", "net::ERR_TIMED_OUT")


# Where things live ------------------------------------------------------------


def web_settings(cfg: Config) -> dict:
    return cfg.web if cfg.web is not None else {}


def profile_name(cfg: Config) -> str:
    return os.environ.get("NOLOOK_WEB_PROFILE") or web_settings(cfg).get("profile") or DEFAULT_PROFILE


def profile_dir(name: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nolook" / "web" / name
    base.mkdir(parents=True, exist_ok=True)
    return base


def _endpoint_file(name: str) -> Path:
    return profile_dir(name) / "endpoint.json"


def read_endpoint(name: str) -> dict | None:
    try:
        ep = json.loads(_endpoint_file(name).read_text())
    except (OSError, ValueError):
        return None
    return ep


def _write_endpoint(name: str, ep: dict) -> None:
    _endpoint_file(name).write_text(json.dumps(ep))


def _alive(http: str) -> bool:
    try:
        urllib.request.urlopen(http + "/json/version", timeout=2).read()
        return True
    except OSError:
        return False


# The browser --------------------------------------------------------------------


def find_browser(cfg: Config) -> str:
    given = web_settings(cfg).get("browser") or os.environ.get("NOLOOK_BROWSER")
    if given:
        if not Path(given).exists() and not shutil.which(given):
            raise NolookError(f"browser not found: {given}")
        return shutil.which(given) or given
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if sys.platform == "darwin" and Path(mac).exists():
        return mac
    # Playwright's own build, if one was ever installed.
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / ".cache" / "ms-playwright"))
    found = sorted(glob.glob(str(cache / "chromium-*" / "chrome-*" / "chrome")))
    if found:
        return found[-1]
    raise NolookError("no Chrome or Chromium found; install one, or set browser in [web] of .nolook.toml")


def viewport(cfg: Config) -> tuple[int, int]:
    dev = str(web_settings(cfg).get("device", "desk"))
    if dev in DEVICES:
        return DEVICES[dev]
    try:
        w, h = (int(v) for v in dev.lower().split("x"))
        return w, h
    except ValueError:
        raise NolookError(f"device must be {' | '.join(DEVICES)} or WxH, not {dev!r}") from None


def start(cfg: Config, headed: bool = False, url: str | None = None) -> dict:
    name = profile_name(cfg)
    ep = read_endpoint(name)
    if ep and _alive(ep["http"]):
        return {**ep, "already": True}
    exe = find_browser(cfg)
    d = profile_dir(name)
    data = d / "chrome"
    data.mkdir(exist_ok=True)
    port_file = data / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    w, h = viewport(cfg)
    args = [
        exe,
        f"--user-data-dir={data}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        f"--window-size={w},{h}",
    ]
    if not headed:
        args.append("--headless=new")
    proxy = web_settings(cfg).get("proxy", "")
    if proxy:
        args.append(f"--proxy-server={_proxy_arg(proxy)}")
    args.append(url or web_settings(cfg).get("url") or "about:blank")
    log_path = d / "browser.log"
    log_start = log_path.stat().st_size if log_path.exists() else 0
    with open(log_path, "ab") as log:  # the browser keeps its own copy
        proc = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        port = _port(port_file, log_path, log_start)
        if port:
            http = f"http://127.0.0.1:{port}"
            if _alive(http):
                ep = {"http": http, "pid": proc.pid, "own": True, "headed": headed}
                _write_endpoint(name, ep)
                return ep
        if proc.poll() is not None:
            raise NolookError(f"the browser exited at start (code {proc.returncode}); see {d / 'browser.log'}")
        time.sleep(0.1)
    proc.kill()
    raise NolookError(f"the browser opened no debugging port in 15 s; see {d / 'browser.log'}")


def _port(port_file: Path, log_path: Path, log_start: int) -> str | None:
    # Chromium writes the port to DevToolsActivePort, and also prints it;
    # some sandboxes keep the file from appearing, so read either.
    try:
        lines = port_file.read_text().split()
        if lines:
            return lines[0]
    except OSError:
        pass
    try:
        with open(log_path, "rb") as f:
            f.seek(log_start)
            m = re.search(rb"DevTools listening on ws://[\d.]+:(\d+)/", f.read())
        return m.group(1).decode() if m else None
    except OSError:
        return None


def _proxy_arg(proxy: str) -> str:
    if proxy == "env":
        proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
        if not proxy:
            raise NolookError('proxy = "env" but https_proxy is not set')
    p = urlparse(proxy)
    if p.username:
        raise NolookError("the proxy needs a user name and password, which Chromium cannot take on its command line")
    return f"{p.scheme}://{p.hostname}:{p.port}" if p.port else proxy


def attach(cfg: Config, http: str) -> dict:
    http = http.rstrip("/")
    if not http.startswith("http"):
        http = "http://" + http
    if not _alive(http):
        raise NolookError(f"no browser answers at {http}; start Chrome with --remote-debugging-port")
    ep = {"http": http, "pid": None, "own": False}
    _write_endpoint(profile_name(cfg), ep)
    return ep


def stop(cfg: Config) -> str:
    name = profile_name(cfg)
    ep = read_endpoint(name)
    if not ep:
        return "no browser recorded for this profile"
    _endpoint_file(name).unlink(missing_ok=True)
    if not ep.get("own"):
        return f"detached from {ep['http']} (left running: nolook did not start it)"
    try:
        ver = json.loads(urllib.request.urlopen(ep["http"] + "/json/version", timeout=2).read())
        c = _Cdp(ver["webSocketDebuggerUrl"])
        c.call("Browser.close")
        c.close()
    except Exception:  # noqa: BLE001 - fall back to the process
        if ep.get("pid"):
            try:
                os.killpg(ep["pid"], signal.SIGTERM)
            except OSError:
                pass
    return "browser closed"


def status(cfg: Config) -> list[str]:
    name = profile_name(cfg)
    ep = read_endpoint(name)
    if not ep:
        return [f"profile {name}: no browser (nolook web start)"]
    if not _alive(ep["http"]):
        return [f"profile {name}: {ep['http']} no longer answers (nolook web start)"]
    lines = [f"profile {name}: {ep['http']}" + ("" if ep.get("own") else " (attached)")]
    for t in _pages(ep["http"]):
        mark = "*" if t["id"] == ep.get("target") else " "
        lines.append(f" {mark} {t['title'][:40]!r} {t['url']}")
    return lines


def _pages(http: str) -> list[dict]:
    return [t for t in json.loads(urllib.request.urlopen(http + "/json", timeout=3).read()) if t.get("type") == "page"]


# CDP ------------------------------------------------------------------------------


class _Cdp:
    def __init__(self, ws_url: str, timeout: float = 15):
        from websockets.sync.client import connect

        # Entered by hand: the connection outlives any one block here, and
        # websockets 17.1 warns about a connection used without entering it.
        self.ws = connect(ws_url, max_size=None, open_timeout=timeout).__enter__()
        self.ids = itertools.count(1)
        self.timeout = timeout
        self.events: list[dict] = []

    def call(self, method: str, timeout: float | None = None, **params) -> dict:
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise NolookError(f"the page did not answer {method} in time (a dialog may be open)")
            try:
                msg = json.loads(self.ws.recv(timeout=left))
            except TimeoutError:
                continue
            if msg.get("id") == i:
                if "error" in msg:
                    raise NolookError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def drain(self) -> None:
        """Take in the events that arrived since the last call."""
        while True:
            try:
                msg = json.loads(self.ws.recv(timeout=0))
            except TimeoutError:
                return
            if "method" in msg:
                self.events.append(msg)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ws.close()


# The backend ------------------------------------------------------------------------


class WebBackend:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.profile = profile_name(cfg)
        ep = read_endpoint(self.profile)
        if not ep or not _alive(ep["http"]):
            raise NolookError(f"no browser for profile {self.profile!r}: run `nolook web start` (or `nolook web attach <url>`)")
        self.ep = ep
        pages = _pages(ep["http"])
        if not pages:
            raise NolookError("the browser has no open page")
        page = next((p for p in pages if p["id"] == ep.get("target")), pages[0])
        if ep.get("target") != page["id"]:
            ep["target"] = page["id"]
            _write_endpoint(self.profile, ep)
        self.cdp = _Cdp(page["webSocketDebuggerUrl"])
        self.base = web_settings(cfg).get("url") or ""
        if not cfg.package:
            cfg.package = urlparse(self.base).netloc or None
        self.id = f"web-{self.profile}"
        # Requests and loads in flight, so settling waits for the response
        # an action caused, not just for two equal dumps.
        # Kept enabled so the tree follows a navigation at once, rather than
        # answering the first dump after it from the previous document.
        self.cdp.call("Accessibility.enable")
        self.cdp.call("Network.enable")
        self.cdp.call("Page.enable")
        self._requests: dict[str, float] = {}
        self._loading: set[str] = set()
        m = self.cdp.call("Page.getLayoutMetrics")
        vp = m.get("cssLayoutViewport") or m.get("layoutViewport")
        self.size = (int(vp["clientWidth"]), int(vp["clientHeight"]))

    # Reading ----------------------------------------------------------------

    def dump(self) -> list[Node]:
        snap = self.cdp.call(
            "DOMSnapshot.captureSnapshot",
            computedStyles=web_tree.STYLES,
            includePaintOrder=True,
            includeDOMRects=True,
        )
        ax = self.cdp.call("Accessibility.getFullAXTree")
        return web_tree.build(snap, ax.get("nodes", []), self.size)

    def pending(self) -> int:
        """Requests and page loads still in flight."""
        self.cdp.drain()
        for e in self.cdp.events:
            m, p = e["method"], e.get("params", {})
            if m == "Network.requestWillBeSent" and p.get("type") not in ("EventSource", "WebSocket", "Ping"):
                self._requests[p["requestId"]] = time.monotonic()
            elif m in ("Network.loadingFinished", "Network.loadingFailed"):
                self._requests.pop(p.get("requestId"), None)
            elif m == "Page.frameStartedLoading":
                self._loading.add(p["frameId"])
            elif m == "Page.frameStoppedLoading":
                self._loading.discard(p["frameId"])
        self.cdp.events.clear()
        now = time.monotonic()
        return len(self._loading) + sum(1 for t in self._requests.values() if now - t < REQUEST_PATIENCE)

    def url(self) -> str:
        r = self.cdp.call("Runtime.evaluate", expression="location.href", returnByValue=True, timeout=5)
        return r.get("result", {}).get("value", "")

    def interruption(self, package: str | None) -> str | None:
        try:
            href = self.url()
        except NolookError as e:
            return str(e)
        if href.startswith("chrome-error://"):
            return "the page failed to load (a browser error page is showing)"
        return None

    # Input ------------------------------------------------------------------

    def _mouse(self, kind: str, x: float, y: float, **extra) -> None:
        self.cdp.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, **extra)

    def click(self, x: int, y: int) -> None:
        self._mouse("mouseMoved", x, y)
        self._mouse("mousePressed", x, y, button="left", clickCount=1)
        self._mouse("mouseReleased", x, y, button="left", clickCount=1)

    def long_click(self, x: int, y: int) -> None:
        self._mouse("mouseMoved", x, y)
        self._mouse("mousePressed", x, y, button="left", clickCount=1)
        time.sleep(0.7)
        self._mouse("mouseReleased", x, y, button="left", clickCount=1)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float) -> None:
        # A finger moving up reveals what is below: wheel down by the same amount.
        self._mouse("mouseWheel", x1, y1, deltaX=x1 - x2, deltaY=y1 - y2)

    def _key(self, key: str, code: int, text: str | None, commands: list[str] | None = None) -> None:
        down = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": key, "windowsVirtualKeyCode": code}
        if text:
            down["text"] = text
        if commands:
            down["commands"] = commands
        self.cdp.call("Input.dispatchKeyEvent", **down)
        self.cdp.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=key, windowsVirtualKeyCode=code)

    def press(self, name: str) -> None:
        if name == "back":
            self.cdp.call("Runtime.evaluate", expression="history.back()")
            return
        if name == "home":
            if not self.base:
                raise NolookError("no home page: set url in [web] of .nolook.toml")
            self._navigate(self.base)
            return
        if name not in KEYS:
            raise NolookError(f"unknown key {name!r}; known: back, home, {', '.join(KEYS)}")
        self._key(*KEYS[name])

    def set_text(self, value: str) -> None:
        self._key("a", 65, None, commands=["selectAll"])
        if value:
            self.cdp.call("Input.insertText", text=value)
        else:
            self._key(*KEYS["delete"])

    def screenshot(self):
        import base64

        from PIL import Image

        r = self.cdp.call("Page.captureScreenshot", format="png")
        img = Image.open(BytesIO(base64.b64decode(r["data"])))
        if img.width != self.size[0]:
            # Bounds are CSS pixels; a high-density screen shoots more.
            img = img.resize(self.size)
        return img

    def _navigate(self, url: str) -> None:
        for attempt in range(2):
            r = self.cdp.call("Page.navigate", url=url, timeout=30)
            err = r.get("errorText")
            if not err:
                return
            if attempt == 0 and err in NET_ERRORS:
                time.sleep(1)
                continue
            raise NolookError(f"cannot open {url}: {err}")

    def open_uri(self, uri: str, package: str | None) -> None:
        if not urlparse(uri).scheme:
            if not self.base:
                raise NolookError(f"{uri!r} is a path, but no base: set url in [web] of .nolook.toml")
            uri = urljoin(self.base, uri)
        self._navigate(uri)

    def launcher(self, package: str) -> str:
        if not self.base:
            raise NolookError("no start page: set url in [web] of .nolook.toml")
        return self.base

    def stop(self, package: str) -> None:
        pass

    def start(self, launcher: str) -> None:
        self._navigate(launcher)

    # Logs (not yet: see docs/web-backend-plan.md, step 5) ---------------------

    def log(self, lines: int, grep: str | None, level: str | None, package: str | None) -> list[str]:
        raise NolookError("log is not available for web pages yet")

    def log_mark(self) -> str:
        return ""

    def log_since(self, mark: str) -> str:
        return ""
