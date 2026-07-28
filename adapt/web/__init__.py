"""Local web editor for manual layout adjustment.

The backend is deliberately thin: it loads a master asset through the existing
:mod:`adapt` extraction path, hands the browser the *algorithmic*
:class:`~adapt.layout.LayoutPlan` for each format, and rasterises whatever plan
comes back. All of the layout knowledge stays in the ``adapt`` package — the UI
only moves boxes around.

Routes
------
``GET  /``                       gallery of all six formats
``GET  /edit/<fmt>``             editor for one format
``GET  /api/sources``            master assets found in the input directory
``POST /api/open``               load a master asset (cached per process)
``GET  /api/manifest``           current asset: elements, formats, edited state
``GET  /api/element/<i>.png``    one extracted element, as extracted
``GET  /api/tile.png``           stateless tile preview (drives live editing)
``GET  /api/plan/<fmt>``         algorithmic (or saved) plan
``PUT  /api/plan/<fmt>``         persist a hand-edited plan
``POST /api/plan/<fmt>/reset``   drop the manual layout, back to the algorithm
``POST /api/plan/<fmt>/explode`` break a flat 'fit' plan into per-element boxes
``GET  /api/render/<fmt>.png``   render the stored plan
``POST /api/render/<fmt>.png``   render a posted plan (live preview / export)
``POST /api/export``             write PNGs for every format to the output dir
"""
from __future__ import annotations

import io
import json
import os
import time

import numpy as np
from flask import (Flask, abort, jsonify, render_template, request,
                   send_file, url_for)

from .. import saliency, store
from ..formats import FORMATS, Format
from ..layout import LayoutPlan, Placement
from ..pipeline import (_bgr, effective_strategy, plan_explode, plan_for_format,
                        render as render_image)
from ..psd_source import load
from ..render import placement_tile

ALLOWED = (".psd", ".png", ".jpg", ".jpeg")


class Session:
    """One loaded master asset plus the working plan for each format."""

    def __init__(self, path: str, out_dir: str):
        self.path = path
        self.out_dir = out_dir
        self.source = load(path)
        self.importance = saliency.importance_map(_bgr(self.source.composite),
                                                  self.source.elements)
        self.saved = store.load(path, out_dir)          # hand-edited, from disk
        self.auto: dict[str, LayoutPlan] = {}           # algorithmic, memoised
        self.rev = int(time.time())                     # cache-buster for renders

    # -- plans ------------------------------------------------------------
    def fmt(self, name: str) -> Format:
        for f in FORMATS:
            if f.name == name:
                return f
        abort(404, f"unknown format {name}")

    def auto_plan(self, name: str) -> LayoutPlan:
        if name not in self.auto:
            self.auto[name] = plan_for_format(self.source, self.fmt(name),
                                              self.importance)
        return self.auto[name]

    def plan(self, name: str) -> LayoutPlan:
        return self.saved.get(name) or self.auto_plan(name)

    def set_plan(self, name: str, plan: LayoutPlan):
        self.saved[name] = plan
        self.rev += 1
        store.save(self.path, self.saved, self.out_dir)

    def reset(self, name: str):
        self.saved.pop(name, None)
        self.rev += 1
        store.save(self.path, self.saved, self.out_dir)

    # -- description for the browser --------------------------------------
    def manifest(self) -> dict:
        src = self.source
        return {
            "path": self.path.replace("\\", "/"),
            "name": os.path.basename(self.path),
            "width": src.width, "height": src.height,
            "elements": [
                {"index": i, "name": el.name, "role": el.role,
                 "is_type": bool(el.is_type), "bbox": list(el.bbox),
                 "w": el.width, "h": el.height,
                 "url": url_for("element_png", index=i)}
                for i, el in enumerate(src.elements)],
            "formats": [
                {"name": f.name, "width": f.width, "height": f.height,
                 "strategy": effective_strategy(src, f),
                 "edited": f.name in self.saved}
                for f in FORMATS],
            "rev": self.rev,
        }


