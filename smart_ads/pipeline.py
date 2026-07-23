"""End-to-end orchestration: master creative -> six ad assets."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import numpy as np

from .classifier import classify
from .config import FORMATS, Format, Strategy
from .debug import save_classification_overlay
from .element import SemanticElement
from .layout.crop import smart_crop
from .layout.relayout import make_background, relayout
from .layout.strategy import choose_strategy
from .loader import load_asset
from .renderer import compose, dominant_color, save
from .saliency import build_importance_map

logger = logging.getLogger(__name__)


def _classify_all(elements: List[SemanticElement], size) -> List[SemanticElement]:
    w, h = size
    for el in elements:
        el.type = classify(el, w, h)
    return elements


def run(input_path: str, output_dir: str, formats: Optional[List[Format]] = None,
        debug: bool = False, jpg: bool = False) -> List[str]:
    formats = formats or FORMATS
    logger.info("Loading %s", input_path)
    asset = load_asset(input_path)
    elements = _classify_all(asset.elements, asset.size)
    logger.info("Classified %d elements", len(elements))

    composite_rgb = asset.composite
    src_w, src_h = asset.size
    source_aspect = src_w / src_h
    ext = ".jpg" if jpg else ".png"

    # Only needed by the crop strategy; compute lazily to avoid the cost otherwise.
    imap = None
    fallback = dominant_color(composite_rgb) if elements else (240, 240, 240)

    written: List[str] = []
    for fmt in formats:
        strat = choose_strategy(fmt, source_aspect)
        out_path = os.path.join(output_dir, f"{fmt.name}{ext}")

        if strat is Strategy.CROP:
            if imap is None:
                imap = build_importance_map(np.array(composite_rgb), elements)
            result, _window = smart_crop(composite_rgb, imap, fmt)
            result = result.convert("RGBA")
        else:
            bg = make_background((fmt.width, fmt.height), elements, fallback)
            placements = relayout(fmt, elements)
            result = compose((fmt.width, fmt.height), bg, placements)

        save(result, out_path, jpg=jpg)
        logger.info("  %-8s via %-8s -> %s", fmt.name, strat.value, out_path)
        written.append(out_path)

    if debug:
        overlay = os.path.join(output_dir, "debug", "classification.png")
        save_classification_overlay(composite_rgb, elements, overlay)
        logger.info("Debug overlay -> %s", overlay)

    return written
