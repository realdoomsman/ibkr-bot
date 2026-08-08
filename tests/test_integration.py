"""Fleet-wide import test: every module must import cleanly.

This is the cheapest possible integration guarantee: a circular import, a
missing dependency, a module-level crash, or a syntax error in ANY engine
or tool fails here at test time instead of at 9:28 AM on the VM. Every
scheduled task's entry module is on this list - keep it that way.
"""

import importlib

import pytest

MODULES = [
    # the library
    "bot.broker", "bot.config", "bot.data", "bot.edgar", "bot.halts",
    "bot.polygon", "bot.risk", "bot.scanner", "bot.strategy",
    "bot.toxicity", "bot.tripwire", "bot.ai_analyst",
    # every scheduled/manual entry point (import must be side-effect safe)
    "run_pennymo", "run_pennyradar", "run_watchdog", "run_review",
    "run_lab", "run_markout", "run_polylab", "run_spreadcheck",
    "run_gate", "run_gapperlab", "flatten_now",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
