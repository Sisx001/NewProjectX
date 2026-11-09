"""Reads a directory of legacy exports: `<collection>.json` (JSON array) or `.ndjson`/`.jsonl`, MongoDB extended JSON tolerated.

Collections may carry the legacy `REBUILD_COLLECTION_PREFIX` (e.g. `v1_orders.json`).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

EXTS = (".json", ".ndjson", ".jsonl")


def flatten(v: Any) -> Any:
    """Collapse extended-JSON wrappers ($oid, $date, $numberLong…) to plain values."""
    if isinstance(v, dict):
        if len(v) == 1:
            (k, inner), = v.items()
            if k == "$oid":
                return inner
            if k == "$date":
                if isinstance(inner, dict) and "$numberLong" in inner:
                    import datetime as dt
                    return dt.datetime.fromtimestamp(int(inner["$numberLong"]) / 1000, dt.UTC).isoformat()
                return inner
            if k in ("$numberLong", "$numberInt"):
                return int(inner)
            if k in ("$numberDouble", "$numberDecimal"):
                return float(inner)
        return {k: flatten(x) for k, x in v.items() if k != "_id"}
    if isinstance(v, list):
        return [flatten(x) for x in v]
    return v


class Source:
    def __init__(self, directory: str | Path, prefix: str = ""):
        self.dir = Path(directory)
        if not self.dir.is_dir():
            raise FileNotFoundError(f"Source directory not found: {self.dir}")
        self.prefix = prefix

    def path(self, name: str) -> Path | None:
        for pre in dict.fromkeys([self.prefix, ""]):
            for ext in EXTS:
                p = self.dir / f"{pre}{name}{ext}"
                if p.is_file():
                    return p
        return None

    def available(self) -> list[str]:
        names = set()
        for p in self.dir.iterdir():
            if p.suffix in EXTS:
                n = p.stem
                names.add(n[len(self.prefix):] if self.prefix and n.startswith(self.prefix) else n)
        return sorted(names)

    def rows(self, name: str) -> Iterator[dict]:
        p = self.path(name)
        if p is None:
            return
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return
        data = json.loads(text) if text[0] == "[" else [json.loads(line) for line in text.splitlines() if line.strip()]
        for row in data:
            if isinstance(row, dict):
                yield flatten(row)

    def list(self, name: str) -> list[dict]:
        return list(self.rows(name))
