# adapt — CV-driven ad asset transformation

Transforms a single **square master ad** (layered PSD or flat JPEG/PNG) into
several **exact-sized secondary assets**, using computer vision to identify the
key visual elements and preserve them across very different aspect ratios —
**without clipping key content and without letterbox padding**.

Built against the Axis Mutual Fund master (`input/Axis.psd`, 1200×1200) and the
six IAB output sizes:

| Size | Type | Aspect | Strategy |
|------|------|--------|----------|
| `200x200` | square | 1.00 | content-aware crop |
| `300x250` | medium rectangle | 1.20 | content-aware crop |
| `468x60`  | full banner | 7.80 | element re-layout |
| `728x90`  | leaderboard | 8.09 | element re-layout |
| `970x90`  | super-leaderboard | 10.78 | element re-layout |
| `160x600` | wide skyscraper | 0.27 | element re-layout |

Nothing in the pipeline is keyed to this particular file: roles come from layer
names, and every layout decision is driven by element **geometry and position**,
so the tool generalises to other masters (see [Generalisation](#generalisation)).

## Why multiple strategies

Cropping a 1:1 master to 8:1 shows only ~12 % of the image — the logo, headline
and CTA do not survive. So the pipeline routes each format
(`adapt/pipeline.py::effective_strategy`) down one of three paths:

* **`crop`** — near-square targets (`200x200`, `300x250`). The whole composite is
  resized to fill the frame exactly, so every element is kept and there are no
  side gaps. Aspect change is small, so distortion is minor.
* **`reflow`** — extreme ratios (banners, skyscraper). A **content-preserving
  re-layout**: **every** element is kept (nothing dropped, nothing clipped), text
  is **re-wrapped** to the new width, and elements are re-stacked following the
  master's own top→bottom reading order.
* **`photo`** — sources with no discrete foreground elements (a plain photo PSD
  or a flat JPEG). There is nothing to re-arrange, so *every* format falls back
  to a **saliency-weighted content-aware crop**, regardless of aspect.

All three paths always emit the **exact** target dimensions — no clipping to a
smaller canvas, no padding out. This is asserted per-format in `pipeline.run`
and covered by the test suite.

### Text re-wrapping with CV (no fonts)

The headline "Your Growth Path Across Market Segments" is one wide line in the
master; in a 160-px-wide skyscraper it must wrap to several lines.
`adapt/textflow.py` does this **without any font**: it segments the rasterised
text into word images using **projection profiles** (horizontal projection →
text lines, vertical projection → words, with an adaptive gap threshold that
scales with text height), then re-flows the words into a target width.

The original glyph rendering (font, colour, weight) is preserved exactly, and a
scale cap guarantees no word is ever clipped — text only ever wraps or shrinks.
Badges, buttons and logos are type-backed too, so they are distinguished by
their high **ink-fill** ratio and kept as scaled units rather than shredded into
"words" — again a geometric rule, not a hardcoded name list.

### Staying sharp

Re-layout involves repeated resampling, which softens small text. Two measures
counteract it: the banner/skyscraper layouts are composed at **2× supersampling**
and downscaled once (`reflow(..., ss=2)`), and every output gets a final
**unsharp mask** (`pipeline._sharpen`).

## Layout plans, and the manual editor

The layout engine does not paint pixels. It emits a **`LayoutPlan`**
(`adapt/layout.py`): a base colour plus an ordered list of `Placement` boxes in
*output pixel* coordinates. `adapt/render.py::render_plan` is the only thing that
rasterises, and `adapt/tiles.py` is the only thing that turns an element into a
tile. So there is exactly one code path from a layout to a PNG:

```
psd_source.load ─▶ plan_for_format ─▶ LayoutPlan ─▶ render_plan ─▶ exact-size PNG
                                          │
                                     (JSON on disk)
                                          │
                                     web editor  ◀── drag / resize / restack
```

Because a plan is plain JSON, it round-trips: a hand-adjusted layout renders
through the same renderer as the algorithmic one, and therefore inherits the same
exact-dimension and no-clipping guarantees. This is asserted in the tests — a
plan sent through `to_json`/`from_json` renders **byte-identically**.

### Placement kinds

| kind | what it draws |
|------|----------------|
| `element` | one extracted element. `params.mode` is `reflow` (text re-wrapped to the box width) or `stretch` (scaled to the box); `params.crop` keeps a sub-rectangle, as fractions of the element's own image |
| `photo_band` | the background, filled into the box, optionally alpha-feathered |
| `color_bar` | a solid rectangle (footer bar, disclaimer strip) |
| `base_image` | a full-frame image from the composite (the `fit` and `photo` strategies) |

Every placement also carries an `opacity`, so an element can be faded to let
whatever sits behind it — usually the background imagery — show through.

Both background kinds treat their box as a **viewport onto the imagery**, framed
by three shared params: `fit` (`cover` / `contain` / `stretch`), `zoom` (a
multiplier on that fit) and `focus_x` / `focus_y` (which point of the image sits
at the box centre). Their defaults reproduce the original behaviour exactly, so
plans saved before they existed render unchanged.

### Wrapping must be reproducible

A re-wrapped text box is described by **(width, line height)** alone — its height
is emergent. For a saved layout to re-render as the layout you saved, wrapping a
block at its own measured width has to reproduce it exactly, so `textflow`
separates the two halves of the job: line breaks are always decided at 1× using
**integer** word extents, and only the rasterisation is supersampled (`ss`).
Deciding breaks at render scale instead lets sub-pixel rounding push a word onto
a new line and silently change a layout that was measured at 1×.

### The editor

`python run.py --serve` starts a local Flask app (`adapt/web/`) with a
dependency-free vanilla-JS front end — no CDN, works offline.

* **Gallery** (`/`) — pick a master asset (or drag one in) and see every format,
  grouped by shape: banners in one column, squares and skyscrapers in the other,
  with the rasterised master pinned to the right. Custom sizes file themselves
  into a group by aspect ratio, so the grouping needs no list to maintain.
  Previews scale to fit their column; the **1:1** toggle shows actual pixels.
* **Editor** (`/edit/970x90`) — the format at its exact pixel dimensions, every
  placement a draggable/resizable box. Layers panel for stacking (drag to
  reorder) and visibility; properties panel for numeric x/y/w/h, line height,
  alignment, bar colour, band feather/focus. Edge and centre **snapping**, arrow
  key nudging. Nothing may leave the frame — the editor clamps exactly as the
  renderer does.
* **Stacking is a direct command.** **Bring to front** / **Send to back** in the
  properties panel (and on each layer row, on hover), `[` / `]` to move one step,
  `Ctrl+Shift+[` / `]` to go all the way. Widening a background until it covers
  its neighbours is the common case — one click puts it back behind them rather
  than hunting for the right drop slot in the layers list.
* **Text behaves like text.** Dragging a text box's side re-wraps it through
  `textflow` on the server and returns the real block; dragging top/bottom
  changes the type size. Glyphs are never stretched. Alignment is left, centre,
  right or **justified** (justified lines fill the column; the last stays flush).
* **Opacity and backdrops.** Every placement has an opacity slider, and
  **Background behind** drops the master's own imagery in behind a chosen
  element as its own placement — which can then be moved, faded or feathered
  independently.
* **Framing the background.** A background box is a viewport, not a fixed crop:
  **Alt+drag inside it slides the imagery** and **Alt+wheel zooms** it, with
  `fit` / `zoom` / `focus` also on the properties panel and **Whole image** /
  **Fill box** shortcuts. This is what makes a wide composition usable in a
  narrow target — a plain cover crop of a 1200×1200 master into `160x600` can
  only ever show 27% of its width, and no amount of moving the *box* recovers
  the rest. Below `zoom` 1 the imagery no longer fills the box and the gap is
  left transparent, so the base colour (or anything behind) shows through.
* **Crop.** Any non-text element can be cropped interactively: the element is
  shown dimmed with a bright rectangle over the part being kept. The crop is
  stored as *fractions* of the element's image, so it keeps its meaning at any
  box size.
* **Undo/redo** (Ctrl+Z / Ctrl+Y, 120 deep) over everything — moves, resizes,
  crops, restacking, hiding, canvas resizes.
* **The master stays in view.** Both pages carry a right-hand panel showing the
  PSD rasterised (`/api/master.png`, scaled for the sidebar, click for full size)
  so the original composition is always next to the one you are rearranging. It
  collapses if you want the room back.
* **Switching assets is clean.** The element, tile and master endpoints are
  cached for an hour, and their URLs would otherwise be identical for every
  asset — so a second PSD opened in the same session was drawn with the first
  one's pictures. Each load mints a **source token** (`manifest.token`) that is
  stamped into all three, so the browser re-fetches exactly when the asset
  changes and keeps caching hard the rest of the time. Re-opening the same file
  mints a new token too, which is what makes a PSD edited on disk show up.
* **Per format.** Moving something in `970x90` has no effect on `160x600`.
* **Copy to…** — but repeating the *same* fix per format is neither. When the
  algorithm reads a master badly, correcting it once is a lot of work and
  correcting it again at every other banner size is the same work plus a good
  chance the three end up not matching. **Copy to…** rescales this layout onto
  whichever sizes you pick (same-shape ones pre-selected) and saves it as each
  one's own manual layout, free to diverge afterwards. Sizes that already carry
  manual edits are skipped unless you confirm, so it cannot quietly destroy work
  done elsewhere. **Resize** is the same operation onto a brand-new size and
  runs through the same code.

  Rescaling is server-side (`pipeline.rescale_plan`) for a reason: a re-wrapped
  text block's height is *emergent* from its width and line height, so scaling
  the recorded height arithmetically stores a box the renderer will not agree
  with — the words re-wrap differently in the narrower column. The copy asks the
  same `tiles.text_tile` the renderer uses, so it is truthful on arrival.
* **Reset to algorithm** per format, **Show render** to see the true server
  render beside the canvas, **Export PNG** (saves the current format), **Export
  all** (writes `output/`) and **Download all (.zip)** (every size in one
  archive). Downloads are plain attachments handed to the browser rather than
  blobs assembled in JS — a slow render then shows up as a slow download instead
  of a failed `fetch`.
* **Explode elements** — the near-square formats default to scaling the flat
  composite (which preserves PSD layer effects); this button breaks that into one
  box per element when you actually want to re-arrange them.

Edits autosave to `output/layouts/<master>.json` and are re-applied headlessly by
`python run.py --use-layout`, so the editor is not a dead end: what you arrange by
hand stays part of the reproducible build.

### Sizes beyond the six

**Add size** in the gallery registers any `w × h` (16–8000px per side) and runs
the *same* layout engine on it — custom sizes are not a second-class path. They
persist with the layouts and are rendered by `--use-layout` alongside the six.

Some targets are simply too harsh for an automatic layout, so
`pipeline.review()` inspects the resulting plan and reports what went wrong:
elements below 6px, text below a 4px line height, or boxes that overflow the
frame and get clamped on top of each other. The thresholds are calibrated so
that none of the six shipped sizes trips one — `468x60` legitimately runs a 4px
line height. When a size does warn, the gallery says so and offers **Edit
manually (raw layout)**: `pipeline.plan_raw()` drops every element into the
frame in reading order, scaled to fit, with no synthesized furniture — a
guaranteed-complete starting point to arrange by hand.

**Resize** in the editor rescales an entire layout to a new canvas: positions
follow each axis, aspect-locked graphics scale uniformly so they are never
distorted, and type size follows the smaller axis. The result is saved as a
custom size of its own, leaving the original untouched.

## Working at a sane resolution

Masters can be far bigger than anything they produce. The Women's Day sample is
6482×3646 — 24 megapixels — while the largest output is 1200px wide. Carrying
that resolution through every resize is what made the tool crawl on an 8 GB
laptop, so `psd_source` decides a **working scale** up front (`MAX_WORK_DIM`,
2400px, override with `--max-dim`) and composites straight into it. Masters at or
below that size are untouched, so the six standard outputs are byte-identical to
before.

A second cost is compositing itself: a `background` *group* stacking seven
full-canvas sub-layers allocates a float buffer per layer. That one is rendered
in **horizontal bands** (`_composite_scaled(..., band=True)`), which is both
lighter and faster. Banding is opt-in and applied only there — ordinary layers
can render differently under a partial viewport, since effects and smart-object
resampling are resolved against it, and `PSDImage.composite()` ignores the
viewport altogether (it returns the embedded preview, which is detected and
falls back to a single pass rather than tiling the image down the canvas).

Measured on that 24-megapixel master, per preview pass:

| | before | after |
|---|---|---|
| resident memory, working | 551 MB | **178 MB** |
| importance map | 13.1 s | **0.9 s** |
| render, per format | 2.0–4.5 s | **0.4–1.3 s** (4 ms cached) |
| load | 41.5 s | **31 s** |
| peak during load | 2.70 GB | **2.21 GB** |

The remaining peak is `psd_tools` compositing a single 15-megapixel smart-object
layer at the file's own resolution — outside this tool's control, transient, and
now called out in the log so it is not a mystery.

The editor also renders **one format at a time** (a lock plus a plan-hash render
cache) and the gallery requests previews sequentially. Eight parallel renders of
a master that size was what made previews fail to appear at all.

## Logging

Every stage prints what it is doing, how long it took and the process's resident
memory, so a slow load or a missing preview is attributable rather than guessed
at:

```
[   1.57s    157MB]   master is 6482x3646; compositing at working scale x0.370 -> 2400x1350
[   7.37s    214MB] composite 6482x3646 done in 5.80s
[  19.35s    230MB]   background layer 'Background'
[  34.75s    247MB]   element 'image' -> object 1524x1350 at (0, 0, 1524, 1350) (8MB)
[  38.52s    158MB] importance map done in 0.85s  2400x1350
[  38.94s    159MB]   plan 970x90 (reflow) done in 0.44s  5 placements
[  39.15s    160MB]   render 970x90 (ss=2) done in 0.21s
```

The web server logs every API call the same way (`GET /api/render/970x90.png ->
200 in 0.42s, 68 KB`), including cache hits and failures. Silence it with
`--quiet` or `ADAPT_QUIET=1`.

## The approach: Hybrid (layer-aware + OpenCV)

The PSD is cleanly layered with semantically-named groups, so we get element
**roles and bounding boxes for free** — far more reliable than guessing them from
a flattened render. OpenCV still does real CV work: saliency + edge/contour
analysis drive the crop scoring, and provide the **fallback element detector**
for flat (non-layered) inputs.

Extraction is done per-layer within each layer's own bounding box rather than by
re-compositing the full 1200×1200 canvas each time — a large speed win.

### The four required CV challenges

1. **Object identification** — `adapt/psd_source.py` extracts each layer group as
   a tight-cropped RGBA element and classifies it into a role (logo, cta, scheme,
   headline, subheadline, riskometer, rating, disclaimer). The bundled
   `footer-logo` group is split so the logo can travel independently of the
   disclaimer. For flat images, `adapt/saliency.py::detect_objects` uses Canny
   edges + contour bounding boxes.
2. **Saliency detection** — `adapt/saliency.py` builds a fused importance map:
   `cv2.saliency` (spectral-residual, with fine-grained / Laplacian fallbacks) +
   Canny edge density, boosted by known element boxes weighted by role priority.
3. **Smart cropping** — `adapt/smartcrop.py` slides a target-aspect window over
   the importance map (via cumulative sums) and keeps the highest-importance
   band. When trimming height it applies a top-favouring prior, so the headline
   and branding survive rather than being sliced off.
4. **Object re-positioning** — `adapt/reflow.py` re-arranges elements into a
   vertical stack (skyscraper) or a 2D column packing (banners), driven by each
   element's original position so the visual hierarchy is preserved.

#### Skyscraper layout (`layout_tall`)

Elements are stacked top→bottom in their original reading order, with a
**full-bleed background band** inserted at the document position where the
master's imagery actually sits (found via a saliency-weighted mean row). A global
size multiplier is solved for so the stack **fills** the canvas height instead of
leaving dead space, then shrunk to fit if it overflows.

#### Banner layout (`layout_wide`)

1. The base is the sampled background colour, with the background imagery drawn
   as a **soft-edged central band** (alpha-feathered left and right) so it blends
   in rather than reading as a pasted-in tile, while text sits on clean space.
2. Elements are packed into reading-order **columns**; a lower-priority text line
   directly following a higher-priority one tucks underneath it to form a
   **title block** (headline + subheadline).
3. Columns are spread across the full width and vertically centred, shrinking
   proportionally if they overflow.
4. A wide, bottom-sitting **logo lock-up** becomes a full-width **footer bar**
   tinted with the logo's own dominant colour, with the lock-up on its right.
5. A very wide, bottom-sitting fine-print line (the disclaimer) becomes a
   **full-width bottom strip**.

Both the footer bar and the disclaimer strip are detected by **shape and
position** (aspect ratio + vertical placement), never by layer name.

## Generalisation

The tool was validated against four different PSDs — the structured Axis master
plus three photographic files with no ad structure at all, which correctly route
to the `photo` path instead of producing a meaningless reflow. Design rules that
keep it generic:

* **Roles** come from layer-name keywords in one place (`elements.classify`),
  ordered so that e.g. `app-rating-logo` classifies as a *rating* badge, not a
  logo.
* **Layout** decisions use geometry — aspect ratio, centroid position, ink-fill,
  priority ordering — so no branch depends on a specific file's contents.
* **Sizes** are all expressed as fractions of the target canvas, so there are no
  magic pixel constants that only work at one output size.
* **Degradation** is explicit: a source with no elements takes the `photo` path,
  and a flat JPEG falls back to contour-based object detection.

## Usage

```bash
pip install -r requirements.txt

# default: input/Axis.psd -> output/
python run.py
# or
python -m adapt --input input/Axis.psd --output output --no-debug

# manual layout editor at http://127.0.0.1:8000
python run.py --serve
python run.py --serve --port 8080 --input input --output output

# re-render, applying any layouts hand-edited in the editor
python run.py --use-layout
```

Outputs land in `output/` as `<w>x<h>.png`. With debug on (default) you also get:

* `output/montage.png` — contact sheet of all six assets
* `output/debug/elements.png` — detected elements boxed and labelled by role
* `output/debug/importance.png` — the fused saliency/edge importance heatmap

To inspect a PSD's structure (useful when adding support for a new master):

```bash
python view_psd.py input/Axis.psd --layers
```

## Project layout

```
adapt/
  __main__.py     argparse CLI (python -m adapt)
  formats.py      target sizes + aspect-based strategy router
  elements.py     Element model, role classification, hierarchy priority
  psd_source.py   PSD layer extraction  (+ flat-image CV fallback)
  saliency.py     OpenCV saliency, edge/contour detection, importance map
  smartcrop.py    saliency-weighted content-aware crop (near-square)
  textflow.py     CV text word-segmentation + re-wrapping (no fonts)
  background.py   cover-fill to exact canvas (no padding)
  log.py          timed, memory-annotated progress log
  layout.py       LayoutPlan / Placement — the layout as data (JSON)
  tiles.py        placement -> pixels; the only rasteriser
  render.py       LayoutPlan -> exact-size image
  reflow.py       content-preserving element reflow -> a plan
  store.py        hand-edited layouts on disk (output/layouts/<master>.json)
  pipeline.py     orchestration, routing, saving, debug/montage
  web/            Flask layout editor (templates/ + static/, no JS deps)
run.py            convenience entry point (== python -m adapt)
view_psd.py       PSD inspector: composite + per-layer PNG export
tests/            pytest suite
```

`input/` and `output/` are gitignored — the master PSDs are large binaries kept
locally, and every output is reproducible from them.

## Tests

```bash
python -m pytest -q
```

Covers aspect-based routing, the photo-only fallback path, role extraction from
the PSD, importance-map shape and range, exact-dimension guarantees for every
format, CV text re-wrapping, and the flat-image contour fallback.

Plus the guarantees the editor rests on: a plan survives a JSON round-trip
**byte-identically**, every re-wrapped text box re-renders to exactly the box the
plan recorded, hand-edited layouts persist and reload, and the whole web API
(open → plan → tile → edit → save → reset → explode → render) round-trips through
Flask's test client.

And the editing features themselves: justified text fills its column, a
fractional crop changes an element's content without moving its box, opacity
really fades towards what is behind, custom sizes lay out and reach the CLI, and
`review()` warns on harsh sizes while staying silent on all six standard ones.

Tests that need the master PSD skip cleanly when it is absent, so a fresh clone
without `input/Axis.psd` still runs the routing and pure-CV tests.

## Limitations / notes

* The brief said "1080×1080 / 5 assets / 1200×300"; the actual master is
  **1200×1200** and the actual list is **six IAB sizes** — the tool targets the
  real master and the six listed sizes.
* At 60–90 px tall the fine print (riskometer, disclaimer) is kept but is
  necessarily small — the reflow prioritises keeping *all* content over per-line
  legibility, matching the brief's "no clipping" requirement.
* The `crop` path resizes the composite to the target aspect rather than
  cropping, so near-square outputs carry a small amount of anisotropic
  distortion. This was the deliberate trade for keeping every element and
  filling the frame with no side gaps.
* **PSD layer effects are lost by per-layer extraction.** `psd_tools` renders a
  layer's *own* pixels, so an effect drawn outside them — the CTA pill's stroke,
  for instance — appears in the flattened composite but not in the extracted
  element. This is why the near-square formats scale the flat composite by
  default and only **Explode elements** on request: exploding trades that
  fidelity for per-element control. It affects the reflow banners too, where the
  stroke is simply absent.
* The flat-JPEG path recovers salient objects but cannot recover semantic roles
  or re-wrap text; results there are best-effort versus the layer-aware PSD path.
* Role classification is keyword-based, so a master using very different layer
  naming needs new keywords in `elements.classify` — the intended extension
  point.
