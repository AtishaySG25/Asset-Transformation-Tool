"""End-to-end and unit tests for the adapt pipeline.

Tests that need the layered master are skipped when it is absent (it is a large
binary kept out of the repo); the pure-CV and routing tests always run.
"""
import io
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

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


def test_plan_json_roundtrip_renders_identically(source):
    # A plan is the contract between the layout engine, the editor and the
    # exporter: sending it through JSON must not change a single pixel.
    from adapt.layout import LayoutPlan
    from adapt.pipeline import plan_for_format, render
    from adapt import saliency as sal

    imp = sal.importance_map(np.array(source.composite)[:, :, ::-1].copy(),
                             source.elements)
    for fmt in FORMATS:
        plan = plan_for_format(source, fmt, imp)
        direct = render(plan, source)
        revived = render(LayoutPlan.from_json(plan.to_json()), source)
        assert revived.size == (fmt.width, fmt.height)
        assert np.array_equal(np.array(direct), np.array(revived)), fmt.name


def test_text_boxes_reproduce_their_own_wrap(source):
    # Every re-wrapped text placement must re-render to exactly the box the plan
    # recorded — otherwise a saved layout would drift each time it is opened.
    from adapt.pipeline import plan_for_format
    from adapt import tiles
    from adapt.layout import resolve_element
    from adapt import saliency as sal

    imp = sal.importance_map(np.array(source.composite)[:, :, ::-1].copy(),
                             source.elements)
    checked = 0
    for fmt in FORMATS:
        for p in plan_for_format(source, fmt, imp).placements:
            if p.params.get("mode") != "reflow":
                continue
            el = resolve_element(source, p)
            tile = tiles.text_tile(el, round(p.w), p.params["line_h"],
                                   align=p.params.get("align", "left"))
            assert (tile.width, tile.height) == (round(p.w), round(p.h)), \
                f"{fmt.name}/{p.id}: {tile.size} != {(round(p.w), round(p.h))}"
            checked += 1
    assert checked, "no re-wrapped text found to check"


def test_manual_edits_survive_a_save_and_reload(source, tmp_path):
    # The editor's persistence path: move something, save, reload, re-render.
    from adapt import store
    from adapt.pipeline import plan_for_format, render
    from adapt import saliency as sal

    fmt = FORMATS[0]
    imp = sal.importance_map(np.array(source.composite)[:, :, ::-1].copy(),
                             source.elements)
    plan = plan_for_format(source, fmt, imp)
    moved = next(p for p in plan.placements if p.kind == "element")
    moved.x, moved.y = 7, 3
    store.save("input/Axis.psd", {fmt.name: plan}, out_dir=str(tmp_path))

    back = store.load("input/Axis.psd", str(tmp_path))[fmt.name]
    p2 = back.by_id(moved.id)
    assert (p2.x, p2.y) == (7, 3)
    out = render(back, source)
    assert out.size == (fmt.width, fmt.height)


def test_web_api_round_trip(source, tmp_path):
    # Smoke-test the editor's own contract end to end through Flask.
    from adapt.web import create_app
    app = create_app("input", str(tmp_path))
    c = app.test_client()

    assert c.get("/api/plan/970x90").status_code == 409      # nothing open yet
    assert c.post("/api/open", json={"path": INPUT}).status_code == 200

    man = c.get("/api/manifest").get_json()
    assert len(man["formats"]) == len(FORMATS)
    assert man["elements"] and all("role" in e for e in man["elements"])

    body = c.get("/api/plan/970x90").get_json()
    plan = body["plan"]
    assert body["edited"] is False and plan["placements"]

    # a tile for the first element renders
    assert c.get(f"/api/element/{man['elements'][0]['index']}.png").status_code == 200

    # the master asset is served rasterised, full size and scaled for the sidebar
    with Image.open(io.BytesIO(c.get("/api/master.png").data)) as im:
        assert im.size == (man["width"], man["height"])
    with Image.open(io.BytesIO(c.get("/api/master.png?w=320").data)) as im:
        assert im.width == 320

    # posting the untouched plan back reproduces the stored render byte for byte
    a = c.get("/api/render/970x90.png").data
    b = c.post("/api/render/970x90.png", json=plan).data
    assert a == b

    # edit -> save -> persisted -> reset
    plan["placements"][1]["x"] = 123
    assert c.put("/api/plan/970x90", json=plan).get_json()["edited"] is True
    assert os.path.exists(tmp_path / "layouts" / "Axis.json")
    assert c.get("/api/plan/970x90").get_json()["plan"]["placements"][1]["x"] == 123
    assert c.post("/api/plan/970x90/reset").get_json()["edited"] is False

    # a near-square format can be broken into per-element boxes on demand
    ex = c.post("/api/plan/300x250/explode").get_json()["plan"]
    assert ex["strategy"] == "explode"
    assert len(ex["placements"]) == len(man["elements"]) + 1

    with Image.open(io.BytesIO(c.get("/api/render/300x250.png").data)) as im:
        assert im.size == (300, 250)


