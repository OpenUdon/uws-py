"""Vendored-file provenance (gate G0).

``--write`` records the SHA-256 of every vendored upstream file; ``--check`` verifies them and the digest of the
conformance corpus that UWS 1.13.0 §7.8 publishes. Upstream ``versions/`` lives in the package as
``src/openudon_uws/_schemas/`` (shipped in the wheel) and upstream ``testdata/`` lives in ``vendor/testdata/``.
Vendored files are upstream bytes and are never edited here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "vendor" / "UPSTREAM.json"
LOCATIONS = {
    "versions": ROOT / "src" / "openudon_uws" / "_schemas",
    "testdata": ROOT / "vendor" / "testdata",
}
UPSTREAM = {
    "repository": "https://github.com/OpenUdon/uws",
    "commit": "fab2179",
    "commitDate": "2026-10-25",
    "pinnedDigests": {
        "testdata/conformance/1.11.0.json": "51ea40b3f392125a22a95d515ee5002071dd8f4d98ab49415819b62f61ef26b5"
    },
}


def current() -> dict[str, str]:
    files: dict[str, str] = {}
    for prefix, base in LOCATIONS.items():
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = f"{prefix}/{path.relative_to(base).as_posix()}"
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def check() -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = current()
    problems: list[str] = []
    for key in ("repository", "commit", "pinnedDigests"):
        if manifest.get(key) != UPSTREAM[key]:
            problems.append(f"UPSTREAM.json {key} does not match the pinned upstream")
    recorded: dict[str, str] = manifest["files"]
    for name, digest in recorded.items():
        if actual.get(name) != digest:
            problems.append(f"{name}: digest mismatch or missing")
    for name in actual:
        if name not in recorded:
            problems.append(f"{name}: not recorded in UPSTREAM.json")
    pinned: dict[str, str] = UPSTREAM["pinnedDigests"]  # type: ignore[assignment]
    for name, digest in pinned.items():
        if actual.get(name) != digest:
            problems.append(f"{name}: does not match the upstream-pinned digest")
    return problems


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "--write":
        MANIFEST.write_text(json.dumps({**UPSTREAM, "files": current()}, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST.relative_to(ROOT)}")
        return 0
    if mode == "--check":
        problems = check()
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(f"provenance ok: {len(current())} vendored files")
        return 0
    print("usage: provenance.py --write | --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
