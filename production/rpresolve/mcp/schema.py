"""
rpresolve.mcp.schema: validate tool arguments against the JSON Schema
subset the tools use: type, properties, required, additionalProperties
(false), enum, minimum, maximum, minLength, minItems, maxItems, items,
pattern, uniqueItems, default (not applied). validate() returns a list of messages;
empty means valid.
"""

import json
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


def validate(schema, value, path="arguments"):
    errors = []
    kind = schema.get("type")
    if kind:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is(value, k) for k in kinds):
            return [f"{path}: expected {' or '.join(kinds)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above the maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} character(s)")
        if "pattern" in schema and not re.search(schema["pattern"], value):
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


def coerce(schema, args):
    """args with integer-typed top-level values given as whole floats (2.0)
    turned into ints, so a handler can use them as indexes or counts."""
    out = dict(args or {})
    for name, sub in (schema.get("properties") or {}).items():
        v = out.get(name)
        if sub.get("type") == "integer" and isinstance(v, float) and v.is_integer():
            out[name] = int(v)
    return out


def with_defaults(schema, args):
    """args with each top-level property's schema default filled in."""
    out = dict(args or {})
    for name, sub in (schema.get("properties") or {}).items():
        if name not in out and "default" in sub:
            out[name] = sub["default"]
    return out
