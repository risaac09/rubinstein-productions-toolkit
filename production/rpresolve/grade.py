"""
rpresolve.grade: put grades on timeline items and read them back through
the item's node Graph (Resolve 21). Stdlib only.

A .drx replaces the item's whole node graph, and the scripting API cannot
create nodes, so a LUT can only go on a node that already exists. Every
apply reads the graph back: setters return True even when a modal dialog
swallowed the write.
"""

import os


def _path_under(path, root, follow_links=True):
    """True if `path` sits inside directory `root`. Both are realpath'd by
    default; with follow_links=False only abspath'd, so a symlink placed
    inside `root` still counts as inside it."""
    norm = os.path.realpath if follow_links else os.path.abspath
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def lut_paths_match(requested, reported, lut_roots=()):
    """Decide whether Graph.GetLUT's `reported` value names the LUT that
    was requested. Resolve may report the absolute path or a path relative
    to one of its LUT folders, so accept any of:
      - both paths equal after os.path.realpath
      - `reported` equals `requested` made relative to a LUT root
      - `reported` is relative and matches the trailing path components of
        `requested`, only when `requested` sits under none of `lut_roots`
        (a LUT root this function was not told about)
    """
    if not reported:
        return False
    req_real = os.path.realpath(requested)
    rep_norm = os.path.normpath(str(reported).replace("\\", "/"))
    if os.path.isabs(rep_norm):
        return os.path.realpath(rep_norm) == req_real

    rep_norm = rep_norm.lstrip("/")
    known_root = False
    req_abs = os.path.abspath(requested)
    for root in lut_roots or ():
        # A symlink inside the root is scanned under its in-root path, so
        # try the unresolved path as well as the realpath.
        for req, norm in ((req_abs, os.path.abspath), (req_real, os.path.realpath)):
            if _path_under(req, root, follow_links=(norm is os.path.realpath)):
                known_root = True
                if os.path.relpath(req, norm(root)) == rep_norm:
                    return True
    if known_root:
        # Under a known root the exact root-relative path is the only
        # match; a shorter tail could name a same-named LUT elsewhere.
        return False
    req_parts = req_real.split(os.sep)
    rep_parts = rep_norm.split("/")
    return len(rep_parts) <= len(req_parts) and req_parts[-len(rep_parts):] == rep_parts


def apply_lut_to_item(item, node_index, lut_path, lut_roots=()):
    """Set a LUT on one node of one timeline item through its Graph and
    read it back. Returns (ok, detail)."""
    graph = item.GetNodeGraph()
    if not graph:
        return (False, "no node graph (is this a video clip?)")
    num_nodes = graph.GetNumNodes() or 0
    if node_index > num_nodes:
        return (False, f"only has {num_nodes} node(s); the scripting API cannot create node {node_index}. "
                       "Add the node in the Color page first, then re-run.")
    if not graph.SetLUT(node_index, lut_path):
        return (False, "SetLUT returned False (is the LUT in a folder Resolve has scanned?)")
    reported = graph.GetLUT(node_index)
    if not lut_paths_match(lut_path, reported, lut_roots):
        return (False, f"read back LUT {reported!r} on node {node_index}, not the requested file")
    return (True, f"node {node_index} LUT = {reported}")


def apply_lut_to_items(items, node_index, lut_path, lut_roots=()):
    return [(item.GetName(), *apply_lut_to_item(item, node_index, lut_path, lut_roots)) for item in items]


def graph_fingerprint(graph):
    """What the scripting API can read of a node graph: per node its label,
    LUT, and (where the build has GetToolsInNode) tool list. Used to tell
    an applied grade from a no-op, since a .drx stores its node graph in an
    opaque binary blob that cannot be compared against directly."""
    count = int(graph.GetNumNodes() or 0)
    tools = getattr(graph, "GetToolsInNode", None)
    nodes = []
    for i in range(1, count + 1):
        node_tools = tools(i) if tools else None
        nodes.append((graph.GetNodeLabel(i) or "", graph.GetLUT(i) or "",
                      tuple(node_tools) if isinstance(node_tools, (list, tuple)) else node_tools))
    return tuple(nodes)


def apply_drx_to_item(item, drx_path, grade_mode):
    """Apply a .drx grade to one timeline item through its Graph, then read
    back a fresh Graph (the grade replaces the old one). The setter returns
    True even when a modal dialog swallows the write, so a graph that reads
    back identical to before the call counts as not applied. Returns
    (ok, detail)."""
    graph = item.GetNodeGraph()
    if not graph:
        return (False, "no node graph (is this a video clip?)")
    before = graph_fingerprint(graph)
    if not graph.ApplyGradeFromDRX(drx_path, grade_mode):
        return (False, "ApplyGradeFromDRX returned False")
    after_graph = item.GetNodeGraph() or graph
    after = graph_fingerprint(after_graph)
    if not after:
        return (False, "grade reported applied but the node graph reads back empty")
    if after == before:
        return (False, f"node graph unchanged after apply ({len(after)} node(s), same labels/LUTs); "
                       "a modal dialog may have swallowed the write, or the clip already carried "
                       "this grade. Check it in the Color page.")
    return (True, f"{len(after)} node(s) after grade (was {len(before)})")


def apply_drx_to_items(items, drx_path, grade_mode):
    return [(item.GetName(), *apply_drx_to_item(item, drx_path, grade_mode)) for item in items]


def read_grade(item):
    """What the API can read of an item's grade: {version, color_group,
    num_nodes, nodes: [{index, label, lut, tools}], fingerprint}. tools is
    None where the build returns None (a node still at default)."""
    graph = item.GetNodeGraph()
    if not graph:
        return None
    fp = graph_fingerprint(graph)
    nodes = [{"index": i, "label": label, "lut": lut,
              "tools": list(tools) if isinstance(tools, tuple) else tools}
             for i, (label, lut, tools) in enumerate(fp, 1)]
    group = getattr(item, "GetColorGroup", None)
    group = group() if group else None
    return {"version": item_version(item),
            "color_group": group.GetName() if group else None,
            "num_nodes": len(fp), "nodes": nodes, "fingerprint": fp}


def is_default_graph(fp):
    """True for a graph a script can overwrite without losing work: at most
    one node, unlabelled, with no LUT and no tools."""
    return len(fp) <= 1 and all(not label and not lut and not tools for label, lut, tools in fp)


def item_version(item):
    """{name, type} of the item's current grade version ("local" or
    "remote"), or None when the build has no GetCurrentVersion."""
    get = getattr(item, "GetCurrentVersion", None)
    if not get:
        return None
    v = get() or {}
    kind = v.get("versionType")
    return {"name": v.get("versionName"),
            "type": {0: "local", 1: "remote"}.get(kind, kind)}
