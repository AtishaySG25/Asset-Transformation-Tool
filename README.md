# Smart Ads — Asset Transformation Tool

Transform a single layered master creative (PSD) into the six standard IAB ad
sizes, preserving the brand-critical content (logo, headline, CTA) and never
clipping content outside the target aspect ratio.

| Output | Size | Strategy |
|--------|------|----------|
| Medium rectangle | 300×250 | Saliency crop |
| Small square | 200×200 | Saliency crop |
| Skyscraper | 160×600 | Template relayout |
| Large leaderboard | 970×90 | Template relayout |
| Leaderboard | 728×90 | Template relayout |
| Full banner | 468×60 | Template relayout |

## Approach

The tool picks one of two strategies per output, based on how far the target
aspect ratio is from the (square) master:

1. **Saliency crop** — when the target is close to square (200×200, 300×250), a
   crop preserves the real design. The crop window is chosen by maximizing an
   *importance map* = OpenCV saliency **+** boosts on the classified logo /
   headline / CTA boxes, so the brand content is kept rather than just the most
   "interesting" pixels.

2. **Template relayout** — when the target is extreme (thin banners, tall
   skyscraper), cropping would destroy the layout, so the PSD's semantic layers
   are re-arranged into a format-specific template. Overflow — the thing that
   broke the previous attempt — is made **impossible by construction**: every
   element is `contain`-fit into a bounded slot, and slot sizes are allocated to
   sum to ≤ the available space (see `smart_ads/layout/relayout.py`).

### Semantic classification

Each PSD layer/group is classified into a role (logo, headline, subtext, CTA,
graph, product, disclaimer, background, decorative) using a hybrid of layer
**name** (whole-word keyword match), layer **kind**, and **geometry/position**.
Cohesive groups (a `cta` group = button + label + icon) are kept whole; "logo"
/ "footer" bands that bundle a logo with legal text are split so the logo can be
isolated.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt
```

> Note: `opencv-contrib-python` (not plain `opencv-python`) is required for
> `cv2.saliency`.

## Run

```bash
python -m smart_ads --input input/Axis.psd --output output
```

Options:

| Flag | Meaning |
|------|---------|
| `--formats 970x90 160x600` | Render only a subset |
| `--jpg` | Save JPG instead of PNG |
| `--debug` | Also write `output/debug/classification.png` (an explainability overlay of what was detected) |
| `-v` | Verbose logging |

## Tests

```bash
python -m pytest tests/
```

The suite locks in the two properties that previously failed: the layout
**never overflows the canvas** for any format, and short classification keywords
match only as whole words (`cta` must not fire inside `rectangle`).

## Project layout

```
smart_ads/
  cli.py / __main__.py   entry point
  config.py              the 6 target formats + strategy tuning
  loader.py              PSD -> list of semantic elements (JPEG stub for future)
  classifier.py          layer -> role (name / kind / geometry)
  saliency.py            saliency + importance map for cropping
  layout/
    strategy.py          crop vs relayout decision
    crop.py              saliency-aware smart crop (exact dims)
    relayout.py          fit-guaranteed template engine
  renderer.py            compositing + saving
  debug.py               classification overlay
  pipeline.py            end-to-end orchestration
```

## Roadmap / known limits

- Flat JPEG/PNG input is stubbed (`FlatImageLoader`) but relayout currently needs
  layer semantics; a flat input would fall back to crop-only.
- The relayout background is a softened/lightened version of the master
  background; brands with dark-themed creatives may want a different treatment.
- Re-laid-out text keeps its original colors, which assumes a light background.
