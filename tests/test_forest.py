"""Forest invariants. Expected byte counts are literals, so the assertions are arithmetic
rather than a second run of the implementation."""

from __future__ import annotations

import os

import pytest

from mac_cleanup.errors import Refusal
from mac_cleanup.forest import attribute, build_forest, delete_scope

BLOCK = 512


def mk(root, spec):
    """Build a tree from {name: int_size | dict}. Returns the root path as str."""
    root.mkdir(parents=True, exist_ok=True)
    for name, val in spec.items():
        if isinstance(val, dict):
            mk(root / name, val)
        else:
            (root / name).write_bytes(b"x" * val)
    return str(root)


# --------------------------------------------------------------------------- structure


def test_ancestor_and_descendant_are_linked_not_duplicated(tmp_path):
    parent = mk(tmp_path / "caches", {"a": 10})
    child = mk(tmp_path / "caches" / "pip", {"b": 10})
    f = build_forest([("caches", parent), ("caches.pip", child)])

    assert len(f.nodes) == 2
    pid, cid = f.by_target["caches"], f.by_target["caches.pip"]
    assert f.nodes[cid].parent == pid
    assert f.nodes[pid].children == [cid]
    assert f.roots == [pid]


def test_three_deep_nesting_links_to_the_nearest_ancestor(tmp_path):
    a = mk(tmp_path / "a", {})
    b = mk(tmp_path / "a" / "b", {})
    c = mk(tmp_path / "a" / "b" / "c", {})
    f = build_forest([("c", c), ("a", a), ("b", b)])  # deliberately unsorted input
    assert f.nodes[f.by_target["c"]].parent == f.by_target["b"]
    assert f.nodes[f.by_target["b"]].parent == f.by_target["a"]
    assert f.nodes[f.by_target["a"]].parent is None


def test_disjoint_siblings_are_separate_roots(tmp_path):
    x = mk(tmp_path / "x", {})
    y = mk(tmp_path / "y", {})
    f = build_forest([("x", x), ("y", y)])
    assert sorted(f.roots) == sorted([f.by_target["x"], f.by_target["y"]])


def test_absent_targets_are_dropped_so_uninstalled_tools_may_be_declared(tmp_path):
    real = mk(tmp_path / "real", {})
    f = build_forest([("real", real), ("ghost", str(tmp_path / "nope"))])
    assert list(f.by_target) == ["real"]


def test_two_target_ids_resolving_to_one_path_is_an_error(tmp_path):
    p = mk(tmp_path / "dup", {})
    with pytest.raises(Refusal) as ei:
        build_forest([("first", p), ("second", p + "/")])
    assert ei.value.code == "E_DUPLICATE_TARGET"
    assert "first" in str(ei.value) and "second" in str(ei.value)


# ------------------------------------------------------------------- attribution rule


def test_attribution_sends_bytes_to_the_deepest_enclosing_target(tmp_path):
    parent = mk(tmp_path / "caches", {})
    child = mk(tmp_path / "caches" / "pip", {})
    f = build_forest([("caches", parent), ("caches.pip", child)])
    pid, cid = f.by_target["caches"], f.by_target["caches.pip"]

    # A directory that is a cut point switches attribution to the child...
    assert attribute(f, child, pid) == cid
    # ...and an ordinary directory inherits whatever node it was entered under.
    assert attribute(f, os.path.join(parent, "ordinary"), pid) == pid
    assert attribute(f, os.path.join(child, "deeper"), cid) == cid


def test_cut_point_directory_blocks_belong_to_the_child(tmp_path):
    """Pinned deliberately: if these blocks went to the parent, deleting only the child
    would free bytes charged to a node the user never selected."""
    parent = mk(tmp_path / "p", {})
    child = mk(tmp_path / "p" / "c", {})
    f = build_forest([("p", parent), ("c", child)])
    assert attribute(f, child, f.by_target["p"]) == f.by_target["c"]


# ------------------------------------------------------------------------- accounting


def test_exclusive_sums_do_not_double_count(tmp_path):
    parent = mk(tmp_path / "p", {})
    child = mk(tmp_path / "p" / "c", {})
    f = build_forest([("p", parent), ("c", child)])
    f.node_for("p").self_alloc = 100 * BLOCK
    f.node_for("c").self_alloc = 40 * BLOCK

    # Literal expectations, not a re-run of the implementation.
    assert f.total_alloc(["p", "c"]) == 140 * BLOCK
    assert f.total_alloc(["p"]) == 100 * BLOCK
    assert f.inclusive_alloc(f.by_target["p"]) == 140 * BLOCK
    assert f.inclusive_alloc(f.by_target["c"]) == 40 * BLOCK


