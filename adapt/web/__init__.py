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
``GET  /api/master.png``         the master asset rasterised (``?w=`` to scale)
``GET  /api/tile.png``           stateless tile preview (drives live editing)
``POST /api/formats``            add a custom target size
``DELETE /api/formats/<fmt>``    remove a custom target size
``GET  /api/review``             per-format warnings (what the algorithm couldn't do)
``GET  /api/plan/<fmt>``         algorithmic (or saved) plan
``PUT  /api/plan/<fmt>``         persist a hand-edited plan
``POST /api/plan/<fmt>/reset``   drop the manual layout, back to the algorithm
``POST /api/plan/<fmt>/explode`` break a flat 'fit' plan into per-element boxes
``POST /api/plan/<fmt>/copy-to`` reuse this layout at other sizes, rescaled
``POST /api/plan/<fmt>/raw``     every layer in reading order (manual fallback)
``GET  /api/render/<fmt>.png``   render the stored plan (``?download=1`` to save)
``POST /api/render/<fmt>.png``   render a posted plan (live preview / export)
``POST /api/export``             write PNGs for every format to the output dir
``GET  /api/download.zip``       every format as one downloadable archive
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import zipfile

import numpy as np
from PIL import Image
from flask import (Flask, abort, jsonify, render_template, request,
                   send_file, url_for)

from .. import formats as formats_mod
from .. import log, saliency, store
from ..formats import FORMATS, Format
from ..layout import LayoutPlan, Placement, rebind
from ..pipeline import (_bgr, effective_strategy, plan_explode, plan_for_format,
                        plan_raw, render as render_image, rescale_plan, review)
from ..psd_source import load
from ..render import placement_tile

ALLOWED = (".psd", ".png", ".jpg", ".jpeg")


class Session:
    """One loaded master asset plus the working plan for each format."""

    def __init__(self, path: str, out_dir: str):
        self.path = path
        self.out_dir = out_dir
        self.source = load(path)
        with log.step("importance map (saliency + edges + element boxes)") as s:
            self.importance = saliency.importance_map(_bgr(self.source.composite),
                                                      self.source.elements)
            s["note"] = f"{self.importance.shape[1]}x{self.importance.shape[0]}"
        self.saved = store.load(path, out_dir)          # hand-edited, from disk
        for name, plan in self.saved.items():
            self._rebind(name, plan)
        self.custom = store.load_custom(path, out_dir)  # user-added target sizes
        self.auto: dict[str, LayoutPlan] = {}           # algorithmic, memoised
        self.rev = int(time.time())                     # cache-buster for renders
        # Identity of *this* loaded master. The element/tile/master endpoints are
        # cached for an hour, and their URLs would otherwise be identical for
        # every asset — so opening a second PSD in the same session would redraw
        # it with the first one's images. Stamped into those URLs by the client.
        # Re-opening the same file mints a new token, which is what makes an
        # edited-on-disk PSD show up rather than serving the stale copy.
        self.token = f"{int(time.time() * 1000):x}"
        # The gallery asks for every format at once. Building and rendering them
        # one at a time keeps peak memory to a single canvas instead of eight,
        # which matters far more than the wall-clock difference.
        self.lock = threading.Lock()
        self._renders: dict[str, bytes] = {}
        if self.saved:
            log.log(f"manual layouts on disk: {', '.join(sorted(self.saved))}", 1)
        if self.custom:
            log.log(f"custom sizes: {', '.join(f.name for f in self.custom)}", 1)

    # -- render cache -----------------------------------------------------
    def cached_render(self, name: str, plan: LayoutPlan, make):
        """Renders are pure functions of (format, plan), so cache them by hash —
        revisiting the gallery should not re-render anything."""
        key = f"{name}:{hashlib.sha1(json.dumps(plan.to_json(), sort_keys=True).encode()).hexdigest()}"
        hit = self._renders.get(key)
        if hit is not None:
            log.log(f"render {name}: cache hit ({len(hit) // 1024} KB)", 1)
            return hit
        data = make()
        if len(self._renders) > 24:                  # keep the cache bounded
            self._renders.pop(next(iter(self._renders)))
        self._renders[key] = data
        return data

    # -- formats ----------------------------------------------------------
    def all_formats(self) -> list[Format]:
        return list(FORMATS) + self.custom

    def fmt(self, name: str) -> Format:
        for f in self.all_formats():
            if f.name == name:
                return f
        abort(404, f"unknown format {name}")

    def add_format(self, w: int, h: int) -> Format:
        why = formats_mod.validate(w, h)
        if why:
            abort(400, why)
        f = Format(w, h)
        if f.name not in {x.name for x in self.all_formats()}:
            self.custom.append(f)
            self._persist()
        return f

    def drop_format(self, name: str):
        self.custom = [f for f in self.custom if f.name != name]
        self.saved.pop(name, None)
        self.auto.pop(name, None)
        self._persist()

    # -- plans ------------------------------------------------------------
    def auto_plan(self, name: str) -> LayoutPlan:
        if name not in self.auto:
            with self.lock:
                if name not in self.auto:            # another thread may have won
                    self.auto[name] = plan_for_format(self.source, self.fmt(name),
                                                      self.importance)
        return self.auto[name]

    def plan(self, name: str) -> LayoutPlan:
        return self.saved.get(name) or self.auto_plan(name)

    def _rebind(self, name: str, plan: LayoutPlan) -> LayoutPlan:
        """Reconcile a plan's element references with the master now loaded, so
        every format names the same layer the same way (see
        :func:`adapt.layout.rebind`)."""
        lost = rebind(plan, self.source)
        if lost:
            log.log(f"{name}: {len(lost)} placement(s) refer to elements that are "
                    f"no longer in the master and will not draw: "
                    f"{', '.join(sorted({p.uid or p.name or p.id for p in lost}))}", 1)
        return plan

    def set_plan(self, name: str, plan: LayoutPlan):
        self.saved[name] = self._rebind(name, plan)
        self.rev += 1
        self._persist()
        log.log(f"saved manual layout for {name} ({len(plan.placements)} placements)")

    def reset(self, name: str):
        self.saved.pop(name, None)
        self.rev += 1
        self._persist()
        log.log(f"reset {name} to the algorithmic layout")

    def _persist(self):
        store.save(self.path, self.saved, self.custom, self.out_dir)

    # -- description for the browser --------------------------------------
    def manifest(self) -> dict:
        src = self.source
        return {
            "path": self.path.replace("\\", "/"),
            "name": os.path.basename(self.path),
            "width": src.width, "height": src.height,
            "elements": [
                {"index": i, "uid": el.uid, "name": el.name, "role": el.role,
                 "is_type": bool(el.is_type), "bbox": list(el.bbox),
                 "w": el.width, "h": el.height,
                 "url": url_for("element_png", index=i, v=self.token)}
                for i, el in enumerate(src.elements)],
            "formats": [
                {"name": f.name, "width": f.width, "height": f.height,
                 "strategy": effective_strategy(src, f),
                 "edited": f.name in self.saved,
                 "custom": f.name not in {x.name for x in FORMATS}}
                for f in self.all_formats()],
            "rev": self.rev,
            "token": self.token,
        }


def create_app(input_dir: str = "input", out_dir: str = "output") -> Flask:
    app = Flask(__name__)
    app.config.update(INPUT_DIR=input_dir, OUT_DIR=out_dir, MAX_CONTENT_LENGTH=512 << 20)
    state: dict[str, Session] = {}

    # ------------------------------------------------------------ logging --
    # One line in, one line out, with the duration — so a slow or failed preview
    # is attributable to a specific request rather than guessed at.
    @app.before_request
    def _started():
        request.environ["adapt.t0"] = time.time()

    @app.after_request
    def _finished(resp):
        if request.path.startswith("/api/"):
            dt = time.time() - request.environ.get("adapt.t0", time.time())
            size = resp.calculate_content_length() or 0
            log.log(f"{request.method} {request.full_path.rstrip('?')} "
                    f"-> {resp.status_code} in {dt:.2f}s, {size // 1024} KB")
        return resp

    @app.errorhandler(Exception)
    def _failed(err):
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException):
            log.log(f"{request.method} {request.path} -> {err.code} {err.description}")
            return jsonify(message=err.description), err.code
        log.log(f"{request.method} {request.path} -> 500 {type(err).__name__}: {err}")
        app.logger.exception(err)
        return jsonify(message=f"{type(err).__name__}: {err}"), 500

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

    @app.get("/api/master.png")
    def master_png():
        """The master asset rasterised — the PSD as the designer drew it, for
        reference while editing. ``?w=`` serves a scaled copy for the sidebar."""
        img = session().source.composite
        w = request.args.get("w", type=int)
        if w and 0 < w < img.width:
            img = img.resize((w, max(1, round(img.height * w / img.width))),
                             Image.LANCZOS)
        return png(img, max_age=3600)

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
                      uid=request.args.get("uid", ""),
                      name=request.args.get("name", ""), params=params)
        tile = placement_tile(p, s.source, int(request.args.get("ss", 2)))
        if tile is None:
            abort(404)
        return png(tile, max_age=3600)

    # ------------------------------------------------------------ formats --
    @app.post("/api/formats")
    def api_add_format():
        """Register a custom target size. The core algorithm lays it out like
        any other format; `review` reports whether that succeeded."""
        d = request.get_json(silent=True) or {}
        try:
            w, h = int(d.get("width")), int(d.get("height"))
        except (TypeError, ValueError):
            abort(400, "width and height must be whole numbers")
        s = session()
        f = s.add_format(w, h)
        return jsonify({"format": f.name, "manifest": s.manifest()})

    @app.delete("/api/formats/<fmt>")
    def api_drop_format(fmt):
        s = session()
        if fmt in {x.name for x in FORMATS}:
            abort(400, "the six standard sizes cannot be removed")
        s.drop_format(fmt)
        return jsonify(s.manifest())

    @app.get("/api/review")
    def api_review():
        """Per-format warnings — what the algorithm could not do at that size."""
        s = session()
        return jsonify({f.name: review(s.plan(f.name), s.source)
                        for f in s.all_formats()})

    # -------------------------------------------------------------- plans --
    def plan_payload(name: str, plan: LayoutPlan, s: Session) -> dict:
        return {"format": name, "plan": plan.to_json(),
                "edited": name in s.saved, "rev": s.rev,
                "warnings": review(plan, s.source)}

    @app.get("/api/plan/<fmt>")
    def api_plan(fmt):
        s = session()
        return jsonify(plan_payload(fmt, s.plan(fmt), s))

    @app.post("/api/plan/<fmt>/raw")
    def api_plan_raw(fmt):
        """Every layer in reading order, nothing else — the starting point when
        the algorithmic layout cannot work at this size. Not saved until the
        client PUTs it back."""
        s = session()
        f = s.fmt(fmt)
        plan = plan_raw(s.source, f.width, f.height)
        return jsonify({"format": fmt, "plan": plan.to_json(),
                        "edited": fmt in s.saved, "rev": s.rev,
                        "warnings": review(plan, s.source)})

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
                        "edited": fmt in s.saved, "rev": s.rev,
                        "warnings": review(plan, s.source)})

    @app.post("/api/plan/<fmt>/copy-to")
    def api_plan_copy_to(fmt):
        """Reuse this format's layout at other sizes.

        Fixing a master the algorithm read badly is a lot of work, and doing it
        again per format is both the same work and a good way to end up with
        three banners that do not match. The layout is rescaled (see
        :func:`adapt.pipeline.rescale_plan`) and saved as each target's own
        hand-edited plan, which it then is — free to diverge afterwards.

        Targets that already carry manual edits are skipped unless ``overwrite``
        is set, so this cannot quietly destroy work done at the other size.
        """
        s = session()
        s.fmt(fmt)                                   # validates the source format
        d = request.get_json(silent=True) or {}
        targets = [str(t) for t in (d.get("targets") or [])]
        overwrite = bool(d.get("overwrite"))
        if not targets:
            abort(400, "no target sizes given")

        plan = s.plan(fmt)
        written, skipped = [], []
        for name in targets:
            if name == fmt:
                continue
            f = s.fmt(name)                          # 404s an unknown size
            if name in s.saved and not overwrite:
                skipped.append(name)
                continue
            s.set_plan(name, rescale_plan(plan, f.width, f.height, s.source))
            written.append(name)
        log.log(f"copied {fmt} layout to {', '.join(written) or 'nothing'}"
                + (f" (skipped edited: {', '.join(skipped)})" if skipped else ""))
        return jsonify({"written": written, "skipped": skipped,
                        "manifest": s.manifest()})

    # ------------------------------------------------------------ renders --
    def _render_bytes(fmt: str, plan: LayoutPlan) -> bytes:
        """PNG bytes for a plan, serialised against other heavy work and cached."""
        s = session()
        f = s.fmt(fmt)

        def make():
            with s.lock:
                img = render_image(plan, s.source)
            assert img.size == (f.width, f.height), f"size mismatch for {fmt}"
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()

        return s.cached_render(fmt, plan, make)

    def png_bytes(data: bytes, download_name: str | None = None):
        resp = send_file(io.BytesIO(data), mimetype="image/png",
                         as_attachment=bool(download_name),
                         download_name=download_name)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/render/<fmt>.png")
    def api_render(fmt):
        """The stored plan, rendered. ``?download=1`` makes it an attachment, so
        the browser saves it with its own download machinery — no fetch, no blob,
        and nothing to fail halfway through on a slow render."""
        s = session()
        return png_bytes(_render_bytes(fmt, s.plan(fmt)),
                         download_name=f"{fmt}.png"
                         if request.args.get("download") else None)

    @app.post("/api/render/<fmt>.png")
    def api_render_posted(fmt):
        plan = LayoutPlan.from_json(request.get_json())
        download = request.args.get("download")
        return png_bytes(_render_bytes(fmt, plan),
                         download_name=f"{fmt}.png" if download else None)

    @app.post("/api/export")
    def api_export():
        s = session()
        out = app.config["OUT_DIR"]
        os.makedirs(out, exist_ok=True)
        written = []
        with log.step(f"export {len(s.all_formats())} formats to {out}"):
            for f in s.all_formats():
                path = os.path.join(out, f"{f.name}.png")
                with open(path, "wb") as fh:
                    fh.write(_render_bytes(f.name, s.plan(f.name)))
                written.append(path.replace("\\", "/"))
        return jsonify({"written": written,
                        "layouts": store.path_for(s.path, out).replace("\\", "/")})

    @app.get("/api/download.zip")
    def api_download_zip():
        """Every format in one archive, as a plain attachment — the reliable way
        to get the edited renders off the machine in one go."""
        s = session()
        buf = io.BytesIO()
        with log.step(f"zip {len(s.all_formats())} formats"):
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in s.all_formats():
                    z.writestr(f"{f.name}.png", _render_bytes(f.name, s.plan(f.name)))
        buf.seek(0)
        stem = os.path.splitext(os.path.basename(s.path))[0]
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{stem}-adapt.zip")

    return app


def serve(input_dir: str = "input", out_dir: str = "output",
          host: str = "127.0.0.1", port: int = 8000, debug: bool = False):
    app = create_app(input_dir, out_dir)
    print(f"adapt editor: http://{host}:{port}/")
    app.run(host=host, port=port, debug=debug, threaded=True)
