from nolook import tree

SCREEN = (1080, 2400)


def node(bounds, cls="android.view.View", text="", desc="", rid="", clickable=False, scrollable=False, children="", **flags):
    attrs = {
        "class": cls,
        "text": text,
        "content-desc": desc,
        "resource-id": rid,
        "package": "app",
        "clickable": str(clickable).lower(),
        "scrollable": str(scrollable).lower(),
        "bounds": "[%d,%d][%d,%d]" % bounds,
        **{k: str(v).lower() for k, v in flags.items()},
    }
    a = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return f"<node {a}>{children}</node>"


def screen(*children):
    return f"<hierarchy>{node((0, 0, 1080, 2400), children=''.join(children))}</hierarchy>"


LIST = node(
    (0, 0, 1080, 2400),
    scrollable=True,
    rid="list",
    children=node((0, 100, 1080, 300), clickable=True, children=node((20, 120, 500, 180), text="标题一") + node((20, 200, 300, 260), text="3 回复"))
    + node((0, 300, 1080, 500), clickable=True, children=node((20, 320, 500, 380), text="标题二"))
    + node((0, 2200, 1080, 2380), clickable=True, children=node((20, 2220, 500, 2280), text="被盖住")),
)
BAR = node((0, 2150, 1080, 2400), children=node((100, 2200, 300, 2350), clickable=True, desc="首页"))


def parsed(xml):
    ns = tree.parse(xml, SCREEN)
    tree.assign_refs(ns, [], 0)
    return ns


def test_rows_absorb_their_texts():
    ns = parsed(screen(LIST))
    rows = [n for n in ns if n.role == "btn"]
    assert [n.label for n in rows] == ["标题一 · 3 回复", "标题二", "被盖住"]
    assert not [n for n in ns if n.role == "text"]


def test_floating_bar_covers_rows_under_it():
    ns = parsed(screen(LIST, BAR))
    by_label = {n.label: n for n in ns}
    assert by_label["被盖住"].covered
    assert not by_label["标题一 · 3 回复"].covered
    assert not by_label["首页"].covered


def test_exact_and_partial_queries():
    ns = parsed(screen(LIST, BAR))
    assert tree.resolve(ns, "标题二").label == "标题二"
    assert tree.resolve(ns, "3 回复").label.startswith("标题一")
    assert tree.resolve(ns, "~标题一").label.startswith("标题一")
    assert tree.resolve(ns, "#list").role == "scroll"


def test_ambiguous_query_refuses():
    ns = parsed(screen(LIST))
    try:
        tree.resolve(ns, "~标题")
    except tree.QueryError as e:
        assert "matched 2" in str(e)
    else:
        raise AssertionError("expected an ambiguity error")
    assert tree.resolve(ns, "~标题", nth=2).label == "标题二"


def test_refs_follow_elements_and_go_stale():
    first = parsed(screen(LIST))
    ref = tree.resolve(first, "标题二").ref
    # The list scrolls up by 100px: same element, same ref.
    moved = LIST.replace("[0,300][1080,500]", "[0,200][1080,400]")
    second = tree.parse(screen(moved), SCREEN)
    tree.assign_refs(second, tree.to_state(first), len(first))
    assert tree.resolve(second, "标题二").ref == ref
    # The element leaves the screen: its ref no longer resolves.
    gone = screen(node((0, 0, 1080, 2400), scrollable=True, children=node((0, 0, 100, 100), clickable=True, text="别的")))
    third = tree.parse(gone, SCREEN)
    tree.assign_refs(third, tree.to_state(second), 10)
    try:
        tree.resolve(third, ref)
    except tree.QueryError as e:
        assert "stale" in str(e)
    else:
        raise AssertionError("expected a stale ref")


def test_delta_lists_added_and_removed():
    first = parsed(screen(LIST))
    with_bar = tree.parse(screen(LIST, BAR), SCREEN)
    tree.assign_refs(with_bar, tree.to_state(first), len(first))
    lines, wholesale = tree.delta(first, with_bar)
    assert not wholesale
    assert any(ln.startswith("+") and "首页" in ln for ln in lines)


def test_overlaps_inside_a_list_are_not_covers():
    card = node(
        (0, 0, 1080, 2000),
        scrollable=True,
        children=node((0, 100, 1080, 600), clickable=True, desc="帖子", children=node((700, 500, 800, 590), clickable=True, desc="评论"))
        + node((40, 560, 1040, 620)),  # a divider drawn after the card
    )
    ns = parsed(screen(card))
    assert not [n for n in ns if n.covered]


def test_large_unclickable_overlay_is_not_a_cover():
    overlay = node((0, 0, 1080, 2400), desc="主屏幕")
    ns = parsed(screen(node((100, 100, 300, 300), clickable=True, desc="设置"), overlay))
    assert not tree.resolve(ns, "设置").covered


def test_id_only_containers_scope_but_are_not_listed():
    ns = parsed(screen(node((0, 0, 1080, 800), rid="sheet", children=node((0, 100, 500, 200), clickable=True, desc="OK"))))
    sheet = tree.resolve(ns, "#sheet")
    assert sheet.role == "box"
    assert [n.label for n in tree.within(ns, sheet)] == ["OK"]
    assert [n.role for n in tree.listed(ns)] == ["btn"]
