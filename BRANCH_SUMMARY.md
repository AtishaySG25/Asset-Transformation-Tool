# feature/layout-editor — summary

Branched from `main` (which had the CV-driven auto-transform pipeline: PSD ->
6 exact-sized secondary assets, no manual editing). This branch adds a local
web tool for reviewing and hand-correcting those layouts, plus a resolution/
memory rework needed to make it usable on large PSDs. Nothing here has been
merged to `main`.

5 commits, in order:

1. `db53df5` — Add a manual layout editor, built on layout plans
2. `dc0b83b` — Editor: opacity, crop, undo, custom sizes, justify, canvas resize
3. `6c82956` — Editor: keep the rasterised master alongside the canvas
4. `f432b4f` — Work at a sane resolution, and say what you are doing
5. `bff0c2c` — Frame the background by hand; make downloads reliable

## 1. Layout plans (the prerequisite refactor)

The original pipeline painted layouts straight onto a PIL canvas — no
intermediate representation, so there was nothing to edit. Split into:

- `adapt/layout.py` — `LayoutPlan` / `Placement`: the layout as plain JSON
  (base colour + an ordered list of boxes), in output-pixel coordinates.
- `adapt/tiles.py` — the only place a placement becomes pixels.
- `adapt/render.py` — `LayoutPlan` -> exact-size image.
- `adapt/reflow.py` — now *builds* a plan instead of pasting into a canvas.
- `adapt/store.py` — hand-edited plans persisted to
  `output/layouts/<master>.json`.

Both the CLI and the editor render through the same `render_plan()`, so a
manually-adjusted layout gets the pipeline's existing exact-dimension /
no-clipping guarantees for free, and a plan round-trips through JSON
byte-identically (asserted in tests).

Two fidelity fixes made along the way:
- `textflow.reflow_to_width` now decides line breaks once, at 1x, using
  integer word extents, and only supersamples the *rasterisation*. Deciding
  breaks at render scale let sub-pixel rounding push a word onto a new line,
  which meant a saved box width didn't reliably re-render as the layout it
  recorded.
