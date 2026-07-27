"""Load the master asset and extract its semantic elements.

Two entry points:

* :func:`load_psd`  — layer-aware extraction (the primary Hybrid path). Every
  ad element is its own named PSD group, so we get role + bounding box for free.
* :func:`load_flat` — fallback for a flat JPEG/PNG: there are no layers, so we
  fall back to pure-CV object detection (saliency + contours) to recover boxes.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image

from .elements import Element, classify
from . import saliency


class Source:
    """A loaded master asset: a background image plus foreground elements."""

    def __init__(self, width, height, background, elements, composite):
        self.width = width
        self.height = height
        self.background = background      # PIL RGB, full canvas
        self.elements = elements          # list[Element]
        self.composite = composite        # PIL RGB, the fully flattened render

    @property
    def aspect(self) -> float:
        return self.width / self.height


def _has_type(layer) -> bool:
    """True if the layer is (or contains) a PSD type layer — a text candidate."""
    if getattr(layer, "kind", "") == "type":
        return True
    if getattr(layer, "is_group", lambda: False)():
        return any(_has_type(c) for c in layer)
    return False


def load_psd(path: str) -> Source:
    from psd_tools import PSDImage

    psd = PSDImage.open(path)
    W, H = psd.width, psd.height
    composite = psd.composite().convert("RGB")
    viewport = (0, 0, W, H)

    background = None
    elements: list[Element] = []

    def add_layer_as_element(layer, role):
        # Composite only within the layer's own (canvas-clipped) bbox — far
        # cheaper than re-rendering the whole 1200x1200 canvas per element.
        l, t, r, b = layer.bbox
        l, t, r, b = max(0, l), max(0, t), min(W, r), min(H, b)
        if r <= l or b <= t:
            return
        local = layer.composite(viewport=(l, t, r, b)).convert("RGBA")
        arr = np.array(local)
        if arr.shape[2] < 4:
            return
        ys, xs = np.where(arr[:, :, 3] > 4)
        if len(xs) == 0:
            return
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        img = local.crop((x0, y0, x1, y1))
        bbox = (l + x0, t + y0, l + x1, t + y1)
        elements.append(Element(role=role, name=layer.name, image=img, bbox=bbox,
                                is_type=_has_type(layer)))

    for layer in psd:
        role = classify(layer.name)

        if role == "background":
            background = layer.composite(viewport=viewport).convert("RGB")
            continue

        # The footer group bundles the logo AND the disclaimer bar; split them so
        # the logo can travel independently into tight banners.
        if layer.name.lower() == "footer-logo" and layer.is_group():
            for child in layer:
                crole = classify(child.name)
                if crole == "logo":
                    add_layer_as_element(child, "logo")
                elif crole == "disclaimer" or getattr(child, "kind", "") == "type":
                    add_layer_as_element(child, "disclaimer")
            continue

        if role is None:
            role = "object"
        add_layer_as_element(layer, role)

    # Fall back to the flattened render if there was no explicit background group.
    if background is None:
        background = composite.copy()

    return Source(W, H, background, elements, composite)


def load_flat(path: str) -> Source:
    """Flat-image fallback: detect salient objects with OpenCV (no layers)."""
    img = Image.open(path).convert("RGB")
    W, H = img.size
    bgr = np.array(img)[:, :, ::-1].copy()

    boxes = saliency.detect_objects(bgr)
    elements: list[Element] = []
    for i, (l, t, r, b) in enumerate(boxes):
        crop = img.crop((l, t, r, b)).convert("RGBA")
        elements.append(Element(role="object", name=f"object_{i}",
                                image=crop, bbox=(l, t, r, b)))
    return Source(W, H, img.copy(), elements, img)


def load(path: str) -> Source:
    if os.path.splitext(path)[1].lower() == ".psd":
        return load_psd(path)
    return load_flat(path)
