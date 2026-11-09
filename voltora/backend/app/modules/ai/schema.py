"""Minimal JSON Schema subset validator (no dependency) for AI tool inputs and extension settings.

Supported: type (object/string/integer/number/boolean/array/null, or a list), properties, required,
additionalProperties (bool), enum, minimum, maximum, minLength, maxLength, pattern, items, minItems, maxItems,
format (email/uri; advisory), default (applied by `apply_defaults`).
"""
from __future__ import annotations

import re
from typing import Any

TYPES = {"object", "string", "integer", "number", "boolean", "array", "null"}
_KEYS = {"type", "properties", "required", "additionalProperties", "enum", "minimum", "maximum", "minLength",
         "maxLength", "pattern", "items", "minItems", "maxItems", "format", "default", "description", "title",
         "secret", "x-widget", "x-public", "examples"}


class SchemaError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['path'] or '$'}: {e['msg']}" for e in errors))
        self.errors = errors


def check_schema(schema: Any, path: str = "$", depth: int = 0) -> None:
    """Reject schemas this validator cannot honour (so a manifest cannot silently skip validation)."""
    if depth > 8:
        raise ValueError(f"{path}: schema nesting too deep")
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: schema must be an object")
    unknown = set(schema) - _KEYS
    if unknown:
        raise ValueError(f"{path}: unsupported schema keywords {sorted(unknown)}")
    t = schema.get("type")
    for tt in (t if isinstance(t, list) else [t] if t else []):
        if tt not in TYPES:
            raise ValueError(f"{path}: unknown type {tt}")
    for k, sub in (schema.get("properties") or {}).items():
        check_schema(sub, f"{path}.{k}", depth + 1)
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]", depth + 1)
    if "pattern" in schema:
        re.compile(schema["pattern"])


def _type_ok(t: str, v: Any) -> bool:
    return {
        "object": isinstance(v, dict),
        "string": isinstance(v, str),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "number": isinstance(v, int | float) and not isinstance(v, bool),
        "boolean": isinstance(v, bool),
        "array": isinstance(v, list),
        "null": v is None,
    }[t]


def _validate(schema: dict, v: Any, path: str, errors: list[dict]) -> None:
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(x, v) for x in ts):
            errors.append({"path": path, "msg": f"expected {' or '.join(ts)}"})
            return
    if "enum" in schema and v not in schema["enum"]:
        errors.append({"path": path, "msg": f"must be one of {schema['enum']}"})
    if isinstance(v, int | float) and not isinstance(v, bool):
        if "minimum" in schema and v < schema["minimum"]:
            errors.append({"path": path, "msg": f"minimum is {schema['minimum']}"})
        if "maximum" in schema and v > schema["maximum"]:
            errors.append({"path": path, "msg": f"maximum is {schema['maximum']}"})
    if isinstance(v, str):
        if "minLength" in schema and len(v) < schema["minLength"]:
            errors.append({"path": path, "msg": f"at least {schema['minLength']} characters"})
        if "maxLength" in schema and len(v) > schema["maxLength"]:
            errors.append({"path": path, "msg": f"at most {schema['maxLength']} characters"})
        if "pattern" in schema and not re.search(schema["pattern"], v):
            errors.append({"path": path, "msg": "has an invalid format"})
        if schema.get("format") == "uri" and v and not v.startswith("https://"):
            errors.append({"path": path, "msg": "must be an https:// URL"})
        if schema.get("format") == "email" and v and "@" not in v:
            errors.append({"path": path, "msg": "must be an email address"})
    if isinstance(v, list):
        if "minItems" in schema and len(v) < schema["minItems"]:
            errors.append({"path": path, "msg": f"at least {schema['minItems']} items"})
        if "maxItems" in schema and len(v) > schema["maxItems"]:
            errors.append({"path": path, "msg": f"at most {schema['maxItems']} items"})
        if "items" in schema:
            for i, item in enumerate(v):
                _validate(schema["items"], item, f"{path}[{i}]", errors)
    if isinstance(v, dict):
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in v:
                errors.append({"path": f"{path}.{k}", "msg": "is required"})
        if schema.get("additionalProperties") is False:
            for k in v:
                if k not in props:
                    errors.append({"path": f"{path}.{k}", "msg": "is not allowed"})
        for k, sub in props.items():
            if k in v:
                _validate(sub, v[k], f"{path}.{k}", errors)


def validate(schema: dict, value: Any) -> Any:
    errors: list[dict] = []
    _validate(schema or {"type": "object"}, value, "$", errors)
    if errors:
        raise SchemaError(errors)
    return value


def apply_defaults(schema: dict, value: dict) -> dict:
    out = dict(value or {})
    for k, sub in (schema.get("properties") or {}).items():
        if k not in out and "default" in sub:
            out[k] = sub["default"]
    return out
