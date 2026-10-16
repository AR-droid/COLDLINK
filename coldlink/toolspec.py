"""Build JSON-schema tool definitions from the tool functions' signatures, so
the MCP server and the Lead agent expose exactly the same tools."""
from __future__ import annotations

import inspect
import typing

_PY = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}


def _schema(tp) -> dict:
    origin = typing.get_origin(tp)
    args = [a for a in typing.get_args(tp) if a is not type(None)]
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)) and args:
        return _schema(args[0])
    if origin is list:
        return {"type": "array", "items": _schema(args[0]) if args else {}}
    return {"type": _PY.get(tp, "string")}


def spec(fn, hidden: tuple[str, ...] = ("lab", "run_id")) -> dict:
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    props, required = {}, []
    for name, p in sig.parameters.items():
        if name in hidden:
            continue
        props[name] = _schema(hints.get(name, str))
        if p.default is inspect.Parameter.empty:
            required.append(name)
    doc = inspect.getdoc(fn) or fn.__name__
    return {"name": fn.__name__, "description": " ".join(doc.split()),
            "input_schema": {"type": "object", "properties": props, "required": required}}
