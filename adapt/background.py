"""Background fill: place source imagery inside an exact target box.

The box is a *viewport* onto the image. The image is scaled to ``zoom`` times the
scale that would just cover the box, and ``focus`` picks which point of the image
sits at the viewport's centre.

``zoom=1`` is the original behaviour — cover the box exactly and centre-crop the
overflow, so there is never a gap. ``zoom<1`` pulls back so *more* of the image
is visible than a cover crop allows, which is the only way to use a wide
composition inside a narrow banner or skyscraper: at 160x600 a cover crop of a
1200x1200 master can only ever show 27% of its width.
"""
from __future__ import annotations

from PIL import Image


def cover_scale(sw: int, sh: int, tw: int, th: int) -> float:
    """The scale at which a (sw, sh) image exactly covers a (tw, th) box."""
    return max(tw / max(1, sw), th / max(1, sh))


def _offset(f: float | None, n: int, t: int) -> int:
    """Where to start reading the scaled image so ``f`` lands at the box centre.

    Clamped to ``[min(0, n - t), max(0, n - t)]``. When the image covers the box
    that is ``[0, n - t]``, which forbids a gap opening at either edge; when it
    is smaller than the box it is ``[n - t, 0]``, which lets the image sit
    anywhere inside the box but not drift out of it.
    """
    lo, hi = min(0, n - t), max(0, n - t)
    if f is None:
        return (n - t) // 2 if n >= t else max(lo, min((n - t) // 2, hi))
    return max(lo, min(int(round(f * n - t / 2)), hi))


def fill_background(bg_rgb: Image.Image, tw: int, th: int,
                    focus: tuple[float, float] | None = None,
                    zoom: float = 1.0,
                    pad: tuple[int, int, int] | None = None) -> Image.Image:
    """Return a ``tw`` x ``th`` image showing ``bg_rgb`` scaled and positioned.

    ``focus`` is an optional (fx, fy) in [0,1] biasing which part of the image is
    kept. ``zoom`` multiplies the cover scale. ``pad`` fills any area the imagery
    no longer reaches (only possible below ``zoom=1``); ``None`` leaves it
    transparent so whatever sits behind the placement shows through.

    Fully covered results come back RGB, exactly as before; only a pulled-back
    one is RGBA.
    """
    sw, sh = bg_rgb.size
    tw, th = max(1, int(tw)), max(1, int(th))
    scale = cover_scale(sw, sh, tw, th) * max(0.01, float(zoom))
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))

    fx, fy = (None, None) if focus is None else focus
    left, top = _offset(fx, nw, tw), _offset(fy, nh, th)
    resized = bg_rgb.resize((nw, nh), Image.LANCZOS)

    if nw >= tw and nh >= th:                     # covers the box: plain crop
        return resized.crop((left, top, left + tw, top + th)).convert("RGB")

    if pad is None:
        out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        out.paste(resized.convert("RGBA"), (-left, -top))
    else:
        out = Image.new("RGB", (tw, th), tuple(pad))
        out.paste(resized.convert("RGB"), (-left, -top))
    return out