- The near-square formats (`200x200`, `300x250`) default to scaling the
  flat composite rather than exploding into per-element boxes, because
  per-layer extraction drops PSD layer effects (e.g. a button's stroke,
  which lives outside the layer's own pixels). Exploding into elements is
  an explicit editor action instead.

`200x200` / `300x250` outputs are byte-identical to pre-branch `main` on the
sample PSD; the banner/skyscraper outputs shifted slightly because layout
math now runs at output resolution instead of 2x (a knife-edge line-wrap
decision can flip).

## 2. The web editor (`adapt/web/`)

Flask app, dependency-free vanilla-JS front end (no CDN, works offline).

- **Gallery** (`/`) — pick or upload a master PSD, see every target format
  grouped by shape (banners vs. squares/skyscrapers — grouped by aspect
  ratio, so custom sizes file themselves), master preview pinned to the
  right, **Export all to output/**.
- **Editor** (`/edit/<fmt>`) — canvas at exact target pixels. Every
  placement is a draggable/resizable box with edge/centre snapping, a
  layers panel (drag to restack, eye icon to hide), and a properties panel
  (x/y/w/h, line height, alignment, bar colour, band feather/focus).
  **Reset to algorithm**, **Show render** (server render next to the
  canvas), **Export PNG**.
- Edits autosave to `output/layouts/<master>.json` and are re-applied
  headlessly by `python run.py --use-layout`.

## 3. Feature set added on top (commit `dc0b83b`)

Six items requested directly, all expressed on the plan model so each
exports through the same renderer as everything else:

1. **Opacity + backdrop** — every placement has an opacity slider;
   **Background behind** drops the master's own imagery in behind a chosen
   element as its own placement (movable, fadeable, featherable
   independently).
2. **Crop** — any non-text element can be cropped interactively (dim
   overlay, bright kept-region rectangle with handles). Stored as
   *fractions* of the element's own image, so it keeps meaning at any box
   size. Disabled for re-wrapped text, which has no stable crop under
   re-flow.
3. **Undo/redo** — 120-deep snapshot stack, Ctrl+Z / Ctrl+Y, over every
   mutation (move, resize, crop, restack, hide, canvas resize).
4. **Custom sizes** — "Add size" registers any 16-8000px target and runs
   it through the *same* layout engine, not a separate path. Persist with
   the layouts and are rendered by `--use-layout` alongside the six
   standard sizes.
5. **Text alignment** — left / centre / right / **justify** added.
   Justified lines fill the column exactly (verified at any supersample
   factor); the last line stays flush left.
6. **Canvas resize** — rescales an entire layout to a new size: positions
   follow each axis, aspect-locked graphics scale uniformly (never
   distorted), type size follows the smaller axis. Saves as a new custom
   size, leaving the original untouched.

Plus `pipeline.review()`: inspects a *built* plan (not a guess from
dimensions) and reports elements under 6px, text under a 4px line height,
or boxes that overflow and get clamped on top of each other. Thresholds
are calibrated so none of the six shipped sizes trips a warning (`468x60`
legitimately runs a 4px line height). A warned size offers `plan_raw()` —
every layer dropped into the frame in reading order, scaled to fit, no
synthesized furniture — as a guaranteed-complete manual starting point.

## 4. Master reference panel (commit `6c82956`)

`/api/master.png` serves the PSD rasterised (full size or `?w=` scaled).
Both the gallery and the editor show it in a collapsible right-hand panel
(state remembered via `localStorage`) so the original composition stays
visible while rearranging its pieces.

## 5. Performance and memory (commit `f432b4f`)

Triggered by testing against a 6482x3646 (24-megapixel, 107MB) PSD, which
made the tool unusably slow and caused gallery previews to fail to load.

- **Working-resolution compositing** — `psd_source` now decides a working
  scale up front (`MAX_WORK_DIM = 2400px`, override with `--max-dim`) and
  composites *straight into it*, rather than materialising the full-res
  image and downscaling after. Masters at or below that size are
  untouched, so `Axis.psd`'s six outputs stay byte-identical.
- **Banded compositing** — a `background` *group* stacking several
  full-canvas sub-layers was allocating ~2.7GB. Rendering it in horizontal
  bands (with an overlap margin to avoid resample seams) fixed that.
  Opt-in and applied only to the background group: ordinary layers can
  render *differently* under a partial viewport (effects/smart-object
  resampling resolve against it), and `PSDImage.composite()` was found to
  ignore the viewport entirely and return the embedded preview — which
  tiled the image down the canvas until a mismatch guard caught it and
  fell back to a single pass.
- **Capped importance map** — computed on a copy no larger than 1400px;
  the crop windows it selects are unchanged (asserted in tests).
- **Serialised + cached rendering** — the web session renders one format
  at a time behind a lock, with a plan-hash render cache; the gallery
  requests previews sequentially rather than firing 6-8 in parallel.

Measured on the 24-megapixel master: resident memory 551 -> 178MB,
importance map 13.1 -> 0.9s, per-format render 2.0-4.5s -> 0.4-1.3s
(4ms cached), load 41.5 -> 31s, peak during load 2.70 -> 2.21GB. The
remaining peak is `psd_tools` compositing one 15-megapixel smart-object
layer at native resolution — outside this tool's control, transient, and
now called out in the log.

- **`adapt/log.py`** — every stage prints its duration and the process's
  resident memory (Windows `GetProcessMemoryInfo` via ctypes). The web
  server logs every API call the same way, including cache hits and
  failures. Silence with `--quiet` or `ADAPT_QUIET=1`.

## 6. Framing the background by hand

The background was placed by a *forced* cover crop: scale until it fills the box,
centre on a focus point, clamp that focus so no gap can open. In a 160x600
skyscraper that means a 1200x1200 master can only ever show 27% of its width —
one bridge pillar out of three — and because the constraint is the scale rather
than the position, moving or resizing the *box* cannot recover the rest.

`fill_background` now treats the box as a **viewport**: `zoom` multiplies the
cover scale and `focus_x` / `focus_y` place a point of the image at the box
centre, clamped to `[min(0, n-t), max(0, n-t)]` — which is the old no-gap rule
when the imagery covers, and free positioning when it does not. `tiles.
background_tile` adds `fit` (`cover` / `contain` / `stretch`) on top, and both
`photo_band` *and* `base_image` now go through it, so the near-square formats are
adjustable too.

Defaults reproduce the old behaviour exactly (`cover`/`stretch` at `zoom=1`), and
that is asserted rather than assumed: all six outputs are byte-identical before
and after, for both the algorithmic plans and the saved hand-edited ones.

In the editor a background box is framed by direct manipulation — **Alt+drag
slides the imagery, Alt+wheel zooms it** — with `fit` / `zoom` / `focus` on the
properties panel and **Whole image** / **Fill box** shortcuts. Tile re-renders
during a gesture are rate-limited to ~11/s behind a self-expiring deadline (not a
counter, so a lost pointerup cannot strand the preview on a stale tile), with the
true tile fetched when the gesture settles.

## 7. Downloads that do not fail

Reported as "Failed to fetch" when downloading, and previews that never arrive.
Both were the same shape of problem: the page built the file itself with
`fetch` + `Blob`, so a render that outran the browser's patience lost the whole
download and reported a bare `TypeError`.

- **Export PNG** now saves the plan, then hands `GET /api/render/<fmt>.png?download=1`
  to the browser as a normal attachment — native progress, retry and resume.
- **Download all (.zip)** (`GET /api/download.zip`) is new: every size in one
  archive, again as a plain attachment. Previously the only "export all" wrote
  to `output/` on the server, which is no help for getting files off the machine.
- Gallery previews now say *why* they failed. An `<img>` reports only that it
  failed, so on error the page asks the server directly: a 500 shows its message,
  a dropped connection says so, and a first attempt that merely timed out is
  retried once and used.

The root cause on this machine was **memory pressure, not a rendering bug**: with
7.5 GB of RAM and ~750 MB free, the long-running server's 830 MB working set had
been paged almost entirely to disk (resident 6 MB), so the first request after an
idle period had to fault it all back before it could answer. Every format renders
correctly — verified through the CLI, the Flask test client, real HTTP, and a
real browser. `--max-dim` remains the lever if a master is too big for comfort.

## New CLI flags

```
python run.py --serve [--host H] [--port P]   # start the web editor
python run.py --use-layout                    # apply hand-edited layouts
python run.py --quiet                         # silence the progress log
python run.py --max-dim N                     # working-resolution cap (default 2400)
```

## Files added

```
adapt/layout.py        adapt/render.py         adapt/tiles.py
adapt/store.py          adapt/log.py
adapt/web/__init__.py
adapt/web/static/{app.css,common.js,editor.js,gallery.js}
adapt/web/templates/{editor.html,gallery.html}
```

## Testing

32 pytest cases (up from 8 on `main`), covering: everything on `main`
(routing, role extraction, importance map, exact dimensions, text reflow,
flat-image fallback) plus — plan JSON round-trips render byte-identically;
re-wrapped text reproduces its own recorded wrap exactly; manual edits
survive a save/reload cycle; the full web API round-trips through Flask's
test client (open -> plan -> tile -> edit -> save -> reset -> explode ->
render); justified text fills its column exactly; fractional element crop
changes content without moving the box; opacity measurably fades toward
what's behind; custom sizes lay out and reach the CLI; `review()` is silent
on all six standard sizes; banded compositing matches single-pass output;
the viewport-ignored fallback is exercised directly; working-size downscale
keeps element boxes and pixels aligned; the importance-map cap doesn't move
the chosen crop window; `zoom=1` is bit-for-bit the old cover crop at every
size and focus; zooming out really does reveal more than a cover crop can;
focus pans content without moving the frame; focus cannot open a gap while the
imagery covers; `contain` fits the whole image; framing survives a plan
round-trip to the renderer; `base_image` still defaults to the old stretch;
and both downloads arrive as attachments carrying the *edited* layout.

Also manually verified end-to-end in a real Chromium browser (Playwright,
throwaway venv, not the project's own): drag, handle-resize with genuine
text re-wrap, z-order, visibility, autosave, reset, explode, opacity,
backdrop insertion, crop drag/apply, undo/redo, justify, custom sizes, the
warning card and raw fallback, canvas resize, and the master panel — on
both `Axis.psd` (1200x1200) and the 24-megapixel Women's Day PSD.

## Known limitations / things flagged to the user

- PSD layer *effects* (e.g. a button's stroke) live outside the layer's
  own pixels and are lost by per-layer extraction — this is why the
  near-square formats default to the flat composite rather than exploding
  into elements automatically.
- Justify only adjusts word spacing, not letter spacing — a line with two
  long words can get one large gap.
- Crop is unavailable for re-wrapped text elements (no stable meaning
  under re-flow).
- Banded compositing (memory fix) is applied only to the background group;
  it was measured to change output on other layer types and was not
  extended there.
- Export is PNG only — no JPEG option currently exists in the tool, despite
  earlier conversation referring to JPG/PNG export.
- Below `zoom` 1 a background no longer fills its box; the shortfall is left
  transparent so the base colour shows through, rather than being edge-extended
  or mirrored.
- The machine this was tested on has 7.5 GB of RAM, and a long-lived server plus
  a large master will page out. That is what made previews and downloads fail;
  the tool now reports it clearly instead of failing silently, but it cannot
  create memory. `--max-dim` lowers the working resolution if needed.

## Branch state

Working tree clean, 5 commits ahead of `main`, not merged.
