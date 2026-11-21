"""
Fusion comp dump: read a .comp or .setting (the Lua table Fusion writes) into a
small JSON tree, and diff two trees. Stdlib only; touches no Resolve state.

Tree shape (the intended tree a person writes by hand has the same shape):

    {"nodes":     {NAME: {"type": TOOL, "pos": [x, y], "inputs": {NAME: scalar}}},
     "links":     [{"from": NODE, "output": "Output", "to": NODE, "input": NAME}],
     "modifiers": {NAME: {"type": "BezierSpline", "keyframes": 4}}}

A tool with a ViewInfo is a node; one without (a spline, a path) is a modifier.
A tool nested in a group or macro is named GROUP/CHILD. A link comes from each
input's SourceOp and Source pair; its target may be a node or a modifier.
"""

import json

from rpresolve.cmd_effect import parse_setting

DEFAULT_OUTPUT = "Output"


def _scalar(v):
    """The plain value of an `Input { Value = x }`, or a FuID { "x" } as its string; else
    (a table of numbers, a gradient) the marker None so it is left out."""
    if isinstance(v, dict) and v.get("__ctor") == "FuID":
        v = v.get(1)
    return v if isinstance(v, (str, int, float, bool)) else None


def _walk(tools, prefix, tree):
    for name, spec in tools.items():
        if not isinstance(spec, dict):
            continue
        full = prefix + str(name)
        ctor = spec.get("__ctor")
        inputs = spec.get("Inputs") if isinstance(spec.get("Inputs"), dict) else {}
        if "ViewInfo" in spec:
            node = {"type": ctor}
            pos = spec["ViewInfo"].get("Pos") if isinstance(spec["ViewInfo"], dict) else None
            if isinstance(pos, dict) and 1 in pos and 2 in pos:
                node["pos"] = [pos[1], pos[2]]
            node["inputs"] = {}
            tree["nodes"][full] = node
        else:
            keys = spec.get("KeyFrames")
            tree["modifiers"][full] = {"type": ctor,
                                       "keyframes": len(keys) if isinstance(keys, dict) else 0}
        for iname, inp in inputs.items():
            if not isinstance(inp, dict):
                continue
            if "SourceOp" in inp:
                tree["links"].append({"from": prefix + str(inp["SourceOp"]),
                                      "output": inp.get("Source") or DEFAULT_OUTPUT,
                                      "to": full, "input": str(iname)})
            elif "ViewInfo" in spec and _scalar(inp.get("Value")) is not None:
                tree["nodes"][full]["inputs"][str(iname)] = _scalar(inp["Value"])
        if isinstance(spec.get("Tools"), dict):  # a group or macro: its children
            _walk(spec["Tools"], full + "/", tree)


def parse(text):
    """The tree of a .comp or .setting text; ValueError when it is not a Lua table."""
    root = parse_setting(text)
    if not isinstance(root, dict) or not isinstance(root.get("Tools"), dict):
        raise ValueError("no Tools table: not a Fusion comp or setting")
    tree = {"nodes": {}, "links": [], "modifiers": {}}
    _walk(root["Tools"], "", tree)
    return tree


def _link_key(link):
    return (link["from"], link["to"], link["input"])


def diff(actual, intended):
    """Lines saying how `actual` departs from `intended`; empty when they match on node names
    and types and on links (from, to, input). Inputs, positions and modifiers are not compared."""
    out = []
    a, i = actual.get("nodes", {}), intended.get("nodes", {})
    out += [f"missing node {n} ({i[n].get('type')})" for n in sorted(set(i) - set(a))]
    out += [f"extra node {n} ({a[n].get('type')})" for n in sorted(set(a) - set(i))]
    out += [f"type mismatch {n}: {a[n].get('type')}, intended {i[n].get('type')}"
            for n in sorted(set(a) & set(i)) if a[n].get("type") != i[n].get("type")]
    al = {_link_key(x): x for x in actual.get("links", [])}
    il = {_link_key(x): x for x in intended.get("links", [])}
    fmt = lambda k: f"{k[0]} -> {k[1]}.{k[2]}"  # noqa: E731
    out += [f"missing link {fmt(k)}" for k in sorted(set(il) - set(al))]
    out += [f"extra link {fmt(k)}" for k in sorted(set(al) - set(il))]
    return out


def load_tree(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
