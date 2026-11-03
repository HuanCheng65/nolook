"""Parse a uiautomator hierarchy into a short, flat list an agent can read.

Only nodes worth acting on or reading are listed: interactive nodes, and
nodes with text that no interactive ancestor already shows. A clickable node
without a label of its own takes its descendants' texts, so a list row reads
as one line instead of five.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

LABEL_MAX = 48
JOIN = " · "

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Node:
    role: str
    text: str
    desc: str
    rid: str
    cls: str
    pkg: str
    bounds: tuple[int, int, int, int]
    flags: dict[str, bool]
    depth: int  # scrollable ancestors, for indentation
    parts: list[str] = field(default_factory=list)  # own label, or absorbed texts
    ref: str | None = None
    end: int = 0  # index past this node's listed descendants
    covered: bool = False
    covered_by: Node | None = None
    raw: int = 0  # index in draw order among all visible elements
    raw_end: int = 0  # past this element's descendants, in the same order
    scroller: list | None = field(default=None, repr=False)  # raw slot of the nearest scrolling ancestor

    @property
    def label(self) -> str:
        return JOIN.join(self.parts)

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return (l + r) // 2, (t + b) // 2

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.role, self.label, self.rid)


def _short_id(rid: str) -> str:
    # View ids come as "com.pkg:id/name"; Compose test tags come bare.
    return rid.split(":id/", 1)[1] if ":id/" in rid else rid


def _role(a: dict[str, str]) -> str:
    cls = a.get("class", "")
    if "EditText" in cls or a.get("password") == "true":
        return "input"
    if a.get("checkable") == "true":
        return "switch" if "Switch" in cls else "check"
    if a.get("scrollable") == "true":
        return "scroll"
    if a.get("clickable") == "true" or a.get("long-clickable") == "true":
        return "btn"
    if "Image" in cls:
        return "img"
    return "text"


def parse(xml: str, screen: tuple[int, int]) -> list[Node]:
    root = ET.fromstring(xml)
    sw, sh = screen
    out: list[Node] = []
    raw: list[list] = []  # [bounds, subtree end, clickable] of every visible element, in draw order

    def visible(b: tuple[int, int, int, int]) -> bool:
        l, t, r, bt = b
        return r > l and bt > t and r > 0 and bt > 0 and l < sw and t < sh

    def walk(el: ET.Element, depth: int, owner: Node | None, scroller: list | None) -> None:
        a = el.attrib
        m = _BOUNDS.match(a.get("bounds", ""))
        node = None
        slot = None
        if m and a.get("visible-to-user", "true") == "true":
            b = tuple(int(x) for x in m.groups())
            if visible(b):
                slot = [b, 0, a.get("clickable") == "true"]
                raw.append(slot)
                if a.get("scrollable") == "true":
                    scroller = slot
                role = _role(a)
                own = (a.get("text") or a.get("content-desc") or "").strip()
                interactive = role != "text" and role != "img"
                rid = _short_id(a.get("resource-id", ""))
                if not interactive and not own and rid:
                    # An id-only container: not listed, but a flow can scope to it.
                    role = "box"
                if owner is not None and not interactive and role != "box":
                    # Text inside a button belongs to the button.
                    if own and own not in owner.parts and len(owner.parts) < 4:
                        owner.parts.append(own)
                elif interactive or own or role == "box":
                    node = Node(
                        role=role,
                        text=a.get("text", ""),
                        desc=a.get("content-desc", ""),
                        rid=_short_id(a.get("resource-id", "")),
                        cls=a.get("class", "").rsplit(".", 1)[-1],
                        pkg=a.get("package", ""),
                        bounds=b,
                        flags={
                            k: a.get(k) == "true"
                            for k in ("checked", "focused", "selected", "enabled")
                        },
                        depth=depth,
                        parts=[own] if own else [],
                    )
                    desc = a.get("content-desc", "").strip()
                    if desc and desc not in node.parts:
                        node.parts.append(desc)
                    node.raw = len(raw) - 1
                    node.scroller = scroller if scroller is not slot else None
                    out.append(node)
        child_owner = owner
        if node is not None and node.role in ("btn", "check", "switch"):
            child_owner = node
        elif node is not None and node.role == "scroll":
            child_owner = None
        child_depth = depth + (1 if node is not None and node.role == "scroll" else 0)
        for c in el:
            walk(c, child_depth, child_owner, scroller)
        if node is not None:
            node.end = len(out)
        if slot is not None:
            slot[1] = len(raw)
            if node is not None:
                node.raw_end = slot[1]

    walk(root, 0, None, None)
    _mark_covered(out, raw, sw * sh)
    return out


def _mark_covered(nodes: list[Node], raw: list[list], screen_area: int) -> None:
    # Something drawn later that holds this node's centre sits on top of it,
    # and a tap there lands on that instead. Which later things count:
    #
    # - for a node in a scrolling list, anything outside that list: floating
    #   bars over scrolled content are the case this exists for. Overlaps
    #   within the list (a card's decorations) are left alone.
    # - for any other node, only something clickable.
    #
    # Inside those, it errs towards "covered": refusing a tap is cheap, a tap
    # that lands elsewhere is not. A large container that takes no clicks (a
    # pane title, a layout root) never counts; a scrim that blocks taps is
    # clickable.
    listed = {n.raw: n for n in nodes}
    for n in nodes:
        if n.role in ("scroll", "text", "img", "box"):
            continue
        cx, cy = n.center
        first = raw[n.raw][1]
        if n.scroller is not None:
            first = max(first, n.scroller[1])
        for j in range(first, len(raw)):
            (l, t, r, b), _, clickable = raw[j]
            if n.scroller is None and not clickable:
                continue
            if not clickable and (r - l) * (b - t) > screen_area / 2:
                continue
            if l <= cx < r and t <= cy < b:
                n.covered = True
                n.covered_by = listed.get(j)
                break


def listed(nodes: list[Node]) -> list[Node]:
    """The nodes a listing shows: all but id-only containers."""
    return [n for n in nodes if n.role != "box"]


def within(nodes: list[Node], scope: Node) -> list[Node]:
    """The nodes drawn inside scope's subtree."""
    return [n for n in nodes if scope.raw < n.raw < scope.raw_end]


