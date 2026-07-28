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


def save(source_path: str, plans: dict[str, LayoutPlan],
         custom: list[Format] | None = None, out_dir: str = "output") -> str:
    p = path_for(source_path, out_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = {"source": source_path.replace("\\", "/"),
               "custom": [[f.width, f.height] for f in (custom or [])],
               "formats": {name: plan.to_json() for name, plan in plans.items()}}
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return p
