"""Pytest path isolation so tests always import the checkout under test."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CHRON_ROOT = Path(__file__).resolve().parent.parent

if str(CHRON_ROOT) not in sys.path:
    sys.path.insert(0, str(CHRON_ROOT))
if str(CHRON_ROOT.parent) not in sys.path:
    sys.path.insert(1, str(CHRON_ROOT.parent))

if "chron" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "chron",
        str(CHRON_ROOT / "__init__.py"),
        submodule_search_locations=[str(CHRON_ROOT)],
    )
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules["chron"] = mod
        spec.loader.exec_module(mod)
