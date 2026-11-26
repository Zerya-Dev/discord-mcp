"""Loads the examples by path, the way a user runs them: as standalone scripts, not as a package."""

import importlib.util
from pathlib import Path
from types import ModuleType

EXAMPLES = Path(__file__).resolve().parent.parent
NAMES = ("arc_bot", "hikari_bot", "core_only")


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