def format_node(n: Node, compact: bool = False) -> str:
    label = n.label
    if len(label) > LABEL_MAX:
        label = label[: LABEL_MAX - 1] + "…"
    bits = [n.ref or "", n.role] if compact else [f"{n.ref or '':<5}", f"{n.role:<6}"]
    if label:
        bits.append(f'"{label}"')
    if n.rid:
        bits.append(f"#{n.rid}")
    for flag in ("checked", "focused", "selected"):
        if n.flags.get(flag):
            bits.append(f"[{flag}]")
    if not n.flags.get("enabled", True):
        bits.append("[disabled]")
    if n.covered:
        by = n.covered_by
        bits.append(f"[covered by {by.ref}]" if by is not None else "[covered]")
    bits.append("(%d,%d)" % n.center)
    return " ".join(bits) if compact else "  " * n.depth + " ".join(bits)


# Refs -----------------------------------------------------------------------
#
# A ref names an element, not a position: it survives a scroll that keeps the
# element on screen, and is gone once the element is. Each dump re-assigns refs
# against the previous dump, so the same element keeps the same ref and a ref
# that no longer resolves is reported as stale instead of tapping whatever now
# sits at the old coordinates.


def assign_refs(nodes: list[Node], prev: list[dict], counter: int) -> int:
    by_key: dict[tuple, list[dict]] = {}
    for p in prev:
        by_key.setdefault(tuple(p["key"]), []).append(p)
    # On a different screen a same-named element is a different element:
    # carrying its ref over would let an old ref tap the new one.
    shared = sum(1 for n in nodes if n.key in by_key)
    if shared < 0.3 * max(len(prev), 1):
        by_key = {}
    taken: set[str] = set()
    for n in nodes:
        cands = [p for p in by_key.get(n.key, []) if p["ref"] not in taken]
        pick = None
        if len(cands) == 1:
            pick = cands[0]
        elif cands:
            # Same label twice (list rows): keep the one that did not move,
            # else the nearest.
            cx, cy = n.center
            pick = min(cands, key=lambda p: abs(p["center"][0] - cx) + abs(p["center"][1] - cy))
        if pick is not None:
            n.ref = pick["ref"]
            taken.add(pick["ref"])
    for n in nodes:
        if n.ref is None:
            counter += 1
            n.ref = f"@e{counter}"
    return counter


