"""Seed hooks. Modules register idempotent foundation seeders (warehouses, default theme, pages, zones…).

Demo catalog data is only created when explicitly requested and is labelled `demo` everywhere it appears.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

SeedFn = Callable[[Session, object, bool], dict | None]
_HOOKS: list[tuple[int, str, SeedFn]] = []


def seed_hook(order: int, name: str):
    def deco(fn: SeedFn):
        _HOOKS.append((order, name, fn))
        return fn

    return deco


def seed_foundation(db: Session, store, demo: bool = False) -> dict:
    from .modules.identity.api import ensure_builtin_roles

    ensure_builtin_roles(db, store.id)
    results = {}
    for _order, name, fn in sorted(_HOOKS, key=lambda h: h[0]):
        results[name] = fn(db, store, demo) or {}
    db.flush()
    return results