def create_app(input_dir: str = "input", out_dir: str = "output") -> Flask:
    app = Flask(__name__)
    app.config.update(INPUT_DIR=input_dir, OUT_DIR=out_dir, MAX_CONTENT_LENGTH=512 << 20)
    state: dict[str, Session] = {}

    # ------------------------------------------------------------ helpers --
    def session() -> Session:
        s = state.get("current")
        if s is None:
            abort(409, "no master asset loaded")
        return s

    def png(img, download_name: str | None = None, max_age: int = 0):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = send_file(buf, mimetype="image/png",
                         as_attachment=bool(download_name),
                         download_name=download_name)
        resp.headers["Cache-Control"] = f"public, max-age={max_age}" if max_age \
            else "no-store"
        return resp

    def list_sources() -> list[str]:
        d = app.config["INPUT_DIR"]
        if not os.path.isdir(d):
            return []
        return sorted(os.path.join(d, f).replace("\\", "/") for f in os.listdir(d)
                      if f.lower().endswith(ALLOWED))

    def open_source(path: str) -> Session:
        if not os.path.exists(path):
            abort(404, f"no such file: {path}")
        s = Session(path, app.config["OUT_DIR"])
        state["current"] = s
        return s

    # ------------------------------------------------------------- pages --
    @app.get("/")
    def index():
        return render_template("gallery.html")

    @app.get("/edit/<fmt>")
    def edit(fmt):
        return render_template("editor.html", fmt=fmt)

    # ------------------------------------------------------- source & meta --
    @app.get("/api/sources")
    def api_sources():
        cur = state.get("current")
        return jsonify({"sources": list_sources(),
                        "current": cur.path.replace("\\", "/") if cur else None})

    @app.post("/api/open")
    def api_open():
        path = (request.get_json(silent=True) or {}).get("path", "")
        return jsonify(open_source(path).manifest())

    @app.post("/api/upload")
    def api_upload():
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(ALLOWED):
            abort(400, "expected a .psd / .png / .jpg upload")
        os.makedirs(app.config["INPUT_DIR"], exist_ok=True)
        dest = os.path.join(app.config["INPUT_DIR"], os.path.basename(f.filename))
        f.save(dest)
        return jsonify(open_source(dest).manifest())

    @app.get("/api/manifest")
    def api_manifest():
        return jsonify(session().manifest())

    @app.get("/api/element/<int:index>.png")
    def element_png(index):
        els = session().source.elements
        if not 0 <= index < len(els):
            abort(404)
        return png(els[index].image, max_age=3600)

    # -------------------------------------------------------------- tiles --
    @app.get("/api/tile.png")
    def api_tile():
        """Rasterise one placement described entirely by the query string.

        Stateless on purpose: the browser can preview a drag/resize before the
        plan is saved, and the URL doubles as the cache key.
        """
        s = session()
        try:
            params = json.loads(request.args.get("params", "{}"))
        except ValueError:
            abort(400, "bad params")
        el = request.args.get("element")
        p = Placement(id="preview", kind=request.args.get("kind", "element"),
                      w=float(request.args.get("w", 1)),
                      h=float(request.args.get("h", 1)),
                      element=int(el) if el not in (None, "", "null") else None,
                      name=request.args.get("name", ""), params=params)
        tile = placement_tile(p, s.source, int(request.args.get("ss", 2)))
        if tile is None:
            abort(404)
        return png(tile, max_age=3600)

    # -------------------------------------------------------------- plans --
    def plan_payload(name: str, plan: LayoutPlan, s: Session) -> dict:
        return {"format": name, "plan": plan.to_json(),
                "edited": name in s.saved, "rev": s.rev}

    @app.get("/api/plan/<fmt>")
    def api_plan(fmt):
        s = session()
        return jsonify(plan_payload(fmt, s.plan(fmt), s))

    @app.put("/api/plan/<fmt>")
    def api_plan_save(fmt):
        s = session()
        s.fmt(fmt)                                   # validates the format name
        s.set_plan(fmt, LayoutPlan.from_json(request.get_json()))
        return jsonify(plan_payload(fmt, s.plan(fmt), s))

    @app.post("/api/plan/<fmt>/reset")
    def api_plan_reset(fmt):
        s = session()
        s.reset(fmt)
        return jsonify(plan_payload(fmt, s.auto_plan(fmt), s))

    @app.post("/api/plan/<fmt>/explode")
    def api_plan_explode(fmt):
        """Turn the flat 'fit' plan into one box per element, so a near-square
        format becomes editable. Not saved until the client PUTs it back."""
        s = session()
        f = s.fmt(fmt)
        plan = plan_explode(s.source, f.width, f.height)
        return jsonify({"format": fmt, "plan": plan.to_json(),
                        "edited": fmt in s.saved, "rev": s.rev})

    # ------------------------------------------------------------ renders --
    def _render(fmt: str, plan: LayoutPlan):
        s = session()
        f = s.fmt(fmt)
        img = render_image(plan, s.source)
        assert img.size == (f.width, f.height), f"size mismatch for {fmt}"
        return img

    @app.get("/api/render/<fmt>.png")
    def api_render(fmt):
        s = session()
        return png(_render(fmt, s.plan(fmt)))

    @app.post("/api/render/<fmt>.png")
    def api_render_posted(fmt):
        plan = LayoutPlan.from_json(request.get_json())
        download = request.args.get("download")
        return png(_render(fmt, plan), download_name=f"{fmt}.png" if download else None)

    @app.post("/api/export")
    def api_export():
        s = session()
        out = app.config["OUT_DIR"]
        os.makedirs(out, exist_ok=True)
        written = []
        for f in FORMATS:
            img = _render(f.name, s.plan(f.name))
            path = os.path.join(out, f"{f.name}.png")
            img.save(path)
            written.append(path.replace("\\", "/"))
        return jsonify({"written": written,
                        "layouts": store.path_for(s.path, out).replace("\\", "/")})

    return app


def serve(input_dir: str = "input", out_dir: str = "output",
          host: str = "127.0.0.1", port: int = 8000, debug: bool = False):
    app = create_app(input_dir, out_dir)
    print(f"adapt editor: http://{host}:{port}/")
    app.run(host=host, port=port, debug=debug, threaded=True)
