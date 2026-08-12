"""Load the master asset and extract its semantic elements.

Two entry points:

* :func:`load_psd`  — layer-aware extraction (the primary Hybrid path). Every
  ad element is its own named PSD group, so we get role + bounding box for free.
* :func:`load_flat` — fallback for a flat JPEG/PNG: there are no layers, so we
  fall back to pure-CV object detection (saliency + contours) to recover boxes.

Both then downscale to a *working resolution* (see :data:`MAX_WORK_DIM`): the
largest asset we ever emit is around 1200px, so carrying a 6482x3646 master
through every resize costs memory and time for detail that is thrown away.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image

from .elements import Element, assign_uids, classify
from . import log, saliency

# Longest side kept for layout work. Comfortably above the largest output
# (1200px) even before 2x supersampling, so nothing visible is lost.
MAX_WORK_DIM = 2400


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


# Compositing a region allocates several float32 RGBA buffers per layer, so a
# full-canvas composite of a group with seven sub-layers can cost gigabytes on a
# 24-megapixel master. Doing it in horizontal bands caps that — and measures
# *faster*, because the allocator is not thrashing.
MAX_BAND_PX = 6_000_000


def _scaled(img: Image.Image, k: float, resample=Image.LANCZOS) -> Image.Image:
    return img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))),
                      resample)


def _composite_scaled(node, box, k: float, mode: str, band: bool = False,
                      max_band_px: int = MAX_BAND_PX) -> Image.Image | None:
    """Composite ``node`` over ``box``, straight to working scale.

    ``band`` renders the region in horizontal strips instead of one piece, which
    is what keeps a group of seven full-canvas sub-layers from allocating
    gigabytes. It is opt-in because it is not free of consequences:

    * Ordinary layers can render *differently* under a partial viewport —
      effects and smart-object resampling are resolved against it — so a strip
      is not always a slice of the whole. Measured on the sample masters, only
      the background group is safe; the others differ on a few percent of
      visible pixels.
    * Each strip rounds its own target height, so content can shift by up to
      half a pixel. On the real masters the boundaries divide evenly and the
      result is identical; on a photographic backdrop it would be invisible
      either way.
    """
    l, t, r, b = box
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    tw, th = max(1, round(w * k)), max(1, round(h * k))
    bands = max(1, -(-(w * h) // max_band_px)) if band else 1
    if bands == 1:                                  # one pass, then scale
        img = node.composite(viewport=box)
        if img is None:
            return None
        img = img.convert(mode)
        return _scaled(img, k) if k < 1 and img.size != (tw, th) else img

    out = Image.new(mode, (tw, th), (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255))
    step = -(-h // bands)
    # A resampling filter reads a few source rows either side of each output row.
    # Cut the image into bare bands and those neighbours are missing at every
    # seam, which shows up as a visible line. So composite each band with an
    # overlap wide enough to cover the filter's reach and trim it off after.
    pad = int(3 / k) + 2 if k < 1 else 0
    for i in range(bands):
        y0, y1 = t + i * step, min(b, t + (i + 1) * step)
        if y1 <= y0:
            break
        yp0, yp1 = max(t, y0 - pad), min(b, y1 + pad)
        band = node.composite(viewport=(l, yp0, r, yp1))
        if band is None:                            # nothing of this layer here
            continue
        if band.size != (w, yp1 - yp0):
            # Not every node honours the viewport — PSDImage.composite() returns
            # the document's embedded preview and ignores it. Banding that would
            # tile the whole image down the canvas, so fall back to one pass.
            log.log(f"viewport ignored (got {band.size}, wanted {(w, yp1 - yp0)}) "
                    f"— compositing in one pass instead", 1)
            del band, out
            whole = node.composite()
            if whole is None:
                return None
            whole = whole.convert(mode)
            return _scaled(whole, k) if k < 1 else whole
        by0, by1 = round((y0 - t) * k), round((y1 - t) * k)
        bp0, bp1 = round((yp0 - t) * k), round((yp1 - t) * k)
        band = band.convert(mode)
        if band.size != (tw, max(1, bp1 - bp0)):
            band = band.resize((tw, max(1, bp1 - bp0)), Image.LANCZOS)
        if (bp0, bp1) != (by0, by1):                # drop the overlap margin
            band = band.crop((0, by0 - bp0, tw, by0 - bp0 + max(1, by1 - by0)))
        out.paste(band, (0, by0))
        del band
    return out


def to_working_size(source: Source, max_dim: int | None = None) -> Source:
    """Downscale a source in place to the working resolution.

    Masters are often far larger than any output. Scaling once here — rather
    than re-scaling the full-resolution pixels for every tile of every format —
    is the difference between a few hundred MB of working set and a few GB.
    """
    max_dim = max_dim or MAX_WORK_DIM
    longest = max(source.width, source.height)
    if longest <= max_dim:
        log.log(f"working size {source.width}x{source.height} (no downscale needed)", 1)
        return source

    k = max_dim / longest
    before = log.mb(source.composite) + log.mb(source.background) + \
        sum(log.mb(e.image) for e in source.elements)

    source.background = _scaled(source.background, k)
    source.composite = _scaled(source.composite, k)
    for el in source.elements:
        el.image = _scaled(el.image, k)
        l, t, r, b = el.bbox
        el.bbox = (round(l * k), round(t * k), round(r * k), round(b * k))
        # Keep the box and the pixels consistent — the layout maths uses both.
        el.bbox = (el.bbox[0], el.bbox[1],
                   el.bbox[0] + el.image.width, el.bbox[1] + el.image.height)

    ow, oh = source.width, source.height
    source.width, source.height = source.composite.size
    after = log.mb(source.composite) + log.mb(source.background) + \
        sum(log.mb(e.image) for e in source.elements)
    log.log(f"working size {ow}x{oh} -> {source.width}x{source.height} "
            f"(x{k:.3f}), pixels held {before:.0f}MB -> {after:.0f}MB", 1)
    return source


def load_psd(path: str, max_dim: int | None = None) -> Source:
    from psd_tools import PSDImage

    max_dim = max_dim or MAX_WORK_DIM
    size_mb = os.path.getsize(path) / 1024 ** 2
    with log.step(f"open {os.path.basename(path)} ({size_mb:.0f} MB)"):
        psd = PSDImage.open(path)
        W, H = psd.width, psd.height

    # Decide the working scale up front and composite straight into it, so no
    # full-resolution buffer is ever materialised.
    k = min(1.0, max_dim / max(W, H))
    if k < 1:
        log.log(f"master is {W}x{H}; compositing at working scale x{k:.3f} "
                f"-> {round(W * k)}x{round(H * k)}", 1)
    biggest = max((((l.bbox[2] - l.bbox[0]) * (l.bbox[3] - l.bbox[1])) / 1e6
                   for l in psd), default=0)
    if biggest > 8:
        # psd_tools renders each layer at the PSD's own resolution before we get
        # to scale it, and that is where the memory goes. Say so, rather than
        # letting the machine quietly start swapping.
        log.log(f"note: largest layer is {biggest:.0f} megapixels — compositing it "
                f"needs roughly {biggest * 0.15:.1f} GB transiently. This is "
                f"psd_tools working at the file's own resolution; it settles once "
                f"extraction finishes.", 1)
    viewport = (0, 0, W, H)

    with log.step(f"composite {W}x{H}"):
        composite = _composite_scaled(psd, viewport, k, "RGB")

    background = None
    elements: list[Element] = []

    def add_layer_as_element(layer, role):
        # Composite only within the layer's own (canvas-clipped) bbox — far
        # cheaper than re-rendering the whole canvas per element.
        l, t, r, b = layer.bbox
        l, t, r, b = max(0, l), max(0, t), min(W, r), min(H, b)
        if r <= l or b <= t:
            log.log(f"skip {layer.name!r}: empty after clipping to canvas", 1)
            return
        local = _composite_scaled(layer, (l, t, r, b), k, "RGBA")
        if local is None:
            log.log(f"skip {layer.name!r}: nothing composited", 1)
            return
        ys, xs = np.where(np.array(local)[:, :, 3] > 4)
        if len(xs) == 0:
            log.log(f"skip {layer.name!r}: fully transparent", 1)
            return
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        img = local.crop((x0, y0, x1, y1))
        # Boxes live in working-scale coordinates, alongside the pixels.
        bbox = (round(l * k) + x0, round(t * k) + y0,
                round(l * k) + x1, round(t * k) + y1)
        elements.append(Element(role=role, name=layer.name, image=img, bbox=bbox,
                                is_type=_has_type(layer)))
        log.log(f"element {layer.name!r} -> {role} {img.width}x{img.height} "
                f"at {bbox} ({log.mb(img):.0f}MB)", 1)

    with log.step(f"extract {len(list(psd))} top-level layers"):
        for layer in psd:
            role = classify(layer.name)

            if role == "background":
                # The one place banding pays: a background group stacks several
                # full-canvas sub-layers, and compositing them in one piece is
                # what makes a big master unusable on a modest machine.
                background = _composite_scaled(layer, viewport, k, "RGB", band=True)
                log.log(f"background layer {layer.name!r}", 1)
                continue

            # The footer group bundles the logo AND the disclaimer bar; split them
            # so the logo can travel independently into tight banners.
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
        log.log("no background layer found — using the flattened composite", 1)
        background = composite.copy()

    src = Source(composite.width, composite.height, background,
                 assign_uids(elements), composite)
    log.log(f"extracted {len(elements)} elements: "
            f"{', '.join(e.role for e in elements) or 'none'}", 1)
    log.log(f"pixels held: {log.mb(composite) + log.mb(background) + sum(log.mb(e.image) for e in elements):.0f}MB", 1)
    return src


def load_flat(path: str, max_dim: int | None = None) -> Source:
    """Flat-image fallback: detect salient objects with OpenCV (no layers)."""
    max_dim = max_dim or MAX_WORK_DIM
    with log.step(f"open flat image {os.path.basename(path)}"):
        img = Image.open(path).convert("RGB")
    W, H = img.size
    bgr = np.array(img)[:, :, ::-1].copy()

    with log.step("detect objects (contours)") as s:
        boxes = saliency.detect_objects(bgr)
        s["note"] = f"{len(boxes)} boxes"
    elements: list[Element] = []
    for i, (l, t, r, b) in enumerate(boxes):
        crop = img.crop((l, t, r, b)).convert("RGBA")
        elements.append(Element(role="object", name=f"object_{i}",
                                image=crop, bbox=(l, t, r, b)))
    return to_working_size(
        Source(W, H, img.copy(), assign_uids(elements), img), max_dim)


def load(path: str, max_dim: int | None = None) -> Source:
    with log.step(f"load {path}") as s:
        src = (load_psd if os.path.splitext(path)[1].lower() == ".psd"
               else load_flat)(path, max_dim)
        s["note"] = f"{src.width}x{src.height}, {len(src.elements)} elements"
    return src
