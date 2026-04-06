"""Safe boolean expression evaluator for routing rules.

Supports a constrained subset:
- and/or/not
- comparisons: >, >=, <, <=, ==, !=
- dotted attribute/name lookups on provided context objects
- numeric, boolean, and string constants

Disallows function calls, imports, comprehensions, subscripts, and all mutations.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace
from typing import Any

_ALLOWED_COMPARE_OPS = (
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.Eq,
    ast.NotEq,
)


class UnsafeExpressionError(ValueError):
    """Raised when expression contains disallowed syntax."""


def _resolve_name(name: str, context: dict[str, Any]) -> Any:
    if name in context:
        return context[name]
    raise UnsafeExpressionError(f"Unknown identifier: {name}")


def _resolve_attr(base: Any, attr: str) -> Any:
    if isinstance(base, dict):
        if attr in base:
            return base[attr]
        raise UnsafeExpressionError(f"Unknown key: {attr}")
    if hasattr(base, attr):
        return getattr(base, attr)
    raise UnsafeExpressionError(f"Unknown attribute: {attr}")


def _eval_node(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, context)

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(bool(_eval_node(v, context)) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(bool(_eval_node(v, context)) for v in node.values)
        raise UnsafeExpressionError("Unsupported boolean operator")

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not bool(_eval_node(node.operand, context))

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, _ALLOWED_COMPARE_OPS):
                raise UnsafeExpressionError("Unsupported compare operator")
            right = _eval_node(comparator, context)
            if isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Eq):
                ok = left == right
            else:
                ok = left != right
            if not ok:
                return False
            left = right
        return True

    if isinstance(node, ast.Name):
        return _resolve_name(node.id, context)

    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, context)
        return _resolve_attr(base, node.attr)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value
        raise UnsafeExpressionError("Unsupported constant type")

    raise UnsafeExpressionError(f"Unsupported syntax: {type(node).__name__}")


def evaluate_boolean_expression(
    expression: str, context: dict[str, Any] | SimpleNamespace
) -> bool:
    """Safely evaluate a constrained boolean expression.

    Returns False if expression cannot be safely evaluated.
    """
    local_context = context if isinstance(context, dict) else vars(context)
    try:
        tree = ast.parse(expression, mode="eval")
        return bool(_eval_node(tree, local_context))
    except Exception:
        return False
