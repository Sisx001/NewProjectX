"""Safe condition language (JSON-logic subset) and {{path}} templating. No code execution, bounded size/depth."""
from __future__ import annotations

import re
from typing import Any

COMPARE = {"==", "!=", ">", "<", ">=", "<=", "in", "contains"}
LOGIC = {"and", "or", "not"}
MAX_DEPTH = 6
MAX_NODES = 60
_PATH = re.compile(r"^[A-Za-z_][\w]*(\.[\w]+){0,8}$")
_TPL = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


class LogicError(ValueError):
    pass


def get_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _operand_ok(x: Any) -> bool:
    if isinstance(x, dict):
        return set(x) == {"var"} and isinstance(x["var"], str) and bool(_PATH.match(x["var"]))
    if isinstance(x, list):
        return len(x) <= 50 and all(isinstance(i, str | int | float | bool) or i is None for i in x)
    return isinstance(x, str | int | float | bool) or x is None


def validate_logic(node: Any) -> None:
    count = [0]

    def walk(n: Any, depth: int) -> None:
        count[0] += 1
        if depth > MAX_DEPTH or count[0] > MAX_NODES:
            raise LogicError("Conditions are too complex")
        if not isinstance(n, dict) or len(n) != 1:
            raise LogicError("Each condition must be an object with exactly one operator")
        op, args = next(iter(n.items()))
        if op in ("and", "or"):
            if not isinstance(args, list) or not args:
                raise LogicError(f"“{op}” needs a non-empty list")
            for a in args:
                walk(a, depth + 1)
        elif op == "not":
            walk(args, depth + 1)
        elif op in COMPARE:
            if not isinstance(args, list) or len(args) != 2 or not all(_operand_ok(a) for a in args):
                raise LogicError(f"“{op}” needs two operands (values or {{\"var\": \"path\"}})")
        else:
            raise LogicError(f"Unsupported operator {op!r}; use {sorted(COMPARE | LOGIC)}")

    if node in (None, {}):
        return
    walk(node, 0)


def _val(x: Any, data: Any) -> Any:
    return get_path(data, x["var"]) if isinstance(x, dict) else x


def evaluate(node: Any, data: Any) -> bool:
    if node in (None, {}):
        return True
    op, args = next(iter(node.items()))
    if op == "and":
        return all(evaluate(a, data) for a in args)
    if op == "or":
        return any(evaluate(a, data) for a in args)
    if op == "not":
        return not evaluate(args, data)
    a, b = _val(args[0], data), _val(args[1], data)
    try:
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op in (">", "<", ">=", "<="):
            if a is None or b is None:
                return False
            return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
        if op == "in":
            return isinstance(b, list | str) and a in b
        if op == "contains":
            return isinstance(a, list | str) and b in a
    except TypeError:
        return False
    return False


def render(template: str, data: Any) -> str:
    def rep(m: re.Match) -> str:
        v = get_path(data, m.group(1))
        return "" if v is None else str(v)

    return _TPL.sub(rep, template or "")