def test_total_over_all_targets_is_the_sum_of_exclusives(tmp_path):
    a = mk(tmp_path / "a", {})
    b = mk(tmp_path / "a" / "b", {})
    c = mk(tmp_path / "c", {})
    f = build_forest([("a", a), ("b", b), ("c", c)])
    f.node_for("a").self_alloc = 7 * BLOCK
    f.node_for("b").self_alloc = 11 * BLOCK
    f.node_for("c").self_alloc = 13 * BLOCK
    assert f.total_alloc() == 31 * BLOCK


def test_shared_with_is_reported_separately_from_exclusive(tmp_path):
    """Hardlinks are detectable and recorded. Clones are not detectable at all, which is
    why the documented relation is `sum(self_alloc) >= physical` rather than equality."""
    a = mk(tmp_path / "a", {})
    b = mk(tmp_path / "b", {})
    f = build_forest([("a", a), ("b", b)])
    aid, bid = f.by_target["a"], f.by_target["b"]
    f.nodes[aid].self_alloc = 8 * BLOCK
    f.nodes[aid].shared_with[bid] = 3 * BLOCK

    assert f.total_alloc(["a"]) == 8 * BLOCK
    assert f.nodes[aid].shared_with == {bid: 3 * BLOCK}, "sharing is adjacent, never folded in"


# ------------------------------------------------------------------------ delete scope


def test_unselected_descendants_are_protected_from_an_ancestor_delete(tmp_path):
    """The predecessor's `clear_contents` on ~/Library/Caches destroyed every child target
    whether or not the user chose it. This is the regression test for that."""
    caches = mk(tmp_path / "caches", {})
    pip = mk(tmp_path / "caches" / "pip", {})
    play = mk(tmp_path / "caches" / "playwright", {})
    f = build_forest([("caches", caches), ("pip", pip), ("playwright", play)])

    root, skip = delete_scope(f, f.by_target["caches"], selected={f.by_target["caches"]})
    assert root == caches
    assert skip == {pip, play}


def test_a_selected_descendant_is_not_skipped(tmp_path):
    caches = mk(tmp_path / "caches", {})
    pip = mk(tmp_path / "caches" / "pip", {})
    play = mk(tmp_path / "caches" / "playwright", {})
    f = build_forest([("caches", caches), ("pip", pip), ("playwright", play)])

    sel = {f.by_target["caches"], f.by_target["pip"]}
    _, skip = delete_scope(f, f.by_target["caches"], selected=sel)
    assert skip == {play}, "pip was chosen, so it is deleted as its own unit, not protected"


def test_delete_scope_descends_past_a_selected_child_to_protect_a_grandchild(tmp_path):
    a = mk(tmp_path / "a", {})
    b = mk(tmp_path / "a" / "b", {})
    c = mk(tmp_path / "a" / "b" / "c", {})
    f = build_forest([("a", a), ("b", b), ("c", c)])

    sel = {f.by_target["a"], f.by_target["b"]}
    _, skip = delete_scope(f, f.by_target["a"], selected=sel)
    assert skip == {c}


def test_leaf_delete_scope_has_nothing_to_skip(tmp_path):
    p = mk(tmp_path / "p", {})
    f = build_forest([("p", p)])
    root, skip = delete_scope(f, f.by_target["p"], selected={f.by_target["p"]})
    assert (root, skip) == (p, set())


# ----------------------------------------------------------------------- resolve rules


def test_a_symlinked_final_component_is_not_dereferenced(tmp_path):
    """resolve_parent keeps the leaf lexical, so a link's own identity stays visible to the
    caller instead of being silently replaced by its target."""
    real = mk(tmp_path / "real", {})
    link = tmp_path / "link"
    link.symlink_to(real)
    f = build_forest([("link", str(link))])
    assert f.nodes[0].key == str(link), "must not collapse to the link target"


def test_relative_and_malformed_paths_are_refused():
    for bad, code in [
        ("relative/path", "E_RELATIVE"),
        ("", "E_MALFORMED"),
        ("/a\0b", "E_MALFORMED"),
    ]:
        with pytest.raises(Refusal) as ei:
            build_forest([("x", bad)])
        assert ei.value.code == code, bad
