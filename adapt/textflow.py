"""CV-based text re-wrapping — no fonts required, fully generic.

A rasterized text layer is segmented into individual word images using
projection profiles (horizontal projection -> text lines, vertical projection
within a line -> words). The words are then re-flowed to a target width, which
lets a wide single-line headline wrap onto several lines to fit a narrow banner
while preserving the original rendering (font, colour, weight) exactly.
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def _ink_mask(rgba: Image.Image, thr: int = 8) -> np.ndarray:
    a = np.array(rgba)
    if a.shape[2] == 4:
        return a[:, :, 3] > thr
    gray = np.array(rgba.convert("L"))
    return gray < 245


def _runs(flags: np.ndarray, merge_gap: int):
    """Runs of True in a 1-D boolean array, merging gaps <= merge_gap."""
    idx = np.where(flags)[0]
    if len(idx) == 0:
        return []
    runs = []
    start = prev = idx[0]
    for i in idx[1:]:
        if i - prev > merge_gap:
            runs.append((int(start), int(prev) + 1))
            start = i
        prev = i
    runs.append((int(start), int(prev) + 1))
    return runs


def ink_fill(rgba: Image.Image) -> float:
    """Fraction of the bounding box covered by ink (low => pure text)."""
    m = _ink_mask(rgba)
    return float(m.mean()) if m.size else 0.0


def segment_words(rgba: Image.Image):
    """Return a reading-ordered list of word crops: dicts with img/w/h."""
    mask = _ink_mask(rgba)
    H, W = mask.shape
    if H == 0 or W == 0:
        return []

    row_bands = _runs(mask.sum(axis=1) > 0, merge_gap=max(1, int(0.02 * H)))
    words = []
    for (y0, y1) in row_bands:
        row_h = y1 - y0
        col = mask[y0:y1].sum(axis=0)
        # A gap wider than ~20% of the line height is a word break, not a letter
        # gap. Scales automatically with text size -> no hardcoded pixel values.
        space_gap = max(2, int(0.20 * row_h))
        for (x0, x1) in _runs(col > 0, merge_gap=space_gap):
            words.append({"img": rgba.crop((x0, y0, x1, y1)),
                          "w": x1 - x0, "h": row_h})
    return words


def reflow_to_width(rgba: Image.Image, target_w: int, line_h: int,
                    align: str = "left", space_ratio: float = 0.32,
                    line_gap_ratio: float = 0.22, ss: int = 1) -> Image.Image:
    """Re-wrap ``rgba`` word-by-word into a block no wider than ``target_w``.

    ``line_h`` sets the line height in px (drives overall text size).
    Returns a transparent RGBA block sized to the wrapped text.

    ``ss`` supersamples the *rasterisation* only: the line breaks are always
    decided at ``target_w``/``line_h``, then drawn ``ss`` x larger. Deciding the
    wrap once and drawing it at any resolution is what keeps a layout stable —
    otherwise sub-pixel rounding at render scale can push a word onto a new line
    and silently change a layout that was measured at 1x.
    """
    words = segment_words(rgba)
    if not words:
        return rgba

    natural_h = float(np.median([w["h"] for w in words]))
    scale = line_h / max(1.0, natural_h)
    # Never let a single word exceed the column: cap the scale so the widest word
    # fits target_w. This guarantees no word is clipped, only wrapped/shrunk.
    widest = max(w["w"] for w in words)
    if widest * scale > target_w:
        scale = target_w / widest
    line_h = max(1, round(natural_h * scale))
    space = max(1, int(space_ratio * line_h))

    # Layout pass: integer word extents at 1x decide the line breaks. Integers
    # (not floats) on purpose — the resulting block width is then exactly the
    # sum this loop compared against, so re-wrapping a block at its own measured
    # width reproduces it identically. Layout plans depend on that: a saved box
    # width must always re-render as the same lines.
    lines, cur, cur_w = [], [], 0
    for i, w in enumerate(words):
        nw = max(1, round(w["w"] * scale))
        add = nw if not cur else space + nw
        if cur and cur_w + add > target_w:
            lines.append(cur)
            cur, cur_w = [], 0
            add = nw
        cur.append(i)
        cur_w += add
    if cur:
        lines.append(cur)

    # Raster pass: same breaks, drawn at ss x.
    rs = scale * ss
    space_r = max(1, int(space_ratio * line_h * ss))
    gap_r = max(1, int(line_gap_ratio * line_h * ss))
    sized = []
    for w in words:
        nw = max(1, round(w["w"] * rs))
        nh = max(1, round(w["h"] * rs))
        sized.append((w["img"].resize((nw, nh), Image.LANCZOS), nw, nh))

    line_h_each = [max(sized[i][2] for i in ln) for ln in lines]
    line_w_each = [sum(sized[i][1] for i in ln) + space_r * (len(ln) - 1)
                   for ln in lines]
    block_w = max(line_w_each)          # never clipped: the widest line always fits
    block_h = sum(line_h_each) + gap_r * (len(lines) - 1)

    canvas = Image.new("RGBA", (max(1, block_w), max(1, block_h)), (0, 0, 0, 0))
    y = 0
    for ln, lh, lw in zip(lines, line_h_each, line_w_each):
        if align == "center":
            x = (block_w - lw) // 2
        elif align == "right":
            x = block_w - lw
        else:
            x = 0
        for i in ln:
            img, nw, nh = sized[i]
            canvas.alpha_composite(img, (max(0, x), y + (lh - nh) // 2))
            x += nw + space_r
        y += lh + gap_r
    return canvas
