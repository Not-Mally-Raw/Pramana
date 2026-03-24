from __future__ import annotations

from types import SimpleNamespace

from utils.safe_eval import evaluate_boolean_expression


def test_safe_eval_allows_basic_boolean_expressions() -> None:
    ctx = {
        "flag_count": 3,
        "risk_score": 0.75,
        "metadata": {"actions_per_min": 7},
    }
    expr = "flag_count >= 2 and risk_score > 0.70 and metadata.actions_per_min >= 5"
    assert evaluate_boolean_expression(expr, ctx) is True


def test_safe_eval_blocks_function_call_payload() -> None:
    ctx = {"flag_count": 3}
    expr = "__import__('os').system('echo hacked') or flag_count > 2"
    assert evaluate_boolean_expression(expr, ctx) is False


def test_safe_eval_supports_namespace_lookup() -> None:
    ctx = SimpleNamespace(
        l1_results=SimpleNamespace(
            injection=SimpleNamespace(flagged=True, score=0.95),
        )
    )
    expr = "l1_results.injection.flagged == True and l1_results.injection.score >= 0.9"
    assert evaluate_boolean_expression(expr, ctx) is True
