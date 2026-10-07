"""Target forest: one traversal, exclusive attribution, honest bounds.

The problem this solves, measured on the author's machine: the bash predecessor declared
``~/Library/Caches`` as a target *and* six of its children as separate targets, and
``CoreSimulator`` alongside its own child ``CoreSimulator/Devices`` -- both measuring
10.861 GiB, so that tree was walked twice on every sizing pass, and there were 34 passes
per menu render.

The fix is a forest of normalized target paths plus one rule: bytes land on the **deepest
enclosing target node**. A worker carries the current node id down the walk and entering a
child directory costs one dict lookup.

On what the numbers mean
------------------------
``self_alloc`` is a sum of ``st_blocks * 512`` over files attributed to a node. That is an
**upper bound** on the physical bytes a deletion would free, and the inequality only runs
one way:

    sum(self_alloc for nodes in S)  >=  physical bytes under S

Two distinct mechanisms make it an over-count, and only one of them is detectable:

* **Hardlinks** -- several names, one inode. Detectable via ``st_nlink > 1``, so they are
  deduplicated here and recorded in ``shared_with``.
* **APFS copy-on-write clones** -- distinct inodes sharing extents, each reporting the full
  allocation in ``st_blocks`` and each with ``st_nlink == 1``. **No ``stat(2)`` field
  distinguishes a clone**, so they cannot be deduplicated and the figure stays an upper
  bound. This is not theoretical: ``pnpm``'s store uses clones by default on APFS, and
  Phase 0 measured ``du`` claiming 5.855 GiB for a store subtree that freed 1.734 GiB.

The ground truth for "what did this actually free" is the ``statvfs`` delta, which is why
the run report prints the accounted figure and the measured delta as two separate numbers
and never equates them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from mac_cleanup.errors import Refusal
from mac_cleanup.paths import resolve_parent


@dataclass
class Node:
    """One target in the forest."""

    key: str
    target_id: str
    parent: int | None = None
    children: list[int] = field(default_factory=list)

    # Exclusive to this node: bytes under `key` but under no deeper selected target.
    self_alloc: int = 0
    self_logical: int = 0
    self_files: int = 0
    self_dirs: int = 0

    # node_id -> bytes of hardlinked inodes also reachable from that node. Populated only
    # for st_nlink > 1; clones are invisible here by design (see module docstring).
    shared_with: dict[int, int] = field(default_factory=dict)


@dataclass
class Forest:
    nodes: list[Node]
    roots: list[int]
    cut_map: dict[str, int]
    by_target: dict[str, int]

    def node_for(self, target_id: str) -> Node:
        return self.nodes[self.by_target[target_id]]

    def inclusive_alloc(self, node_id: int) -> int:
        """Self plus every descendant node's self. Recursion depth is target nesting."""
        total = self.nodes[node_id].self_alloc
        for child in self.nodes[node_id].children:
            total += self.inclusive_alloc(child)
        return total

    def total_alloc(self, target_ids: list[str] | None = None) -> int:
        """Upper bound on bytes under the given targets, each byte counted once.

        Because `self_alloc` is exclusive, this is a plain sum: no subtraction step that a
        later edit could forget. Ancestors do not double-count their selected descendants.
        """
        ids = list(self.by_target) if target_ids is None else target_ids
        return sum(self.nodes[self.by_target[t]].self_alloc for t in ids)


def build_forest(target_paths: list[tuple[str, str]]) -> Forest:
    """Build the forest from ``(target_id, raw_path)`` pairs.

    Paths are normalized with the shared resolve contract (parent resolved, final component
    lexical) and non-existent targets are dropped, so callers may declare targets for tools
    that are not installed.

    Raises:
        Refusal: ``E_DUPLICATE_TARGET`` if two target ids resolve to the same path. A
            target file that aliases itself is a definition bug, and guessing which row the
            user meant would either double-count a footer total or render one row as 0 B --
            and "0 B" reads as "nothing there, safe to delete".
    """
    by_key: dict[str, str] = {}
    for target_id, raw in target_paths:
        key = resolve_parent(raw)
        if not os.path.lexists(key):
            continue
        if key in by_key:
            raise Refusal(
                "E_DUPLICATE_TARGET", key, f"both {by_key[key]!r} and {target_id!r} resolve here"
            )
        by_key[key] = target_id

    # Shallowest first, so an ancestor is always indexed before any of its descendants and
    # the nearest-ancestor search below cannot miss.
    keys = sorted(by_key, key=lambda k: (k.count(os.sep), k))

    nodes: list[Node] = []
    index: dict[str, int] = {}
    roots: list[int] = []

    for key in keys:
        node_id = len(nodes)
        nodes.append(Node(key=key, target_id=by_key[key]))
        index[key] = node_id

        parent: int | None = None
        probe = os.path.dirname(key)
        while True:
            if probe in index:
                parent = index[probe]
                break
            nxt = os.path.dirname(probe)
            if nxt == probe:
                break
            probe = nxt

        nodes[node_id].parent = parent
        if parent is None:
            roots.append(node_id)
        else:
            nodes[parent].children.append(node_id)

    return Forest(
        nodes=nodes,
        roots=roots,
        cut_map=dict(index),
        by_target={n.target_id: i for i, n in enumerate(nodes)},
    )


def attribute(forest: Forest, child_path: str, parent_node: int) -> int:
    """The whole attribution rule: deepest enclosing target wins.

    Called once per directory on entry. A cut-point directory's own inode blocks belong to
    the **child** node, which follows from returning the child id here before the caller
    adds that directory's own `st_blocks`.
    """
    return forest.cut_map.get(child_path, parent_node)


def delete_scope(forest: Forest, node_id: int, selected: set[int]) -> tuple[str, set[str]]:
    """Return ``(root_to_delete, skip_paths)`` for one selected node.

    Descendant targets the user did **not** select are pruned from the walk. This is what
    stops "clear user caches" from silently destroying an unselected Playwright browser
    cache -- the bash predecessor's ``clear_contents`` on a parent removed every child
    target whether or not it was chosen.
    """
    skip: set[str] = set()
    stack = list(forest.nodes[node_id].children)
    while stack:
        child = stack.pop()
        if child in selected:
            # Deleted as its own unit; descend past it to find unselected grandchildren.
            stack.extend(forest.nodes[child].children)
        else:
            skip.add(forest.nodes[child].key)
    return forest.nodes[node_id].key, skip
