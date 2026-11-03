"""Project settings from the nearest .nolook.toml, with env overrides.

    serial = "emulator-5556"
    adb_port = 5038
    package = "oolong.client"
    flows = "tools/flows"
    guard = ["opAgree", "add_post"]   # logcat regexes flagged after each action
    danger = ["赞", "关注", "发送"]      # labels a tap refuses without --confirm
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    root: Path
    serial: str | None = None
    adb_port: int | None = None
    package: str | None = None
    flows: str = "flows"
    guard: list[str] = field(default_factory=list)
    hide: list[str] = field(default_factory=lambda: ["com.android.systemui"])
    # Taps on these refuse without confirm: the app may be logged in to a
    # real account, and one wrong tap posts, likes or deletes.
    danger: list[str] = field(
        default_factory=lambda: [
            "赞", "踩", "关注", "取消关注", "发送", "发布", "回复", "删除", "收藏", "一键签到", "签到", "举报", "拉黑", "屏蔽",
            "like", "follow", "send", "post", "delete", "submit", "buy", "pay",
        ]
    )

    @property
    def flows_dir(self) -> Path:
        return self.root / self.flows


def find_config(start: Path | None = None) -> Config:
    here = (start or Path.cwd()).resolve()
    data: dict = {}
    root = here
    for d in (here, *here.parents):
        f = d / ".nolook.toml"
        if f.is_file():
            data = tomllib.loads(f.read_text())
            root = d
            break
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__ and k != "root"}
    cfg = Config(root=root, **known)
    cfg.serial = os.environ.get("NOLOOK_SERIAL", cfg.serial)
    port = os.environ.get("NOLOOK_ADB_PORT")
    if port:
        cfg.adb_port = int(port)
    if cfg.adb_port:
        # adbutils reads this when it first connects.
        os.environ["ANDROID_ADB_SERVER_PORT"] = str(cfg.adb_port)
    return cfg


def cache_dir(serial: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nolook"
    d = base / serial.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nolook" / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def follow_session(cfg: Config, session: str) -> bool:
    """Point cfg at the device a Claude Code session last drove; False if none is recorded."""
    import json

    try:
        rec = json.loads((session_dir() / f"{session}.json").read_text())
    except (OSError, ValueError):
        return False
    cfg.serial = rec.get("serial") or cfg.serial
    if rec.get("adb_port"):
        cfg.adb_port = int(rec["adb_port"])
        os.environ["ANDROID_ADB_SERVER_PORT"] = str(cfg.adb_port)
    return True
