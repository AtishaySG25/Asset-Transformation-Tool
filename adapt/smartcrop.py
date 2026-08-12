"""Saliency-weighted content-aware cropping for near-square targets.

For formats whose aspect is close to the source, we keep the whole composition
and just trim the least-important margin. We slide a target-aspect window over
the fused importance map and pick the position that retains the most importance,
then resize the chosen window to the exact target dimensions.
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def _best_window(importance: np.ndarray, target_aspect: float):
    """Return (l, t, r, b): the max-importance window of the given aspect."""
    H, W = importance.shape
    src_aspect = W / H

    if target_aspect >= src_aspect:
        # Target is wider -> keep full width, trim height.
        cw = W
        ch = int(round(W / target_aspect))
        ch = min(ch, H)
        row = importance.sum(axis=1)
        cum = np.concatenate([[0.0], np.cumsum(row)])
        best_s, best = -1.0, 0
        for y in range(0, H - ch + 1):
            s = cum[y + ch] - cum[y]
            if s > best_s:
                best_s, best = s, y
        return (0, best, cw, best + ch)
    else:
        # Target is taller -> keep full height, trim width.
        ch = H
        cw = int(round(H * target_aspect))
        cw = min(cw, W)
        col = importance.sum(axis=0)
        cum = np.concatenate([[0.0], np.cumsum(col)])
        best_s, best = -1.0, 0
        for x in range(0, W - cw + 1):
            s = cum[x + cw] - cum[x]
            if s > best_s:
                best_s, best = s, x
        return (best, 0, best + cw, H)


def crop_box(importance: np.ndarray, tw: int, th: int):
    """The (l, t, r, b) window to keep for a (tw, th) target.

    When height is trimmed, a gentle top-favouring prior biases the window to keep
    the top of the ad (headline/branding) rather than slicing it off.
    """
    imp = importance
    if tw / th >= (imp.shape[1] / imp.shape[0]):
        # Trimming height: favour the top so the headline is retained.
        H = imp.shape[0]
        prior = np.linspace(1.9, 0.4, H, dtype=np.float32)[:, None]
        imp = imp * prior
    return _best_window(imp, tw / th)


def content_aware_crop(composite: Image.Image, importance: np.ndarray,
                       tw: int, th: int) -> Image.Image:
    """Crop ``composite`` to the best target-aspect window, resize to (tw, th)."""
    return composite.crop(crop_box(importance, tw, th)).resize((tw, th), Image.LANCZOS)


def fit_all(composite: Image.Image, tw: int, th: int) -> Image.Image:
    """Resize the *whole* composite to exactly (tw, th) — every element is kept
    and the frame is filled edge to edge (no crop, no letterbox bars). For
    near-square targets the aspect change is small, so distortion is minor."""
    return composite.resize((tw, th), Image.LANCZOS)
