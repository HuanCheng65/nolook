"""Turn a page's DOM snapshot and accessibility tree into the shared Node list.

The input is what two CDP calls return: `DOMSnapshot.captureSnapshot` (DOM,
layout, computed styles, paint order) and `Accessibility.getFullAXTree`,
joined by backend node id. Nothing here touches the page.

What is listed follows docs/web-backend.md, "What is listed":

- clickable = an interactive accessibility role, or a pointer cursor on an
  element whose parent has none and that has text outside any control;
- a name comes from the accessibility tree, then title / alt / placeholder,
  then a label inferred from the field's surroundings, then the element's
  own text;
- icon-font glyphs (Unicode private use areas) are dropped from all text;
- aria-hidden and inert subtrees, invisible boxes and nameless leaves are
  not listed; text is listed once per block, not once per inline run, and
  not at all when it is a listed control's label;
- a control that is laid out but transparent is listed as invisible (a
  button shown on hover, a file input laid over its button);
- covered = the topmost box at the centre is neither the node, inside it,
  nor one of its ancestors.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Only small array compares here; a thread pool per core costs more than it saves.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import numpy as np

from .tree import Node

STYLES = ["cursor", "opacity", "visibility", "overflow-x", "overflow-y", "pointer-events", "display", "position"]
_CURSOR, _OPACITY, _VISIBILITY, _OVX, _OVY, _PE, _DISPLAY, _POSITION = range(len(STYLES))

_PUA = re.compile("[-\U000f0000-\U000ffffd\U00100000-\U0010fffd]")
_WS = re.compile(r"\s+")

BTN_ROLES = {
    "button", "menuitem", "tab", "option", "treeitem", "gridcell", "combobox", "listbox",
    "PopUpButton", "DisclosureTriangle", "menuitemcheckbox", "menuitemradio", "slider",
}
# Roles whose name is a label next to them when the page gives none.
FIELD_ROLES = {"textbox", "searchbox", "spinbutton", "combobox", "listbox", "slider"}
CHECK_ROLES = {"checkbox", "radio", "menuitemcheckbox", "menuitemradio"}
INPUT_ROLES = {"textbox", "searchbox", "spinbutton"}
MAX_PARTS = 4
INVISIBLE_OPACITY = 0.05


def clean(s: str | None) -> str:
    if not s:
        return ""
    return _WS.sub(" ", _PUA.sub("", s)).strip()


@dataclass
class _Ax:
    role: str = ""
    name: str = ""
    props: dict = field(default_factory=dict)


def _ax_index(ax_nodes: list[dict]) -> dict[int, _Ax]:
    out: dict[int, _Ax] = {}
    for a in ax_nodes:
        bid = a.get("backendDOMNodeId")
        if bid is None or a.get("ignored"):
            continue
        role = (a.get("role") or {}).get("value", "")
        if role in ("StaticText", "InlineTextBox"):
            continue
        props = {p["name"]: (p.get("value") or {}).get("value") for p in a.get("properties", [])}
        out[bid] = _Ax(role, clean((a.get("name") or {}).get("value")), props)
    return out


def _rare(d: dict, key: str) -> set[int]:
    return set((d.get(key) or {}).get("index", []))


def _rare_str(d: dict, key: str, strings: list[str]) -> dict[int, str]:
    r = d.get(key) or {}
    return {i: strings[v] for i, v in zip(r.get("index", []), r.get("value", [])) if v >= 0}


def build(snapshot: dict, ax_nodes: list[dict], viewport: tuple[int, int]) -> list[Node]:
    strings: list[str] = snapshot["strings"]
    doc = snapshot["documents"][0]
    nd = doc["nodes"]
    lay = doc["layout"]
    count = len(nd["parentIndex"])
    parent = nd["parentIndex"]
    sx, sy = doc.get("scrollOffsetX", 0) or 0, doc.get("scrollOffsetY", 0) or 0
    vw, vh = viewport
    url = strings[doc["documentURL"]] if doc.get("documentURL", -1) >= 0 else ""
    host = urlparse(url).netloc or urlparse(url).scheme

    def s(i: int) -> str:
        return strings[i] if i >= 0 else ""

    names = [s(i) for i in nd["nodeName"]]
    types = nd["nodeType"]
    attrs: list[dict[str, str]] = []
    for a in nd["attributes"]:
        attrs.append({strings[a[k]]: strings[a[k + 1]] for k in range(0, len(a) - 1, 2)})
    input_value = _rare_str(nd, "inputValue", strings)
    checked = _rare(nd, "inputChecked")

    children: list[list[int]] = [[] for _ in range(count)]
    for i, p in enumerate(parent):
        if p >= 0:
            children[p].append(i)
    # Subtree ranges in document order (nodes come in pre-order).
    end = list(range(1, count + 1))
    for i in range(count - 1, -1, -1):
        p = parent[i]
        if p >= 0 and end[i] > end[p]:
            end[p] = end[i]

    box: dict[int, tuple[float, float, float, float]] = {}
    style: dict[int, list[str] | None] = {}
    text_of: dict[int, str] = {}
    scroll_size: dict[int, tuple[float, float]] = {}
    paint: dict[int, int] = {}
    for k, ni in enumerate(lay["nodeIndex"]):
        x, y, w, h = lay["bounds"][k]
        box[ni] = (x - sx, y - sy, x - sx + w, y - sy + h)
        st = [s(v) for v in lay["styles"][k]]
        # Text boxes carry no styles of their own; they read their parent's.
        style[ni] = st if len(st) == len(STYLES) else None
        if lay["text"][k] >= 0:
            text_of[ni] = strings[lay["text"][k]]
        if lay.get("scrollRects") and lay["scrollRects"][k]:
            _, _, sw, sh = lay["scrollRects"][k]
            scroll_size[ni] = (sw, sh)
        if lay.get("paintOrders"):
            paint[ni] = lay["paintOrders"][k]

    axi = _ax_index(ax_nodes)
    backend_ids = nd["backendNodeId"]

    def ax(i: int) -> _Ax | None:
        return axi.get(backend_ids[i])

    def edit_host(i: int) -> bool:
        # The outermost contenteditable element is one field; what is inside
        # it is that field's content.
        if attrs[i].get("contenteditable", "false") == "false":
            return False
        p = parent[i]
        while p >= 0:
            if attrs[p].get("contenteditable", "false") != "false":
                return False
            p = parent[p]
        return True

    def ax_kind(i: int) -> str | None:
        a = ax(i)
        if types[i] == 1 and edit_host(i):
            return "input"
        if a is None:
            return None
        if a.role in INPUT_ROLES or a.role == "combobox" and a.props.get("editable"):
            # Not every node marked editable: inside a rich-text field, its
            # paragraphs are too, and they are not fields of their own.
            return "input"
        if a.role == "switch":
            return "switch"
        if a.role in CHECK_ROLES:
            return "check"
        if a.role == "link":
            return "link"
        if a.role in BTN_ROLES:
            return "btn"
        return None

    # A pointer-cursor wrapper whose text is all inside real controls is
    # not a control of its own (a styled div around a button). One with
    # text of its own is (a clickable row that also holds a "more" button).
    loose = [False] * count
    for i in range(count - 1, -1, -1):
        if types[i] == 3:
            loose[i] = bool(clean(text_of.get(i, "")))
        p = parent[i]
        if p >= 0 and loose[i] and not (types[i] == 1 and ax_kind(i) is not None):
            loose[p] = True

    # What of each box is in view: clipped by the viewport and by every
    # ancestor that clips its overflow, except for fixed boxes, which only
    # the viewport clips. The listing and the covered check both use it.
    screen = (0.0, 0.0, float(vw), float(vh))
    inner_clip: list[tuple] = [screen] * count
    vis: dict[int, tuple] = {}
    for i in range(count):
        p = parent[i]
        clip = inner_clip[p] if p >= 0 else screen
        st = style.get(i)
        if st is not None and st[_POSITION] == "fixed":
            clip = screen
        b = box.get(i)
        if b is not None:
            v = (max(b[0], clip[0]), max(b[1], clip[1]), min(b[2], clip[2]), min(b[3], clip[3]))
            if v[2] - v[0] >= 1 and v[3] - v[1] >= 1:
                vis[i] = v
            if st is not None and (st[_OVX] != "visible" or st[_OVY] != "visible"):
                clip = (max(clip[0], b[0]), max(clip[1], b[1]), min(clip[2], b[2]), min(clip[3], b[3]))
        inner_clip[i] = clip

    # Elements that are some control's label: their text is that control's
    # name already, so it is not listed again as text.
    by_id = {a["id"]: i for i, a in enumerate(attrs) if a.get("id")}
    label_els: set[int] = set()
    for i, a in enumerate(attrs):
        for ref in a.get("aria-labelledby", "").split():
            if ref in by_id and by_id[ref] != i:
                label_els.add(by_id[ref])
        if names[i] == "LABEL" and (a.get("for") in by_id or any(names[j] in ("INPUT", "SELECT", "TEXTAREA") for j in range(i, end[i]))):
            label_els.add(i)

    out: list[Node] = []
    listed_at: dict[int, Node] = {}
    # Takes the text of a control that is not listed (hidden, off screen), so
    # an enclosing control does not take that text as its own name.
    sink = Node("btn", "", "-", "", "", "", (0, 0, 0, 0), {}, 0)

    def visible_rect(i: int) -> tuple | None:
        return vis.get(i)

    def own_text(i: int) -> str:
        if types[i] == 3:
            return clean(text_of.get(i, ""))
        return ""

    def inferred_label(i: int) -> str:
        # The nearest text before the field inside its own container, or its
        # container's: "Model [select]" laid out as two siblings.
        node, hops = i, 0
        while hops < 2 and parent[node] >= 0:
            p = parent[node]
            sibs = children[p]
            idx = sibs.index(node)
            for j in reversed(sibs[:idx]):
                t = subtree_text(j, limit=40)
                if t:
                    return t
            node, hops = p, hops + 1
        return ""

    def subtree_text(i: int, limit: int = 120) -> str:
        bits: list[str] = []
        for j in range(i, end[i]):
            if types[j] == 3 and j in text_of:
                t = clean(text_of[j])
                if t:
                    bits.append(t)
                    if sum(len(b) for b in bits) > limit:
                        break
        return " ".join(bits)

    def block_of(i: int) -> bool:
        st = style.get(i)
        return st is not None and not st[_DISPLAY].startswith("inline") and st[_DISPLAY] != "contents"

    def walk(i: int, opacity: float, depth: int, owner: Node | None, block: Node | None, pointer_above: bool, in_label: bool = False) -> None:
        if types[i] == 3:
            t = own_text(i)
            if not t:
                return
            st = style.get(i) or style.get(parent[i])
            if st and st[_VISIBILITY] != "visible":
                return
            if owner is not None:
                # A control's name does not depend on which part of it is in view.
                if not owner.desc and t not in owner.parts and len(owner.parts) < MAX_PARTS:
                    owner.parts.append(t)
                return
            if visible_rect(i) is None or in_label:
                return
            if block is not None:
                block.text = (block.text + " " + t).strip() if block.text else t
            return
        if types[i] != 1 and types[i] != 9 and types[i] != 11:
            for c in children[i]:
                walk(c, opacity, depth, owner, block, pointer_above, in_label)
            return
        a = attrs[i]
        if a.get("aria-hidden") == "true" or "inert" in a:
            return
        tag = names[i].lower()
        if tag in ("script", "style", "noscript", "template", "head"):
            return
        st = style.get(i)
        op = opacity
        pointer = False
        if st is not None:
            try:
                op *= float(st[_OPACITY] or 1)
            except ValueError:
                pass
            pointer = st[_CURSOR] == "pointer"
        rect = visible_rect(i)
        kind = ax_kind(i)
        if kind is None and pointer and not pointer_above and loose[i] and owner is None:
            kind = "btn"
        scrolls = False
        if st is not None and i in scroll_size and box.get(i):
            b = box[i]
            sw, sh = scroll_size[i]
            over = st[_OVX] in ("auto", "scroll") and sw > (b[2] - b[0]) + 1 or st[_OVY] in ("auto", "scroll") and sh > (b[3] - b[1]) + 1
            scrolls = bool(over) and tag not in ("html", "body")
        node: Node | None = None
        hidden = st is not None and st[_VISIBILITY] != "visible"
        if rect is not None and not hidden and (owner is None or kind is not None):
            role = kind or ("scroll" if scrolls else None)
            axn = ax(i)
            if role is None and axn is not None and axn.role in ("image", "img") and axn.name:
                role = "img"
            if role is not None:
                value = input_value.get(i, "") or (subtree_text(i, limit=200) if role == "input" else "")
                node = _node(i, role, rect, depth, host, tag, a, axn, value, i in checked)
                if role in ("btn", "link", "input", "check", "switch") and not node.parts:
                    for key in ("title", "alt", "placeholder", "aria-label"):
                        v = clean(a.get(key))
                        if v:
                            node.parts.append(v)
                            break
                if (axn is not None and axn.role in FIELD_ROLES or role == "input") and not axn_named(axn):
                    lab = inferred_label(i)
                    if lab:
                        node.parts.insert(0, lab)
                        node.flags["inferred"] = True
                if op < INVISIBLE_OPACITY and role != "scroll":
                    node.flags["invisible"] = True
                node.raw, node.raw_end = i, end[i]
                out.append(node)
                listed_at[i] = node
        new_block = None if node is not None and node.role == "scroll" else block
        if node is None and owner is None and rect is not None and not hidden and block_of(i):
            # Text directly in this block reads as one line.
            nb = _node(i, "text", rect, depth, host, tag, a, None, "", False)
            nb.raw, nb.raw_end = i, end[i]
            out.append(nb)
            listed_at[i] = nb
            new_block = nb
        child_owner = owner
        if node is not None and node.role in ("btn", "link", "check", "switch", "input"):
            # A rich-text field's paragraphs are its value, not page text.
            child_owner = node
        elif node is None and kind is not None and kind != "input":
            child_owner = sink
        child_depth = depth + (1 if node is not None and node.role == "scroll" else 0)
        for c in children[i]:
            walk(c, op, child_depth, child_owner, new_block, pointer_above or pointer, in_label or i in label_els)
        if node is not None:
            node.end = len(out)

    root = next((i for i in range(count) if parent[i] < 0), 0)
    walk(root, 1.0, 0, None, None, False)

    # Blocks keep their text; an empty block is gone, and a block whose text
    # is all in one listed child adds nothing.
    kept: list[Node] = []
    for n in out:
        if n.role == "text":
            if not n.text:
                continue
            n.parts = [n.text]
        kept.append(n)
    _mark_covered(kept, vis, paint, style, parent)
    for idx, n in enumerate(kept):
        j = idx + 1
        while j < len(kept) and kept[j].raw < n.raw_end:
            j += 1
        n.end = j
    return kept


def axn_named(a: _Ax | None) -> bool:
    return bool(a and a.name)


def _node(i: int, role: str, rect: tuple, depth: int, host: str, tag: str, a: dict, axn: _Ax | None, value: str, checked: bool) -> Node:
    name = axn.name if axn else ""
    props = axn.props if axn else {}
    l, t, r, b = (round(v) for v in rect)
    n = Node(
        role=role,
        text=value if role == "input" else "",
        desc=name,
        rid=a.get("data-testid") or a.get("data-test") or a.get("id", ""),
        cls=tag,
        pkg=host,
        bounds=(l, t, r, b),
        flags={
            "checked": checked or props.get("checked") in ("true", "mixed", True),
            "focused": bool(props.get("focused")),
            "selected": bool(props.get("selected")),
            "enabled": not props.get("disabled"),
        },
        depth=depth,
        parts=[name] if name and role != "input" else ([clean(value)] if role == "input" and value else []),
    )
    if role == "input" and name and name not in n.parts:
        n.parts.append(name)
    return n


def _mark_covered(nodes: list[Node], vis: dict, paint: dict, style: dict, parent: list[int]) -> None:
    if not paint:
        return
    idx = [i for i in vis if i in paint and style.get(i) and style[i][_PE] != "none" and style[i][_VISIBILITY] == "visible"]
    if not idx:
        return
    arr = np.array([vis[i] for i in idx], dtype=float)
    order = np.array([paint[i] for i in idx])
    owner_of = np.array(idx)
    by_raw = {n.raw: n for n in nodes}
    for n in nodes:
        if n.role in ("text", "img", "scroll"):
            continue
        cx, cy = n.center
        hit = (arr[:, 0] <= cx) & (cx < arr[:, 2]) & (arr[:, 1] <= cy) & (cy < arr[:, 3])
        if not hit.any():
            continue
        top = int(owner_of[np.flatnonzero(hit)[np.argmax(order[hit])]])
        if n.raw <= top < n.raw_end:
            continue
        # An ancestor on top means the node itself takes no pointer events:
        # the click still reaches the control's own handler region.
        a = parent[n.raw]
        while a >= 0 and a != top:
            a = parent[a]
        if a == top:
            continue
        n.covered = True
        c = top
        while c >= 0 and c not in by_raw:
            c = parent[c]
        n.covered_by = by_raw.get(c)
