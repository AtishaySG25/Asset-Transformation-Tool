"""Load a master creative into a flat list of semantic elements.

Today only layered PSDs are supported (that's the assignment input). A flat
JPEG/PNG path is stubbed via `FlatImageLoader` so the future "PSD or image"
requirement is a small addition rather than a rewrite: everything downstream
only depends on `LoadedAsset`.

Group handling is the subtle part. Designers group a whole semantic unit (a
"cta" group = button + label + icon) which we want to keep as ONE element, but
they also bundle unrelated things into "footer"/"logo" bands (logo + legal
text) which we must SPLIT to isolate a clean logo. So we recurse into a group
only when its name reads as a logo/footer/disclaimer band (or is unnamed);
cohesive groups are composited whole.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image
from psd_tools import PSDImage

from .classifier import name_hint
from .element import ElementType, SemanticElement

logger = logging.getLogger(__name__)

# Groups with these name-hints are treated as bands to split, not cohesive
# units (e.g. a "footer"/"logo" band bundling the logo with legal text). A
# "background" group, by contrast, is composited whole into one backdrop.
_RECURSE_TYPES = {ElementType.LOGO, ElementType.DISCLAIMER}
_MAX_DEPTH = 3


@dataclass
class LoadedAsset:
    composite: Image.Image                 # flattened RGB master
    size: Tuple[int, int]                  # (width, height)
    elements: List[SemanticElement] = field(default_factory=list)
    has_layers: bool = True


def _tighten(img: Image.Image, bbox: Tuple[int, int, int, int]):
    """Trim fully-transparent margins so artwork is tightly bounded.

    Returns (tight_image, adjusted_bbox) or None if the layer is empty. bbox is
    the layer's source-space box; we shift it by however much we trimmed.
    """
    if img is None:
        return None
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    alpha_box = img.getbbox()  # bounding box of non-zero (incl. non-transparent) pixels
    if alpha_box is None:
        return None
    img = img.crop(alpha_box)
    if img.width == 0 or img.height == 0:
        return None
    l, t, r, b = bbox
    nl, nt = l + alpha_box[0], t + alpha_box[1]
    adj = (nl, nt, nl + img.width, nt + img.height)
    return img, adj


def _clip_bbox(bbox, w, h):
    l, t, r, b = bbox
    return (max(0, min(l, w)), max(0, min(t, h)),
            max(0, min(r, w)), max(0, min(b, h)))


def _extract(container, canvas_w, canvas_h, depth=0) -> List[SemanticElement]:
    """Walk a PSD group, emitting semantic elements per the split/keep rule."""
    elements: List[SemanticElement] = []
    for layer in container:
        try:
            if not layer.visible:
                continue
        except Exception:
            pass

        is_group = getattr(layer, "is_group", lambda: False)()
        hint = name_hint(layer.name)

        if is_group and depth < _MAX_DEPTH and (hint in _RECURSE_TYPES or hint is None):
            child = _extract(layer, canvas_w, canvas_h, depth + 1)
            if child:
                elements.extend(child)
                continue
            # Fall through: composite the whole group if recursion found nothing.

        try:
            img = layer.composite()
        except Exception as exc:
            logger.warning("Could not composite layer %r: %s", layer.name, exc)
            continue

        tightened = _tighten(img, tuple(layer.bbox))
        if tightened is None:
            continue
        tight_img, adj_bbox = tightened

        el = SemanticElement(
            name=layer.name,
            image=tight_img,
            bbox=_clip_bbox(adj_bbox, canvas_w, canvas_h),
            kind=str(layer.kind),
        )
        elements.append(el)
    return elements


class PSDLoader:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> LoadedAsset:
        psd = PSDImage.open(self.path, ignore_errors=True)
        w, h = psd.size
        composite = psd.composite().convert("RGB")
        elements = _extract(psd, w, h)
        logger.info("Loaded PSD %s (%dx%d) -> %d elements", self.path, w, h, len(elements))
        return LoadedAsset(composite=composite, size=(w, h), elements=elements, has_layers=True)


class FlatImageLoader:
    """Fallback for flat JPEG/PNG inputs (no layer semantics available)."""
    def __init__(self, path: str):
        self.path = path

    def load(self) -> LoadedAsset:
        img = Image.open(self.path).convert("RGB")
        logger.info("Loaded flat image %s (%dx%d)", self.path, *img.size)
        return LoadedAsset(composite=img, size=img.size, elements=[], has_layers=False)


def load_asset(path: str) -> LoadedAsset:
    ext = os.path.splitext(path)[1].lower()
    loader = PSDLoader(path) if ext == ".psd" else FlatImageLoader(path)
    return loader.load()
