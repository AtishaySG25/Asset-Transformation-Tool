"""The core guarantee: relayout never places anything outside the canvas.

This is the property whose absence broke the previous attempt (reshaped parts
spilling past the aspect ratio). It must hold for every format and every element.
"""
import pytest
from PIL import Image

from smart_ads.config import FORMATS
from smart_ads.element import ElementType, SemanticElement
from smart_ads.layout.crop import smart_crop
from smart_ads.layout.relayout import _contain, relayout
from smart_ads.layout.strategy import choose_strategy
from smart_ads.config import Strategy
from smart_ads.saliency import build_importance_map
import numpy as np


def _el(name, etype, w, h):
    img = Image.new("RGBA", (w, h), (200, 50, 80, 255))
    return SemanticElement(name=name, image=img, bbox=(0, 0, w, h),
                           kind="pixel", type=etype)


def _sample_elements():
    # Deliberately awkward aspect ratios to stress the fitter.
    return [
        _el("background", ElementType.BACKGROUND, 1200, 1200),
        _el("logo", ElementType.LOGO, 1900, 110),      # extreme band
        _el("headline", ElementType.HEADLINE, 620, 130),
        _el("subtext", ElementType.SUBTEXT, 1000, 40),
        _el("cta", ElementType.CTA, 270, 130),
        _el("graph", ElementType.GRAPH, 650, 140),
    ]


@pytest.mark.parametrize("fmt", [f for f in FORMATS
                                 if choose_strategy(f) is Strategy.RELAYOUT])
def test_relayout_never_overflows(fmt):
    elements = _sample_elements()
    for img, x, y in relayout(fmt, elements):
        assert x >= 0 and y >= 0, f"{fmt.name}: negative offset ({x},{y})"
        assert x + img.width <= fmt.width, f"{fmt.name}: overflow right"
        assert y + img.height <= fmt.height, f"{fmt.name}: overflow bottom"


def test_relayout_produces_placements():
    # A relayout format should render at least logo + headline + cta.
    fmt = next(f for f in FORMATS if f.name == "970x90")
    assert len(relayout(fmt, _sample_elements())) >= 3


def test_contain_fits_box():
    fitted = _contain(Image.new("RGBA", (1000, 40)), 200, 90)
    assert fitted.width <= 200 and fitted.height <= 90


@pytest.mark.parametrize("fmt", [f for f in FORMATS
                                 if choose_strategy(f) is Strategy.CROP])
def test_crop_exact_dimensions(fmt):
    comp = Image.new("RGB", (1200, 1200), (120, 120, 120))
    imap = build_importance_map(np.array(comp), _sample_elements())
    result, _ = smart_crop(comp, imap, fmt)
    assert result.size == (fmt.width, fmt.height)
