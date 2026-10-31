"""
rpresolve.mcp.schema: validate tool arguments against the JSON Schema
subset the tools use: type, properties, required, additionalProperties
(false), enum, minimum, maximum, minLength, minItems, maxItems, items,
pattern (an unanchored search as in JSON Schema, but a closing $ means the very end, so a trailing newline fails), uniqueItems, default (not applied).
Every other keyword is ignored: description, title, and anything the tools
do not use today (oneOf, anyOf, format, ...); add it here before a tool
relies on it. Numbers must be finite. validate() returns a list of messages;
empty means valid.
"""

import json
import math
import re

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _is(value, kind):
    if kind in ("integer", "number") and isinstance(value, bool):
        return False
    if kind == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _TYPES[kind])


def _same(a, b):
    """Equality where True is not 1."""
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _strict_end(pattern):
    # Python's $ also matches before a final newline, so "SW\n" would pass ^SW$.
    # shortcut: only a closing $ is rewritten, not one inside a group.
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def validate(schema, value, path="arguments"):
    errors = []
    kind = schema.get("type")
    if kind:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is(value, k) for k in kinds):
            return [f"{path}: expected {' or '.join(kinds)}, got {type(value).__name__}"]
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return [f"{path}: {value} is not a finite number"]
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above the maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} character(s)")
        if "pattern" in schema and not re.search(_strict_end(schema["pattern"]), value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} item(s)")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) < len(value):
            errors.append(f"{path}: items are not unique")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate(schema["items"], item, f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or ():
            if name not in value:
                errors.append(f"{path}: missing required '{name}'")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    errors.append(f"{path}: unknown property '{name}'")
        for name, sub in props.items():
            if name in value:
                errors += validate(sub, value[name], f"{path}.{name}")
    return errors


def coerce(schema, value):
    """value with whole floats (2.0) turned into ints wherever the schema says
    integer, at any depth, so a handler can use them as indexes or counts."""
    kind = schema.get("type")
    if (kind == "integer" or isinstance(kind, list) and "integer" in kind
            and "number" not in kind) and isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        return {k: coerce(props[k], v) if k in props else v for k, v in value.items()}
    if isinstance(value, list) and "items" in schema:
        return [coerce(schema["items"], v) for v in value]
    return value


def with_defaults(schema, args):
    """args with each top-level property's schema default filled in."""
    out = dict(args or {})
    for name, sub in (schema.get("properties") or {}).items():
        if name not in out and "default" in sub:
            out[name] = sub["default"]
    return out
