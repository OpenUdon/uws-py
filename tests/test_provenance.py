"""G0: vendored upstream files are unchanged."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_vendored_files_match_upstream_manifest() -> None:
    spec = importlib.util.spec_from_file_location("provenance", ROOT / "scripts" / "provenance.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check() == []
