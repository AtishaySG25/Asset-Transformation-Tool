"""Persistence for manually-edited layouts.

Edits made in the web editor are written to ``<out_dir>/layouts/<stem>.json`` —
one file per master asset, one entry per output format, plus any custom sizes
the user added. Only formats the user actually touched are stored; everything
else falls back to the algorithmic plan, so a saved file stays small and
readable (and diffable in git).
"""
from __future__ import annotations

import json
import os

from .formats import Format
from .layout import LayoutPlan

DIRNAME = "layouts"


def path_for(source_path: str, out_dir: str = "output") -> str:
    stem = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(out_dir, DIRNAME, f"{stem}.json")


def _read(source_path: str, out_dir: str) -> dict:
    p = path_for(source_path, out_dir)
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load(source_path: str, out_dir: str = "output") -> dict[str, LayoutPlan]:
    """Saved plans for a master asset, keyed by format name ("970x90")."""
    data = _read(source_path, out_dir)
    return {name: LayoutPlan.from_json(d)
            for name, d in (data.get("formats") or {}).items()}


def load_custom(source_path: str, out_dir: str = "output") -> list[Format]:
    """Extra target sizes the user added for this master."""
    data = _read(source_path, out_dir)
    return [Format(int(w), int(h)) for w, h in (data.get("custom") or [])]


def load_roles(source_path: str, out_dir: str = "output") -> dict[str, str]:
    """Roles the user assigned by hand, keyed by ``"<index>:<layer name>"``.

    Keyed on both because neither alone is reliable: a PSD can carry four layers
    called ``Vector Smart Object``, so the name does not identify one, and the
    index moves if the file is re-saved with a layer added. Matching prefers the
    pair, then falls back to the name when it is unique (see :func:`apply_roles`).
    """
    return dict(_read(source_path, out_dir).get("roles") or {})


def role_key(index: int, name: str) -> str:
    return f"{index}:{name}"


def apply_roles(source, overrides: dict[str, str]) -> int:
    """Overlay hand-assigned roles onto a loaded source. Returns how many stuck."""
    if not overrides:
        return 0
    by_name: dict[str, list[int]] = {}
    for i, el in enumerate(source.elements):
        by_name.setdefault(el.name, []).append(i)

    hit = 0
    for key, role in overrides.items():
        idx, _, name = key.partition(":")
        target = None
        if idx.isdigit() and int(idx) < len(source.elements) \
                and source.elements[int(idx)].name == name:
            target = int(idx)                     # the exact layer we recorded
        elif len(by_name.get(name, [])) == 1:
            target = by_name[name][0]             # moved, but the name is unique
        if target is not None:
            source.elements[target].role = role
            hit += 1
    return hit


def save(source_path: str, plans: dict[str, LayoutPlan],
         custom: list[Format] | None = None, out_dir: str = "output",
         roles: dict[str, str] | None = None) -> str:
    p = path_for(source_path, out_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = {"source": source_path.replace("\\", "/"),
               "custom": [[f.width, f.height] for f in (custom or [])],
               "roles": dict(roles or {}),
               "formats": {name: plan.to_json() for name, plan in plans.items()}}
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return p
