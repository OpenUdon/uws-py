from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.oracle import Oracle, OracleUnavailable, build

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "testdata"


@pytest.fixture(scope="session")
def oracle() -> Oracle:
    """The Go oracle, built once before any differential test runs.

    Without Go the differential tests are skipped, unless ``UWS_REQUIRE_ORACLE=1`` (set in CI), in which case
    they fail.
    """
    try:
        return Oracle(build())
    except OracleUnavailable as exc:
        if os.environ.get("UWS_REQUIRE_ORACLE") == "1":
            pytest.fail(f"Go oracle is required but unavailable: {exc}")
        pytest.skip(f"Go oracle unavailable: {exc}")