def to_state(nodes: list[Node]) -> list[dict]:
    return [{"ref": n.ref, "key": list(n.key), "center": list(n.center)} for n in nodes]


# Queries --------------------------------------------------------------------
#
#   @e12         a ref from the last output
#   #reply       resource id / Compose test tag
#   ~回复         label contains
#   /^回复\d+$/   label matches the regex
#   回复          text, description or one part of the label, exactly
#   btn:回复      any of the above, limited to one role


class QueryError(Exception):
    pass


class NoMatch(QueryError):
    """Nothing matches; it may still appear."""


class Ambiguous(QueryError):
    """Several match; waiting will not make it one."""


ROLES = ("btn", "input", "text", "img", "scroll", "check", "switch", "box")


def match(nodes: list[Node], q: str) -> list[Node]:
    role = None
    head, sep, rest = q.partition(":")
    if sep and head in ROLES and rest:
        role, q = head, rest
    if q.startswith("@e"):
        hits = [n for n in nodes if n.ref == q]
    elif q.startswith("#"):
        hits = [n for n in nodes if n.rid == q[1:]]
    elif q.startswith("~"):
        hits = [n for n in nodes if q[1:] in n.label]
    elif len(q) > 2 and q.startswith("/") and q.endswith("/"):
        rx = re.compile(q[1:-1])
        hits = [n for n in nodes if rx.search(n.label)]
    else:
        hits = [n for n in nodes if q in (n.text, n.desc) or q in n.parts]
    if role:
        hits = [n for n in hits if n.role == role]
    return hits


def near_misses(nodes: list[Node], q: str, limit: int = 5) -> list[Node]:
    import difflib

    needle = q.split(":", 1)[-1] if q.split(":", 1)[0] in ROLES else q
    needle = needle.lstrip("~").strip("/")
    if not needle:
        return []
    scored = []
    for n in nodes:
        cands = n.parts + [n.text, n.desc]
        best = max((difflib.SequenceMatcher(None, needle, c).ratio() for c in cands if c), default=0)
        if needle in n.label:
            best = max(best, 0.9)
        if best >= 0.4:
            scored.append((best, n))
    scored.sort(key=lambda s: -s[0])
    return [n for _, n in scored[:limit]]


def resolve(nodes: list[Node], q: str, nth: int | None = None) -> Node:
    hits = match(nodes, q)
    if nth is not None:
        if not 1 <= nth <= len(hits):
            raise NoMatch(f"{q!r} matched {len(hits)}, no #{nth}")
        return hits[nth - 1]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        if q.startswith("@e"):
            raise NoMatch(f"{q} is stale: that element is no longer on screen. Run `nolook snap`.")
        msg = f"nothing matches {q!r}"
        if q.startswith("#"):
            ids = sorted({n.rid for n in nodes if n.rid})
            if ids:
                msg += "\nids on screen: " + " ".join("#" + i for i in ids[:30])
            else:
                msg += "\nno element on screen has an id (Compose: set testTagsAsResourceId)"
        else:
            near = near_misses(nodes, q)
            if near:
                msg += "\nclosest:\n" + "\n".join("  " + format_node(n, compact=True) for n in near)
        raise NoMatch(msg)
    lines = "\n".join("  " + format_node(n, compact=True) for n in hits[:8])
    raise Ambiguous(f"{q!r} matched {len(hits)}; use a ref, add a role (btn:…), or --nth:\n{lines}")


# Deltas ---------------------------------------------------------------------


def delta(before: list[Node], after: list[Node], limit: int = 20) -> tuple[list[str], bool]:
    """Lines describing what changed, and whether the screen changed wholesale."""
    before = listed(before)
    after = listed(after)
    old = {n.ref for n in before}
    new = {n.ref for n in after}
    added = [n for n in after if n.ref not in old]
    removed = [n for n in before if n.ref not in new]
    kept = len(new & old)
    wholesale = not before or kept < 0.3 * max(len(before), 1)
    lines = []
    for sign, group in (("+", added), ("-", removed)):
        for n in group[:limit]:
            lines.append(f"{sign} {format_node(n, compact=True)}")
        if len(group) > limit:
            lines.append(f"{sign} … {len(group) - limit} more")
    return lines, wholesale
