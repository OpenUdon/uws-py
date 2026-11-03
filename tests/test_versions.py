"""G2: version admission and SemVer gates."""

from __future__ import annotations

import itertools

import pytest

from openudon_uws.versions import (
    PUBLISHED_UWS_VERSIONS,
    compare_semver,
    is_valid_uws_version,
    parse_semver,
    supports_version_at_least,
)


def test_published_versions_match_vendored_schemas() -> None:
    from importlib.resources import files

    shipped = {p.name.removesuffix(".json") for p in files("openudon_uws").joinpath("_schemas").iterdir()}
    assert set(PUBLISHED_UWS_VERSIONS) == {v for v in shipped if v[0].isdigit()}


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("1.13.0", True),
        ("1.0.0-rc.1", True),
        ("1.0.0-a+b", True),
        ("1.0", False),
        ("2.0.0", False),
        ("1.0.0\n", False),
        ("01.0.0", False),
        ("1.01.0", False),
        ("1.0.0-01", False),
        ("1.١.0", False),
    ],
)
def test_valid_uws_version(version: str, valid: bool) -> None:
    assert is_valid_uws_version(version) is valid


def test_prerelease_precedence() -> None:
    order = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    parsed = [parse_semver(v) for v in order]
    for a, b in itertools.pairwise(parsed):
        assert a is not None and b is not None
        assert compare_semver(a, b) == -1


def test_supports_version_at_least() -> None:
    assert supports_version_at_least("1.13.0", 1, 9)
    assert not supports_version_at_least("1.9.0-rc.1", 1, 9)
    assert not supports_version_at_least("garbage", 1, 0)
