"""Bangladesh divisions → districts → upazilas (English + Bangla). Static, cached, case-insensitive lookups."""
from __future__ import annotations

import functools
import json
from pathlib import Path

DATA = Path(__file__).parent / "data" / "bd_locations.json"


@functools.lru_cache
def tree() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


@functools.lru_cache
def _index() -> dict:
    districts: dict[str, dict] = {}
    for dv in tree()["divisions"]:
        for ds in dv["districts"]:
            entry = {"name": ds["name"], "bn_name": ds["bn_name"], "division": dv["name"],
                     "upazilas": {u["name"].lower(): u["name"] for u in ds["upazilas"]}}
            districts[ds["name"].lower()] = entry
            districts[ds["bn_name"]] = entry
    return districts


def resolve(district: str | None, upazila: str | None = None) -> dict | None:
    """Return canonical {division, district, upazila} or None when the district is unknown.

    An unknown upazila yields upazila=None (callers decide whether that is acceptable)."""
    if not district:
        return None
    d = _index().get(district.strip().lower()) or _index().get(district.strip())
    if d is None:
        return None
    up = d["upazilas"].get((upazila or "").strip().lower()) if upazila else None
    return {"division": d["division"], "district": d["name"], "upazila": up}


def districts_of(division: str) -> list[str]:
    for dv in tree()["divisions"]:
        if dv["name"].lower() == division.lower():
            return [d["name"] for d in dv["districts"]]
    return []
