"""Declarative layout plans.

A :class:`LayoutPlan` is the *description* of one output format — a base colour
plus an ordered list of :class:`Placement` boxes — with no pixels in it. The
algorithmic layout engine emits a plan, the renderer turns a plan into the exact
target-sized PNG, and the web editor is nothing more than a plan editor. Because
a plan is plain JSON it round-trips to disk, so a hand-adjusted layout can be
re-rendered headlessly long after the browser is closed.

Placement kinds
---------------
``element``     one extracted :class:`~adapt.elements.Element`. ``params["mode"]``
                is ``"reflow"`` (text re-wrapped to the box width, see
                :func:`adapt.tiles.text_tile`) or ``"stretch"`` (scaled to the box).
``photo_band``  the source background, cover-filled into the box, optionally
                feathered at the left/right edges.
``color_bar``   a solid rectangle (footer bar, disclaimer strip, ...).
``base_image``  a full-frame image derived from the composite — used by the crop
                and photo strategies.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("element", "photo_band", "color_bar", "base_image")

# Strategy names a plan can be built under (see adapt.reflow / adapt.pipeline).
STRATEGIES = ("reflow-wide", "reflow-tall", "explode", "photo")


@dataclass
class Placement:
    """One box on the output canvas, in *output pixel* coordinates."""

    id: str
    kind: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    z: int = 0
    element: int | None = None      # index into Source.elements
    name: str = ""                  # element name — survives re-indexing
    role: str = ""
    visible: bool = True
    lock_aspect: bool = True
    opacity: float = 1.0            # <1 lets whatever is behind show through
    params: dict = field(default_factory=dict)
    # Tile cached by the planner so the first render costs nothing extra. Never
    # serialised; a plan loaded from JSON simply re-renders from `params`.
    tile: object = field(default=None, repr=False, compare=False)

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind,
                "x": round(self.x, 2), "y": round(self.y, 2),
                "w": round(self.w, 2), "h": round(self.h, 2),
                "z": self.z, "element": self.element, "name": self.name,
                "role": self.role, "visible": self.visible,
                "lock_aspect": self.lock_aspect, "opacity": round(self.opacity, 3),
                "params": dict(self.params)}

    @classmethod
    def from_json(cls, d: dict) -> "Placement":
        return cls(id=d["id"], kind=d["kind"],
                   x=float(d.get("x", 0)), y=float(d.get("y", 0)),
                   w=float(d.get("w", 0)), h=float(d.get("h", 0)),
                   z=int(d.get("z", 0)), element=d.get("element"),
                   name=d.get("name", ""), role=d.get("role", ""),
                   visible=bool(d.get("visible", True)),
                   lock_aspect=bool(d.get("lock_aspect", True)),
                   opacity=float(d.get("opacity", 1.0)),
                   params=dict(d.get("params") or {}))


@dataclass
class LayoutPlan:
    width: int
    height: int
    strategy: str = "reflow-wide"
    base_color: tuple[int, int, int] = (255, 255, 255)
    placements: list[Placement] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.width}x{self.height}"

    def add(self, p: Placement) -> Placement:
        """Append a placement, stacking it on top of the current top-most one."""
        p.z = len(self.placements)
        self.placements.append(p)
        return p

    def ordered(self) -> list[Placement]:
        """Placements in draw order (lowest z first)."""
        return sorted(self.placements, key=lambda p: p.z)

    def by_id(self, pid: str) -> Placement | None:
        return next((p for p in self.placements if p.id == pid), None)

    def to_json(self) -> dict:
        return {"width": self.width, "height": self.height,
                "strategy": self.strategy, "base_color": list(self.base_color),
                "placements": [p.to_json() for p in self.ordered()]}

    @classmethod
    def from_json(cls, d: dict) -> "LayoutPlan":
        return cls(width=int(d["width"]), height=int(d["height"]),
                   strategy=d.get("strategy", "reflow-wide"),
                   base_color=tuple(d.get("base_color", (255, 255, 255))),
                   placements=[Placement.from_json(p) for p in d.get("placements", [])])


def element_id(idx: int, el) -> str:
    """Stable placement id for an element — unique within a plan and readable."""
    return f"el{idx}-{el.role}"


def resolve_element(source, p: Placement):
    """The Element a placement refers to, matched by index then by name.

    Name matching keeps a saved layout working if the same PSD is re-extracted
    and the element order shifts.
    """
    els = source.elements
    if p.element is not None and 0 <= p.element < len(els):
        el = els[p.element]
        if not p.name or el.name == p.name:
            return el
    if p.name:
        return next((e for e in els if e.name == p.name), None)
    return None
