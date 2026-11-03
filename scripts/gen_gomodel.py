"""Regenerates src/openudon_uws/_gomodel.json from the Go oracle's ``struct-model`` kind.

The model describes the Go types ``encoding/json`` decodes a ``uws1.Document`` into (field names and kinds, and
which types have a custom ``UnmarshalJSON`` that refuses unknown non-``x-`` fields). Gate G6 checks the committed
file against the oracle.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.oracle import Oracle, build  # noqa: E402

TARGET = ROOT / "src" / "openudon_uws" / "_gomodel.json"


def generate() -> dict[str, object]:
    verdict = Oracle(build()).run_one("struct-model", Path("-"))
    model: dict[str, object] = verdict.value
    return model


def main() -> int:
    TARGET.write_text(json.dumps(generate(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
