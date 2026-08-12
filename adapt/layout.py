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
``photo_band``  the source background, filled into the box, optionally feathered
                at the left/right edges.
``color_bar``   a solid rectangle (footer bar, disclaimer strip, ...).
``base_image``  a full-frame image derived from the composite — used by the crop
                and photo strategies.

Both background kinds treat their box as a *viewport* onto the imagery, framed by
three shared params (see :func:`adapt.tiles.background_tile`): ``fit``
(``cover`` / ``contain`` / ``stretch``), ``zoom`` (a multiplier on that fit) and
``focus_x`` / ``focus_y`` (which point of the image sits at the box centre).
Their defaults reproduce the original behaviour exactly — ``cover`` for a band,
``stretch`` for a base image, both at ``zoom=1`` — so a plan saved before these
existed renders identically.
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
    uid: str = ""                   # Element.uid — the identity that survives
    name: str = ""                  # element name, for display
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
                "z": self.z, "element": self.element, "uid": self.uid,
                "name": self.name,
                "role": self.role, "visible": self.visible,
                "lock_aspect": self.lock_aspect, "opacity": round(self.opacity, 3),
                "params": dict(self.params)}

    @classmethod
    def from_json(cls, d: dict) -> "Placement":
        return cls(id=d["id"], kind=d["kind"],
                   x=float(d.get("x", 0)), y=float(d.get("y", 0)),
                   w=float(d.get("w", 0)), h=float(d.get("h", 0)),
                   z=int(d.get("z", 0)), element=d.get("element"),
                   # Left empty for a plan saved before uids existed: `rebind`
                   # fills it in, and until then `resolve_element` falls back to
                   # the index-and-name pair those plans do carry.
                   uid=d.get("uid", ""),
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
    """Readable placement id for an element. Not an identity — it encodes the
    index and the role, both of which can change under the placement (see
    :func:`rebind`), so it is reissued rather than relied on."""
    return f"el{idx}-{el.role}"


def resolve_element(source, p: Placement):
    """The Element a placement refers to, in decreasing order of confidence.

    1. the recorded index, if the element sitting there still agrees with the
       placement's identity — the only handle that separates two layers sharing
       a name, and correct whenever the extraction has not moved;
    2. the recorded uid — survives re-indexing, and unique;
    3. the recorded name — the best a plan saved before uids can offer;
    4. the bare index, when the placement carries no identity at all.
    """
    els = source.elements
    at = els[p.element] if p.element is not None and 0 <= p.element < len(els) else None
    if at is not None:
        if p.uid:
            if at.uid == p.uid:
                return at
        elif not p.name or at.name == p.name:
            return at
    if p.uid:
        hit = next((e for e in els if e.uid == p.uid), None)
        if hit is not None:
            return hit
    if p.name:
        return next((e for e in els if e.name == p.name), None)
    # An identity that matches nothing means the element is gone; falling back to
    # whatever now sits at the index is how a box ends up drawing a stranger.
    return None if p.uid else at


def rebind(plan: LayoutPlan, source) -> list[Placement]:
    """Re-point a saved plan's boxes at the source that is actually loaded.

    A plan records which element each box draws by index, name and role — none
    of which belong to the PSD. They belong to the *extraction*, so a change to
    it (different classification code, a layer consumed as the background, a
    different ``--max-dim``) leaves every plan saved beforehand describing its
    own boxes with labels that have since moved to other layers. Because plans
    are saved per format, only the formats touched since the change get the new
    labels: the same layer then reads as "Group 7 (object)" in 728x90 and
    "Footer (disclaimer)" in 970x90, and the editor — which draws a plain
    element straight from its index — shows the wrong artwork under the wrong
    name.

    So a plan is reconciled with the source every time it is loaded: every box is
    matched to an element and then relabelled from it, which is the one place a
    name or a role is allowed to come from. Returns the boxes whose element is no
    longer in the master at all.

    Matching is done for the plan as a whole rather than a box at a time, because
    a box on its own is not always enough to go on: a PSD can hold five layers
    called "Vector Smart Object", and resolving each box independently by name
    lands all five on the first of them. Confident matches are taken first and
    the ambiguous ones are then shared out among what is left.
    """
    els = source.elements
    boxes = [p for p in plan.placements if p.kind == "element"]
    bound: list[int | None] = [None] * len(boxes)
    claimed: set[int] = set()

    def claim(n: int, i: int):
        bound[n] = i
        claimed.add(i)

    def recorded(n: int) -> int:
        return len(els) if boxes[n].element is None else boxes[n].element

    # 1. The uid — unique by construction, so a hit needs no corroboration.
    by_uid = {el.uid: i for i, el in enumerate(els)}
    for n, p in enumerate(boxes):
        if p.uid and p.uid in by_uid:      # a box with no uid matches nothing here
            claim(n, by_uid[p.uid])

    # 2. The recorded index, where the element still there agrees by name — and
    #    that name belongs to one layer only. Agreement on a repeated name is
    #    weak evidence (a box can land on the wrong copy by coincidence, which
    #    then pushes its neighbours onto the wrong copies too), so those are
    #    left to step 3, which weighs the whole run at once.
    repeats: dict[str, int] = {}
    for el in els:
        repeats[el.name] = repeats.get(el.name, 0) + 1
    for n, p in enumerate(boxes):
        if bound[n] is not None or p.element is None:
            continue
        if (0 <= p.element < len(els) and p.element not in claimed
                and (not p.name or (els[p.element].name == p.name
                                    and repeats[p.name] == 1))):
            claim(n, p.element)

    # 3. Whatever is left, by name — shared out in recorded order against the
    #    unclaimed layers of that name in document order. Extraction preserves
    #    the master's own layer order even when the indices shift, so the n-th
    #    box of a repeated name is the n-th such layer.
    free: dict[str, list[int]] = {}
    for i, el in enumerate(els):
        if i not in claimed:
            free.setdefault(el.name, []).append(i)
    rest: dict[str, list[int]] = {}
    for n, p in enumerate(boxes):
        if bound[n] is None and p.name:
            rest.setdefault(p.name, []).append(n)
    for name, ns in rest.items():
        pool = free.get(name, [])
        order = sorted(ns, key=recorded)
        for n, i in zip(order, pool):
            claim(n, i)
        # More boxes of a name than there are layers of it: the plan draws one
        # element twice, which is what `plan_wide` used to do to a footer logo.
        # Keep them drawing the element their neighbours settled on, so the
        # renderer and this pass agree about what is on the canvas.
        spare = pool[-1] if pool else next(
            (i for i, el in enumerate(els) if el.name == name), None)
        if spare is not None:
            for n in order:
                if bound[n] is None:
                    claim(n, spare)

    lost = []
    for n, p in enumerate(boxes):
        if bound[n] is None:
            lost.append(p)
            continue
        el = els[bound[n]]
        p.element, p.uid, p.name, p.role = bound[n], el.uid, el.name, el.role
    reissue_ids(plan)
    return lost


def reissue_ids(plan: LayoutPlan) -> None:
    """Make every placement id current and unique.

    The editor keys selection, deletion, drag-to-reorder and backdrop pairing on
    the id, so a plan holding the same id twice — which one built before
    :func:`adapt.reflow.plan_wide` stopped placing a single element as both the
    footer bar and the disclaimer could — edits two boxes as one.
    """
    order = plan.ordered()
    want = [element_id(p.element, p) if p.kind == "element" and p.element is not None
            else p.id for p in order]

    # A backdrop is addressed as `bg-<the id of the box it sits behind>`, so it
    # has to follow that box when the box is renamed.
    fronted = {}
    for p, w in zip(order, want):
        fronted.setdefault(p.id, w)
    want = [f"bg-{fronted[p.id[3:]]}" if p.id.startswith("bg-") and p.id[3:] in fronted
            else w for p, w in zip(order, want)]

    taken: set[str] = set()
    for p, w in zip(order, want):
        pick, n = w, 2
        while pick in taken:
            pick, n = f"{w}-{n}", n + 1
        taken.add(pick)
        p.id = pick
