"""`nolook doctor`: check what nolook needs, and say how to fix what is missing.

With the web backend it checks the browser side instead of adb and the device.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import time
import urllib.error
import urllib.request

from .config import Config

OK, WARN, FAIL = "✓", "!", "✗"


def run(cfg: Config) -> int:
    failed = 0

    def line(mark: str, what: str, fix: str = "") -> None:
        nonlocal failed
        failed += mark == FAIL
        print(f"{mark} {what}" + (f"\n    → {fix}" if fix else ""))

    cfg_file = cfg.root / ".nolook.toml"
    if cfg_file.is_file():
        line(OK, f"config: {cfg_file}")
    else:
        line(WARN, "config: no .nolook.toml here or above", "optional; add one to pick the device, app and flows directory")

    if cfg.web is not None:
        _web(cfg, line)
        print("all good" if not failed else f"{failed} problem(s) to fix")
        return 1 if failed else 0

    adb = shutil.which("adb")
    if adb:
        line(OK, f"adb: {adb}")
    else:
        line(WARN, "adb: not on PATH", "install Android platform-tools and add them to PATH (nolook can still use a running adb server)")

    port = int(os.environ.get("ANDROID_ADB_SERVER_PORT", cfg.adb_port or 5037))
    # Look before asking adbutils: it starts a server on a port that has none,
    # and a check should not change what it checks.
    devices = None
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        line(FAIL, f"adb server on port {port}: not running", "start it with `adb start-server`, or set adb_port / NOLOOK_ADB_PORT")
    else:
        try:
            import adbutils

            devices = adbutils.AdbClient(port=port).device_list()
        except Exception as e:  # noqa: BLE001 - every failure here means the same thing to the user
            line(FAIL, f"adb server on port {port}: not answering ({type(e).__name__})", "restart it: adb kill-server && adb start-server")
    if devices is not None:
        if not devices:
            line(FAIL, f"devices on port {port}: none", "connect a device with USB debugging on, or start an emulator")
        else:
            serials = [d.serial for d in devices]
            line(OK, f"devices on port {port}: {', '.join(serials)}")
            if cfg.serial and cfg.serial not in serials:
                line(FAIL, f"configured serial {cfg.serial} is not connected", "fix serial in .nolook.toml or NOLOOK_SERIAL")
            elif not cfg.serial and len(serials) > 1:
                line(WARN, "several devices and no serial chosen", "set serial in .nolook.toml, NOLOOK_SERIAL, or pass --serial")

    if devices:
        try:
            from .device import Device

            t = time.monotonic()
            d = Device(cfg)
            connect = time.monotonic() - t
            t = time.monotonic()
            ns = d.nodes()
            dump = time.monotonic() - t
            line(OK, f"device server on {d.serial}: connected in {connect:.1f}s, tree read in {dump:.2f}s ({len(ns)} elements)")
            front = {n.pkg for n in ns}
            if cfg.package and cfg.package in front and not any(n.rid for n in ns if n.pkg == cfg.package):
                line(
                    WARN,
                    f"{cfg.package} shows no ids",
                    "Compose: set Modifier.semantics { testTagsAsResourceId = true } near the root so testTags become #ids",
                )
        except Exception as e:  # noqa: BLE001
            line(FAIL, f"device server: {e}", "check the device is unlocked and authorised for debugging (adb devices shows 'device')")

    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        line(OK, "ffmpeg: found (for motion)")
    else:
        line(WARN, "ffmpeg: not found", "needed only for `nolook motion`; install ffmpeg")

    if importlib.util.find_spec("perfetto"):
        line(OK, "perfetto: installed (for trace; its trace processor downloads on first use)")
    else:
        line(WARN, "perfetto: not installed", "needed only for `nolook trace`; reinstall nolook or pip install perfetto")

    print("all good" if not failed else f"{failed} problem(s) to fix")
    return 1 if failed else 0


def _web(cfg: Config, line) -> None:
    """The web backend: its library, a browser, the site, and the browser for this profile."""
    from . import web

    if importlib.util.find_spec("websockets"):
        line(OK, "websockets: installed")
    else:
        # An editable install keeps the dependencies it was installed with
        line(FAIL, "websockets: not installed", "reinstall nolook (uv tool install --reinstall nolook) or pip install websockets")
        return

    try:
        line(OK, f"browser: {web.find_browser(cfg)}")
    except Exception as e:  # noqa: BLE001
        line(FAIL, f"browser: {e}")

    url = web.web_settings(cfg).get("url")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                line(OK, f"site {url}: answers ({r.status})")
        except urllib.error.HTTPError as e:
            # It answers; what a page returns is the site's business
            line(OK, f"site {url}: answers ({e.code})")
        except Exception as e:  # noqa: BLE001
            line(FAIL, f"site {url}: not answering ({getattr(e, 'reason', None) or type(e).__name__})", "start the dev server, or fix url in [web]")
    else:
        line(WARN, "site: no url in [web]", "optional; set url in .nolook.toml so relative opens and `nolook web start` know the site")

    name = web.profile_name(cfg)
    ep = web.read_endpoint(name)
    if not ep or not web._alive(ep["http"]):
        line(WARN, f"browser for profile {name}: not running", "nolook web start (or nolook web attach <url>)")
        return
    try:
        from .device import Device

        t = time.monotonic()
        d = Device(cfg)
        connect = time.monotonic() - t
        t = time.monotonic()
        ns = d.nodes()
        dump = time.monotonic() - t
        line(OK, f"browser for profile {name}: {ep['http']}, connected in {connect:.1f}s, page read in {dump:.2f}s ({len(ns)} elements)")
    except Exception as e:  # noqa: BLE001
        line(FAIL, f"browser for profile {name}: {e}", "nolook web stop && nolook web start")
