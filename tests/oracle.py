"""Go oracle harness for gate G6.

The oracle (``tools/go-oracle``) runs the upstream Go reference implementation at the pinned commit. It is built
once per test session into ``.oracle-cache/<hash>/`` where the hash covers its source, so a change to ``main.go``,
``go.mod`` or ``go.sum`` always rebuilds it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ORACLE_SRC = ROOT / "tools" / "go-oracle"
CACHE = ROOT / ".oracle-cache"


@dataclass(frozen=True)
class Verdict:
    file: str
    kind: str
    valid: bool
    error: str
    value: Any


class OracleUnavailable(RuntimeError):
    pass


def source_hash() -> str:
    digest = hashlib.sha256()
    for name in ("main.go", "go.mod", "go.sum"):
        digest.update(name.encode())
        digest.update((ORACLE_SRC / name).read_bytes())
    return digest.hexdigest()[:16]


def build() -> Path:
    if shutil.which("go") is None:
        raise OracleUnavailable("go is not installed")
    binary = CACHE / source_hash() / "go-oracle"
    if binary.exists():
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GOTOOLCHAIN": os.environ.get("GOTOOLCHAIN", "auto")}
    proc = subprocess.run(
        ["go", "build", "-o", str(binary), "."],
        cwd=ORACLE_SRC,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if proc.returncode != 0 or not binary.exists():
        raise OracleUnavailable(f"go build failed: {proc.stderr.strip()[-2000:]}")
    return binary


class Oracle:
    def __init__(self, binary: Path) -> None:
        self.binary = binary

    def run(self, kind: str, files: list[Path]) -> dict[str, Verdict]:
        """Runs a batch; if the Go process dies, reruns each file alone and reports crashing inputs.

        A crashing input gets ``valid=False`` and ``error="crash: ..."``: Go has no verdict for it.
        """
        try:
            return self._run(kind, files, 900)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if len(files) == 1:
                raise
        out: dict[str, Verdict] = {}
        for f in files:
            try:
                out.update(self._run(kind, [f], 120))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                out[str(f)] = Verdict(str(f), kind, False, f"crash: {type(exc).__name__}", None)
        return out

    def _run(self, kind: str, files: list[Path], timeout: int) -> dict[str, Verdict]:
        payload = "".join(f"{kind}\t{f}\n" for f in files)
        proc = subprocess.run(
            [str(self.binary)], input=payload.encode(), capture_output=True, timeout=timeout, check=True
        )
        out: dict[str, Verdict] = {}
        for line in proc.stdout.decode("utf-8").split("\n"):
            if not line.strip():
                continue
            raw = json.loads(line)
            out[raw["file"]] = Verdict(
                raw["file"], raw.get("kind", kind), bool(raw.get("valid")), raw.get("error", ""), raw.get("value")
            )
        return out

    def run_one(self, kind: str, file: Path) -> Verdict:
        return self.run(kind, [file])[str(file)]
