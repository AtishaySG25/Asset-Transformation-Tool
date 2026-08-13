"""Cutting one element into parts that can be placed independently.

A riskometer is a single PSD layer holding two gauges and their labels; a
"features" strip is one layer holding four icons. Re-laying that out for a
banner means being able to take the piece apart and put the bits side by side —
which the layout engine cannot do while it is one indivisible box.

A part is *not* a new element. It is another placement of the same element with
a different ``params.crop`` — fractions of the element's own image, which
:func:`adapt.tiles.crop_fractions` already renders and which keep their meaning
at any box size. So splitting costs no new rendering machinery and survives a
save/reload like everything else.

The cuts themselves are found from the artwork rather than guessed: ink is
projected onto an axis and the runs between transparent gaps are the parts. That
is the same projection-profile idea :mod:`adapt.textflow` uses to find words,
which is what makes it work on a graphic nobody has labelled.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .textflow import _ink_mask, _runs

# A gap has to be this much of the span before it counts as a division, so
# ordinary spacing inside one graphic does not shatter it into fragments.
MIN_GAP = 0.02
# Runs thinner than this are specks, not parts.
MIN_PART = 0.02
MAX_PARTS = 12

Rect = tuple[float, float, float, float]        # (l, t, r, b) as fractions


def _bands(mask: np.ndarray, axis: int, want: int | None) -> list[tuple[int, int]]:
    """Runs of ink along ``axis``, merged until at most ``want`` remain.

    ``axis=0`` collapses rows to find *columns* (a left-to-right split);
    ``axis=1`` collapses columns to find *rows*.
    """
    present = mask.any(axis=axis)
    span = len(present)
    if not span:
        return []
    runs = _runs(present, merge_gap=max(1, int(MIN_GAP * span)))
    runs = [(a, b) for a, b in runs if (b - a) >= MIN_PART * span]
    limit = min(want or MAX_PARTS, MAX_PARTS)

    # Too many pieces: repeatedly fuse the pair separated by the smallest gap,
    # so the divisions that survive are the most pronounced ones.
    while len(runs) > limit:
        i = min(range(len(runs) - 1), key=lambda k: runs[k + 1][0] - runs[k][1])
        runs[i:i + 2] = [(runs[i][0], runs[i + 1][1])]
    return runs


def _spread(runs: list[tuple[int, int]], span: int) -> list[tuple[float, float]]:
    """Turn ink runs into abutting fractional slices.

    The cut is placed midway between neighbouring runs rather than tight around
    the ink, so the parts tile the original exactly: dropped straight back into
    the same box they reproduce it, and nothing falls down a crack.
    """
    out = []
    for i, (a, b) in enumerate(runs):
        lo = 0 if i == 0 else (runs[i - 1][1] + a) / 2
        hi = span if i == len(runs) - 1 else (b + runs[i + 1][0]) / 2
        out.append((lo / span, hi / span))
    return out


def auto_parts(img: Image.Image, axis: str = "auto",
               want: int | None = None) -> list[Rect]:
    """Split ``img`` where its own artwork already divides.

    ``axis`` is ``"x"`` (side by side), ``"y"`` (stacked) or ``"auto"``, which
    takes whichever direction the picture actually separates in — and if both
    do, the one that yields more parts.
    """
    mask = _ink_mask(img.convert("RGBA"))
    if not mask.any():
        return []
    h, w = mask.shape
    cols = _bands(mask, 0, want) if axis in ("auto", "x") else []
    rows = _bands(mask, 1, want) if axis in ("auto", "y") else []

    if axis == "auto":
        use_x = len(cols) >= len(rows)
    else:
        use_x = axis == "x"
    runs, span = (cols, w) if use_x else (rows, h)
    if len(runs) < 2:
        return []
    slices = _spread(runs, span)
    return [(a, 0.0, b, 1.0) if use_x else (0.0, a, 1.0, b) for a, b in slices]


def grid_parts(cols: int, rows: int) -> list[Rect]:
    """An even ``cols`` x ``rows`` division — the fallback for artwork with no
    gaps to find, and the way to cut a photograph."""
    cols = max(1, min(int(cols), MAX_PARTS))
    rows = max(1, min(int(rows), MAX_PARTS))
    return [(c / cols, r / rows, (c + 1) / cols, (r + 1) / rows)
            for r in range(rows) for c in range(cols)]


def compose_crop(existing: Rect | None, part: Rect) -> Rect:
    """Nest a part's fractions inside a crop the placement already carries.

    A part is expressed against what the box currently *shows*, but the stored
    crop is against the element's whole image — so splitting something already
    cropped has to compose the two rather than replace one with the other.
    """
    if not existing:
        return tuple(float(v) for v in part)
    L, T, R, B = (float(v) for v in existing)
    l, t, r, b = part
    return (L + l * (R - L), T + t * (B - T),
            L + r * (R - L), T + b * (B - T))
