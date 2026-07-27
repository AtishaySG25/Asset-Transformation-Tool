"""adapt — transform one square master ad into exact-sized secondary assets.

Hybrid pipeline: layer-aware element extraction from a PSD (with an OpenCV
saliency/contour fallback for flat images), then per-format content-aware
cropping or element re-layout.
"""
from .pipeline import run, transform, effective_strategy
from .formats import FORMATS, Format, strategy_for

__all__ = ["run", "transform", "effective_strategy",
           "FORMATS", "Format", "strategy_for"]
