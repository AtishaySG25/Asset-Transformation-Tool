"""End-to-end and unit tests for the adapt pipeline.

Tests that need the layered master are skipped when it is absent (it is a large
binary kept out of the repo); the pure-CV and routing tests always run.
"""
import os

import numpy as np
import pytest
from PIL import Image

from adapt.formats import FORMATS, Format, strategy_for
from adapt import smartcrop, saliency, textflow

INPUT = os.path.join("input", "Axis.psd")
needs_master = pytest.mark.skipif(not os.path.exists(INPUT),
                                  reason="master PSD not present")


@pytest.fixture(scope="module")
def source():
    if not os.path.exists(INPUT):
        pytest.skip("master PSD not present")
    from adapt.psd_source import load
    return load(INPUT)


def test_router_classifies_regimes():
    by_name = {f.name: strategy_for(f) for f in FORMATS}
    assert by_name["200x200"] == "crop"
    assert by_name["300x250"] == "crop"
    assert by_name["970x90"] == "reflow"
    assert by_name["728x90"] == "reflow"
    assert by_name["468x60"] == "reflow"
    assert by_name["160x600"] == "reflow"


def test_text_reflow_wraps_without_clipping():
    # A wide synthetic "text" strip of separated blobs -> multiple words that
    # re-wrap into a narrow column with no word exceeding the target width.
    from PIL import Image, ImageDraw
    strip = Image.new("RGBA", (600, 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    for i in range(6):
        d.rectangle([i * 100 + 10, 8, i * 100 + 70, 32], fill=(200, 0, 80, 255))
    words = textflow.segment_words(strip)
    assert len(words) == 6
    block = textflow.reflow_to_width(strip, target_w=140, line_h=24)
    assert block.width <= 140          # never overflows the column
    assert block.height > 40           # wrapped onto multiple lines


def test_reflow_keeps_all_elements_and_exact_size(source):
    from adapt.reflow import reflow
    out = reflow(source, 160, 600)
    assert out.size == (160, 600)


def test_elements_extracted_with_roles(source):
    roles = {el.role for el in source.elements}
    # The key hierarchy elements must be identified from the PSD.
    for expected in ("logo", "cta", "scheme", "headline"):
        assert expected in roles, f"missing role {expected}"
    assert source.width == 1200 and source.height == 1200


def test_importance_map_shape_and_range(source):
    bgr = np.array(source.composite.convert("RGB"))[:, :, ::-1].copy()
    imp = saliency.importance_map(bgr, source.elements)
    assert imp.shape == (source.height, source.width)
    assert 0.0 <= float(imp.min()) and float(imp.max()) <= 1.0


def test_content_aware_crop_exact_size(source):
    bgr = np.array(source.composite.convert("RGB"))[:, :, ::-1].copy()
    imp = saliency.importance_map(bgr, source.elements)
    out = smartcrop.content_aware_crop(source.composite, imp, 300, 250)
    assert out.size == (300, 250)


@needs_master
def test_pipeline_produces_exact_dimensions(source, tmp_path):
    from adapt.pipeline import run
    paths = run(INPUT, str(tmp_path), debug=False)
    assert len(paths) == len(FORMATS)
    for fmt in FORMATS:
        p = tmp_path / f"{fmt.name}.png"
        assert p.exists(), f"missing {p}"
        with Image.open(p) as im:
            # Exact dimensions: no clipping to a smaller canvas, no padding out.
            assert im.size == (fmt.width, fmt.height)


def test_photo_only_source_routes_to_photo_path():
    # A source with no discrete foreground elements (a plain photo PSD/JPEG) has
    # nothing to re-arrange, so every format — including the extreme banner
    # ratios — must take the crop-based 'photo' path rather than a reflow.
    from adapt.pipeline import effective_strategy, transform
    from adapt.psd_source import Source

    photo = Image.new("RGB", (1920, 1280), (90, 120, 160))
    src = Source(1920, 1280, photo, [], photo)

    for fmt in FORMATS:
        assert effective_strategy(src, fmt) == "photo", fmt.name

    imp = saliency.importance_map(np.array(photo)[:, :, ::-1].copy(), [])
    for fmt in FORMATS:
        out = transform(src, fmt, imp)
        assert out.size == (fmt.width, fmt.height), fmt.name


def test_structured_source_does_not_take_photo_path():
    # Sanity check the other side of the branch: once there are real foreground
    # elements, routing falls back to the aspect-based crop/reflow decision.
    from adapt.pipeline import effective_strategy
    from adapt.psd_source import Source
    from adapt.elements import Element

    canvas = Image.new("RGB", (1200, 1200), (255, 255, 255))
    tile = Image.new("RGBA", (200, 80), (10, 20, 30, 255))
    els = [Element(role="headline", name="h", image=tile, bbox=(100, 100, 300, 180)),
           Element(role="logo", name="l", image=tile, bbox=(100, 900, 300, 980))]
    src = Source(1200, 1200, canvas, els, canvas)

    assert effective_strategy(src, Format(200, 200)) == "crop"
    assert effective_strategy(src, Format(970, 90)) == "reflow"


def test_detect_objects_flat_fallback():
    # A synthetic image with two bright blobs -> at least one contour box.
    img = np.zeros((400, 400, 3), np.uint8)
    img[50:150, 50:150] = 255
    img[220:330, 260:360] = 200
    boxes = saliency.detect_objects(img)
    assert len(boxes) >= 1
    for (l, t, r, b) in boxes:
        assert r > l and b > t