def test_justify_fills_the_column():
    # Justified lines span the full column; the last line stays flush left.
    from PIL import Image as Im, ImageDraw
    strip = Im.new("RGBA", (600, 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    for i in range(6):
        d.rectangle([i * 100 + 10, 8, i * 100 + 70, 32], fill=(200, 0, 80, 255))
    left = textflow.reflow_to_width(strip, target_w=140, line_h=24, align="left")
    just = textflow.reflow_to_width(strip, target_w=140, line_h=24, align="justify")
    assert just.width == 140 >= left.width      # fills the column exactly
    assert just.height == left.height           # same line breaks, same height


def test_element_crop_is_fractional_and_exact(source):
    from adapt import tiles
    el = next(e for e in source.elements if not e.is_type)
    full = tiles.graphic_tile(el, 200, 100)
    half = tiles.graphic_tile(el, 200, 100, crop=[0.0, 0.0, 0.5, 1.0])
    assert full.size == half.size == (200, 100)   # the box is unchanged...
    assert np.array(full) .shape == np.array(half).shape
    assert not np.array_equal(np.array(full), np.array(half))   # ...content is not
    # A full-extent crop is a no-op.
    assert np.array_equal(np.array(tiles.graphic_tile(el, 60, 60, crop=[0, 0, 1, 1])),
                          np.array(tiles.graphic_tile(el, 60, 60)))


def test_opacity_fades_towards_what_is_behind(source):
    from adapt.pipeline import plan_for_format, render
    from adapt import saliency as sal
    imp = sal.importance_map(np.array(source.composite)[:, :, ::-1].copy(),
                             source.elements)
    fmt = FORMATS[0]
    plan = plan_for_format(source, fmt, imp)
    solid = np.array(render(plan, source), dtype=np.int16)
    for p in plan.placements:
        if p.kind == "element":
            p.opacity = 0.0
    faded = np.array(render(plan, source), dtype=np.int16)
    assert solid.shape == faded.shape
    assert np.abs(solid - faded).mean() > 1.0     # elements really did fade out


def test_custom_sizes_are_laid_out_and_reviewed(source):
    # A custom size goes through the same engine; review() reports when it fails.
    from adapt.formats import Format, validate
    from adapt.pipeline import plan_for_format, plan_raw, render, review
    from adapt import saliency as sal
    imp = sal.importance_map(np.array(source.composite)[:, :, ::-1].copy(),
                             source.elements)

    assert validate(10, 10) and validate(99999, 100)      # rejected, with a reason
    assert validate(1000, 300) is None

    ok = Format(1000, 300)
    assert render(plan_for_format(source, ok, imp), source).size == (1000, 300)
    assert review(plan_for_format(source, ok, imp), source) == []

    harsh = Format(120, 40)
    assert review(plan_for_format(source, harsh, imp), source), "should warn"

    # The manual fallback still holds every element, at the exact size.
    raw = plan_raw(source, harsh.width, harsh.height)
    assert len([p for p in raw.placements if p.kind == "element"]) == len(source.elements)
    assert render(raw, source).size == (120, 40)


def test_no_standard_format_trips_a_warning(source):
    # The thresholds must not cry wolf on the six sizes we ship.
    from adapt.pipeline import plan_for_format, review
    from adapt import saliency as sal
    imp = sal.importance_map(np.array(source.composite)[:, :, ::-1].copy(),
                             source.elements)
    for fmt in FORMATS:
        assert review(plan_for_format(source, fmt, imp), source) == [], fmt.name


def test_custom_sizes_persist_and_reach_the_cli(source, tmp_path):
    from adapt import store
    from adapt.formats import Format
    from adapt.pipeline import run
    store.save(INPUT, {}, custom=[Format(1000, 300)], out_dir=str(tmp_path))
    assert store.load_custom(INPUT, str(tmp_path)) == [Format(1000, 300)]
    paths = run(INPUT, str(tmp_path), debug=False, use_saved=True)
    assert any(p.endswith("1000x300.png") for p in paths)
    with Image.open(tmp_path / "1000x300.png") as im:
        assert im.size == (1000, 300)


class _Node:
    """Stands in for a psd_tools layer: composites a crop of a fixed image."""

    def __init__(self, img, honour_viewport=True):
        self.img = img
        self.honour_viewport = honour_viewport

    def composite(self, viewport=None):
        if viewport is None or not self.honour_viewport:
            return self.img
        return self.img.crop(viewport)


def _artwork(w, h):
    """Smooth gradients plus hard shapes — representative of real artwork.

    (Pure noise would be a pointless subject here: any sub-pixel difference in
    the resampling grid changes every pixel, so it measures nothing useful.)
    """
    y, x = np.mgrid[0:h, 0:w]
    a = np.stack([(x * 255 // max(1, w - 1)), (y * 255 // max(1, h - 1)),
                  ((x + y) * 255 // max(1, w + h - 2))], -1).astype(np.uint8)
    img = Image.fromarray(a, "RGB")
    d = ImageDraw.Draw(img)
    for i in range(6):
        d.rectangle([w * i // 7, h // 4, w * i // 7 + w // 12, 3 * h // 4],
                    fill=(240, 30, 90))
    d.ellipse([w // 3, h // 3, 2 * w // 3, 2 * h // 3], outline=(0, 0, 0), width=5)
    return img


def test_banded_compositing_matches_one_pass():
    # Bands exist to cap memory, so they must not change the picture. The overlap
    # margin is what makes the seams disappear.
    from adapt.psd_source import _composite_scaled
    src = _artwork(600, 400)
    node = _Node(src)
    box, k = (0, 0, 600, 400), 0.25
    one = _composite_scaled(node, box, k, "RGB", band=False)
    many = _composite_scaled(node, box, k, "RGB", band=True, max_band_px=80_000)
    assert one.size == many.size == (150, 100)

    # Each band rounds its own target height, so content inside it can land up to
    # half a pixel out — invisible on the photographic backdrop this is used for,
    # and zero on the real master, where the boundaries divide evenly. What must
    # never happen is tiling, which puts these numbers in the hundreds.
    d = np.abs(np.asarray(one, float) - np.asarray(many, float))
    assert d.mean() < 1.0, d.mean()
    assert d.max(2).mean(1).max() < 40, d.max(2).mean(1).max()


def test_banding_falls_back_when_the_viewport_is_ignored():
    # PSDImage.composite() returns the embedded preview and ignores the viewport.
    # Banding that would tile the whole image down the canvas, so it must detect
    # the mismatch and composite in one pass instead.
    from adapt.psd_source import _composite_scaled
    src = _artwork(600, 400)
    banded = _composite_scaled(_Node(src, honour_viewport=False), (0, 0, 600, 400),
                               0.25, "RGB", band=True, max_band_px=20_000)
    plain = _composite_scaled(_Node(src), (0, 0, 600, 400), 0.25, "RGB", band=False)
    assert banded.size == (150, 100)
    assert np.array_equal(np.asarray(banded), np.asarray(plain))


def test_working_size_downscale_keeps_boxes_and_pixels_aligned():
    from adapt.elements import Element
    from adapt.psd_source import Source, to_working_size
    canvas = _artwork(4000, 2000)
    tile = Image.new("RGBA", (400, 200), (10, 20, 30, 255))
    src = Source(4000, 2000, canvas, [Element(role="logo", name="l", image=tile,
                                              bbox=(100, 50, 500, 250))], canvas)
    to_working_size(src, max_dim=1000)
    assert (src.width, src.height) == (1000, 500)
    el = src.elements[0]
    assert el.image.size == (100, 50)
    # The box must still describe where those pixels are, at the new scale.
    assert el.bbox == (25, 12, 125, 62)
    assert (el.width, el.height) == el.image.size


def test_importance_map_cap_does_not_change_the_crop(source):
    # The map is downscaled for speed; the window it selects must not move.
    bgr = np.array(source.composite.convert("RGB"))[:, :, ::-1].copy()
    full = saliency.importance_map(bgr, source.elements, max_dim=99999)
    capped = saliency.importance_map(bgr, source.elements)
    assert capped.shape == full.shape
    for fmt in FORMATS:
        a = smartcrop.crop_box(full, fmt.width, fmt.height)
        b = smartcrop.crop_box(capped, fmt.width, fmt.height)
        assert all(abs(x - y) <= 2 for x, y in zip(a, b)), (fmt.name, a, b)


def test_detect_objects_flat_fallback():
    # A synthetic image with two bright blobs -> at least one contour box.
    img = np.zeros((400, 400, 3), np.uint8)
    img[50:150, 50:150] = 255
    img[220:330, 260:360] = 200
    boxes = saliency.detect_objects(img)
    assert len(boxes) >= 1
    for (l, t, r, b) in boxes:
        assert r > l and b > t
