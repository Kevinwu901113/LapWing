"""Eval suite pytest configuration.

Keeps evals out of normal `pytest tests/` runs since they're expensive
(real LLM calls). Run with `pytest evals/ --run-evals` explicitly.
"""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-evals",
        action="store_true",
        default=False,
        help="Run eval suite (expensive, uses real LLM calls)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-evals", default=False):
        return
    skip_eval = pytest.mark.skip(reason="use --run-evals to run eval suite")
    for item in items:
        if "evals" in str(item.fspath):
            item.add_marker(skip_eval)
