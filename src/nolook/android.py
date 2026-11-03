"""An Android device or emulator, driven through uiautomator2 and adb shell."""

from __future__ import annotations

import re
import time

from . import tree
from .backend import NolookError
from .tree import Node


class AndroidBackend:
    def __init__(self, serial: str | None):
        import uiautomator2 as u2  # imported late: --help should not pay for it

        try:
            self.u2 = u2.connect(serial) if serial else u2.connect()
        except Exception as e:  # noqa: BLE001 - u2 raises many kinds
            raise NolookError(f"cannot connect to {serial or 'the default device'}: {e}") from e
        _retry_stale_connections(self.u2)
        self.id = self.u2.serial
        self.size = self.u2.window_size()

    # Reading ----------------------------------------------------------------

    def dump(self) -> list[Node]:
        # A dump can fail while the app is starting or a window is changing.
        for attempt in range(3):
            try:
                xml = self.u2.dump_hierarchy()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise NolookError(f"cannot read the UI tree: {type(e).__name__}: {e}") from e
                time.sleep(0.4)
        return tree.parse(xml, self.size)

    def interruption(self, package: str | None) -> str | None:
        power = self.shell(["dumpsys", "power"])
        m = re.search(r"mWakefulness=(\w+)", power)
        if m and m.group(1) != "Awake":
            return f"the screen is off ({m.group(1).lower()})"
        locked = r"(isKeyguardShowing|mShowingLockscreen|mKeyguardShowing|isStatusBarKeyguard)=true"
        if re.search(locked, self.shell(["dumpsys", "window"])):
            return "the device is locked"
        # Some ROMs report the lock screen only in KeyguardController's section here.
        acts = self.shell(["dumpsys", "activity", "activities"])
        if re.search(r"(mKeyguardShowing|keyguardShowing)=true", acts):
            return "the device is locked"
        if package:
            m = re.search(r"(?:topResumedActivity|mResumedActivity)[=:] ?ActivityRecord\{\S+ \S+ ([\w.]+)/", acts)
            if m and m.group(1) != package:
                return f"{m.group(1)} is in front, not {package}"
        return None

    # Input ------------------------------------------------------------------

    def click(self, x: int, y: int) -> None:
        self.u2.click(x, y)

    def long_click(self, x: int, y: int) -> None:
        self.u2.long_click(x, y)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float) -> None:
        self.u2.swipe(x1, y1, x2, y2, duration=duration)

    def press(self, name: str) -> None:
        self.u2.press(name)

    def set_text(self, value: str) -> None:
        self.u2(focused=True).set_text(value)

    def screenshot(self):
        return self.u2.screenshot()

    # Apps -------------------------------------------------------------------

    def open_uri(self, uri: str, package: str | None) -> None:
        cmd = ["am", "start", "-a", "android.intent.action.VIEW", "-d", uri]
        if package:
            cmd += ["-p", package]
        self.shell(cmd)

    def launcher(self, package: str) -> str:
        comp = self.shell(["cmd", "package", "resolve-activity", "--brief", package]).strip().splitlines()[-1]
        if "/" not in comp:
            raise NolookError(f"{package} has no launcher activity")
        return comp

    def stop(self, package: str) -> None:
        self.shell(["am", "force-stop", package])

    def start(self, launcher: str) -> None:
        self.shell(["am", "start", "-n", launcher])

    # Logs -------------------------------------------------------------------

    def log(self, lines: int, grep: str | None, level: str | None, package: str | None) -> list[str]:
        cmd = ["logcat", "-d", "-t", str(max(lines * 20, 500) if grep else lines)]
        pid = self.pid(package) if package else None
        if pid:
            cmd.append(f"--pid={pid}")
        if level:
            cmd.append(f"*:{level.upper()}")
        out = self.shell(cmd).splitlines()
        if grep:
            rx = re.compile(grep)
            out = [ln for ln in out if rx.search(ln)]
        return out[-lines:]

    def log_mark(self) -> str:
        # logcat -T takes epoch seconds; a second of slack beats a missed line.
        return f"{int(self.shell(['date', '+%s']).strip()) - 1}.000"

    def log_since(self, mark: str) -> str:
        return self.shell(["logcat", "-d", "-T", mark])

    # Android only -------------------------------------------------------------

    def pid(self, package: str) -> str | None:
        return self.shell(["pidof", package]).strip().split(" ")[0] or None

    def shell(self, cmd: list[str], timeout: int = 60) -> str:
        # The connection to the device server drops now and then; one retry covers it.
        try:
            return self.u2.shell(cmd, timeout=timeout).output
        except (ConnectionError, OSError):
            time.sleep(0.5)
            return self.u2.shell(cmd, timeout=timeout).output

    def shell_stream(self, cmd: list[str]):
        """A shell command left running; the caller closes the returned connection."""
        return self.u2._dev.shell(cmd, stream=True)

    def pull(self, remote: str, local: str) -> None:
        self.u2.pull(remote, local)


def _retry_stale_connections(dev) -> None:
    # The device server closes idle keep-alive connections; the next request
    # then fails with RemoteDisconnected before the server saw it, so sending
    # it once more is safe.
    import http.client

    from uiautomator2.core import BasicUiautomatorServer

    if getattr(BasicUiautomatorServer, "_nolook_retry", False):
        return
    orig = BasicUiautomatorServer.jsonrpc_call

    def call(self, *args, **kwargs):
        try:
            return orig(self, *args, **kwargs)
        except http.client.RemoteDisconnected:
            return orig(self, *args, **kwargs)

    BasicUiautomatorServer.jsonrpc_call = call
    BasicUiautomatorServer._nolook_retry = True
