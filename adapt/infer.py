"""Role inference for masters whose layers are not usefully named.

:func:`adapt.elements.classify` reads a role out of a layer's *name*, which works
when a designer has named things (``headline``, ``cta``, ``footer-logo``) and
tells us nothing at all when they have not — a PSD full of ``Vector Smart
Object``, ``L copy 2`` and ``Group 1`` classifies as zero roles, so every element
arrives as a generic ``object``: the layout engine has no hierarchy to work with,
the background is handed over as a draggable foreground box, and the footer
never forms.

This module recovers what it can from *structure* instead — where a layer sits in
the stack, how much of the canvas it covers, its aspect ratio, and whether it is
backed by type. It only ever fills in roles that the name-based pass left blank,
so a well-named master is completely unaffected.

Inference is a guess, and it says so: every role it assigns is logged, and the
editor lets the user override any of them (see ``/api/roles``). The point is to
start from something usable rather than from sixteen anonymous boxes.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import log

# A layer has to cover almost the whole canvas to be read as the backdrop.
BG_COVERAGE = 0.92
# ...and sit in the bottom of the stack, not float above the content.
BG_STACK_FRACTION = 0.34
# The footer/disclaimer strip: wide, and low on the canvas.
FOOTER_MIN_WIDTH = 0.60
FOOTER_MIN_CY = 0.75
# A logo lock-up reads as a wide, short bar near the bottom.
LOGO_MIN_ASPECT = 4.0
LOGO_MAX_AREA = 0.16
LOGO_MIN_CY = 0.68


@dataclass
class LayerInfo:
    """What we know about one top-level layer before extracting its pixels."""

    index: int                       # position in the stack, 0 == bottom
    name: str
    bbox: tuple[int, int, int, int]  # already clipped to the canvas
    has_type: bool
    role: str | None = None          # from the name, if the name said anything

    @property
    def width(self) -> int:
        return max(0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> int:
        return max(0, self.bbox[3] - self.bbox[1])

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)


def infer_roles(layers: list[LayerInfo], W: int, H: int) -> dict[int, str]:
    """Roles for the layers the name pass could not place, by index.

    Returns only *inferred* entries — a layer whose name already gave a role is
    never reconsidered, so this can be layered onto the existing pass without
    changing any master that is properly named.
    """
    canvas = max(1, W * H)
    unknown = [x for x in layers if x.role is None]
    if not unknown:
        return {}

    out: dict[int, str] = {}

    # -- background --------------------------------------------------------
    # Full-canvas layers low in the stack are backdrop, whatever they are called.
    # This runs even when a name already matched, because the common arrangement
    # is a plain fill *named* "Background" with the actual artwork sitting on top
    # of it — and that artwork is every bit as much the backdrop. Leaving it
    # unclaimed is what turns a 1200x1200 painting into a draggable box.
    cutoff = max(1, int(len(layers) * BG_STACK_FRACTION))
    for x in unknown:
        if x.index < cutoff and x.area >= BG_COVERAGE * canvas:
            out[x.index] = "background"

    # -- disclaimer / footer text ------------------------------------------
    # Wide and low, and backed by type: the statutory strip along the bottom.
    footers = [x for x in unknown if x.index not in out and x.has_type
               and x.width >= FOOTER_MIN_WIDTH * W and x.cy >= FOOTER_MIN_CY * H]
    for x in footers:
        out[x.index] = "disclaimer"

    # -- headline / subheadline --------------------------------------------
    # The remaining type layers, largest first. Two is as far as this is worth
    # taking: beyond a headline and a subheadline the engine treats text alike.
    texts = sorted((x for x in unknown if x.index not in out and x.has_type),
                   key=lambda x: -x.area)
    for role, x in zip(("headline", "subheadline"), texts):
        out[x.index] = role

    # -- logo lock-up -------------------------------------------------------
    # A wide, short, non-type bar near the bottom — what plan_wide turns into a
    # full-width footer band. Only the best candidate, to avoid inventing two.
    bars = [x for x in unknown if x.index not in out and not x.has_type
            and x.aspect >= LOGO_MIN_ASPECT and x.cy >= LOGO_MIN_CY * H
            and x.area <= LOGO_MAX_AREA * canvas]
    if bars:
        out[max(bars, key=lambda x: x.width).index] = "logo"

    if out:
        by_index = {x.index: x for x in layers}
        log.log(f"no role names on {len(unknown)} layer(s) — inferred "
                f"{len(out)} from structure: "
                + ", ".join(f"{by_index[i].name!r}->{r}"
                            for i, r in sorted(out.items())), 1)
    return out
