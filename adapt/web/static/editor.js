/* Per-format layout editor.

   The plan (see adapt/layout.py) is the single source of truth: every gesture
   mutates a placement's box, the browser previews it with server-rendered tiles,
   and Export posts the same plan back to be rasterised by the pipeline's own
   renderer. Nothing about the layout is re-implemented here. */

const $ = (id) => document.getElementById(id);
const FMT = document.body.dataset.fmt;
const MIN = 4;                       // smallest allowed box, in output px
const HISTORY = 120;                 // undo depth

let manifest = null, plan = null, info = null;
let zoom = 1, selId = null, saveTimer = null;
const selIds = new Set();            // every selected id; selId is the primary
let undoStack = [], redoStack = [], cropping = null;
const boxes = new Map();             // placement id -> DOM node

/* Selection is a set with a "primary" (the last one clicked). `sel()` is the
   single-placement view the properties panel uses — deliberately null when more
   than one is selected, so per-element fields cannot silently edit just one of
   them; group operations take over instead. */
const selected = () => plan.placements.filter((p) => selIds.has(p.id));
const sel = () => (selIds.size === 1
  ? plan.placements.find((p) => selIds.has(p.id)) || null
  : null);
const primary = () => plan.placements.find((p) => p.id === selId) || null;

/* The union box of a selection, in output pixels. Group move and resize work on
   this rather than on each box, so the arrangement inside it is preserved. */
function groupBox(list = selected()) {
  if (!list.length) return null;
  const x = Math.min(...list.map((p) => p.x));
  const y = Math.min(...list.map((p) => p.y));
  const r = Math.max(...list.map((p) => p.x + p.w));
  const b = Math.max(...list.map((p) => p.y + p.h));
  return { x, y, w: Math.max(1e-6, r - x), h: Math.max(1e-6, b - y) };
}
const byZ = () => [...plan.placements].sort((a, b) => a.z - b.z);
const isText = (p) => p.kind === "element" && p.params.mode === "reflow";
const canCrop = (p) => p.kind === "element" && p.params.mode !== "reflow";
/* A background box is a viewport onto the master's imagery rather than a picture
   of a thing, so it is framed (fit / zoom / focus) instead of cropped. */
const isBg = (p) => p.kind === "photo_band" || p.kind === "base_image";
const fitOf = (p) => p.params.fit || (p.kind === "photo_band" ? "cover" : "stretch");
const clamp01 = (v) => Math.max(0, Math.min(1, v));

/* How big the imagery is once scaled into a background box, so a drag measured
   in canvas pixels converts to the right change in focus. Mirrors
   adapt.background.cover_scale — the box is the viewport, this is the picture. */
function imageryPx(p) {
  const sw = manifest.width, sh = manifest.height;
  const fit = fitOf(p);
  let s = fit === "contain" ? Math.min(p.w / sw, p.h / sh)
                            : Math.max(p.w / sw, p.h / sh);
  s *= p.params.zoom ?? 1;
  return { w: Math.max(1, sw * s), h: Math.max(1, sh * s) };
}

/* ------------------------------------------------------------- loading -- */
async function boot() {
  try {
    manifest = await api("/api/manifest");
  } catch (e) {
    document.querySelector(".workspace").innerHTML =
      '<div class="empty">No asset loaded — <a href="/">open one first</a>.</div>';
    return;
  }
  info = manifest.formats.find((f) => f.name === FMT);
  if (!info) {
    document.querySelector(".workspace").innerHTML =
      `<div class="empty">${FMT} is not a size on this asset — <a href="/">back</a>.</div>`;
    return;
  }
  $("strategy").textContent = info.strategy;
  buildTabs();
  initMaster(manifest);

  // ?raw=1 — arrive straight in the manual fallback layout (from a warned card)
  if (new URLSearchParams(location.search).get("raw")) {
    await loadPlan(await api(`/api/plan/${FMT}/raw`, { method: "POST" }));
    markDirty();
    toast("raw layout — every layer in order, arrange as you like");
  } else {
    await loadPlan();
  }
  fitZoom();
}

function buildTabs() {
  $("tabs").innerHTML = manifest.formats.map((f) =>
    f.name === FMT ? `<b>&nbsp;${f.name}&nbsp;</b>`
                   : `&nbsp;<a href="/edit/${f.name}">${f.name}</a>&nbsp;`).join("");
}

async function loadPlan(fresh = null) {
  const data = fresh || await api(`/api/plan/${FMT}`);
  plan = data.plan;
  $("editedBadge").hidden = !data.edited;
  $("explode").hidden = !(plan.strategy === "fit" || plan.strategy === "photo");
  showWarnings(data.warnings);
  $("cw").value = plan.width;
  $("ch").value = plan.height;
  undoStack = []; redoStack = [];
  selIds.clear();
  selId = null;
  rebuild();
}

function showWarnings(warnings) {
  const el = $("warnings");
  if (!warnings || !warnings.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<b>This size is a stretch for the algorithm.</b>
    <ul>${warnings.map((w) => `<li>${w}</li>`).join("")}</ul>
    <button id="goRaw">Start from a raw layout instead</button>`;
  $("goRaw").onclick = async () => {
    snapshot();
    const d = await api(`/api/plan/${FMT}/raw`, { method: "POST" });
    plan = d.plan; showWarnings(d.warnings); rebuild(); markDirty();
  };
}

function rebuild() {
  boxes.forEach((n) => n.remove());
  boxes.clear();
  // a restack or a removal can leave ids behind that no longer exist
  for (const id of [...selIds]) {
    if (!plan.placements.some((p) => p.id === id)) selIds.delete(id);
  }
  if (!selIds.has(selId)) selId = [...selIds][selIds.size - 1] ?? null;
  paint();
}

/* --------------------------------------------------------------- stage -- */
/* Fit both axes: a 160x600 skyscraper must not open at 600% just because it is
   narrow, and tiles are only fetched at 2x so extreme zoom would look soft. */
function fitZoom() {
  const wrap = $("stageWrap");
  const fit = Math.min((wrap.clientWidth - 60) / plan.width,
                       (wrap.clientHeight - 60) / plan.height);
  const z = Math.max(0.5, Math.min(4, Math.floor(fit * 2) / 2));
  $("zoom").value = z;
  setZoom(z);
}

function setZoom(z) {
  zoom = z;
  $("zoomLabel").textContent = `${Math.round(z * 100)}%`;
  paint();
  if (cropping) paintCrop();
}

function paint() {
  const stage = $("stage");
  stage.style.width = `${plan.width * zoom}px`;
  stage.style.height = `${plan.height * zoom}px`;
  stage.style.backgroundColor = cssColor(plan.base_color);
  for (const p of byZ()) syncBox(p);
  drawGroupFrame();
  drawLayers();
  drawProps();
}

function syncBox(p) {
  let el = boxes.get(p.id);
  if (!el) {
    el = document.createElement("div");
    el.className = "box";
    el.dataset.id = p.id;
    el.appendChild(document.createElement("img"));
    $("stage").appendChild(el);
    boxes.set(p.id, el);
  }
  el.style.left = `${p.x * zoom}px`;
  el.style.top = `${p.y * zoom}px`;
  el.style.width = `${p.w * zoom}px`;
  el.style.height = `${p.h * zoom}px`;
  el.style.zIndex = p.z;
  el.style.opacity = p.opacity ?? 1;
  el.classList.toggle("hidden", !p.visible);
  el.classList.toggle("sel", selIds.has(p.id) && !cropping);
  el.classList.toggle("outline", !selIds.has(p.id));
  el.style.background = p.kind === "color_bar" ? cssColor(p.params.color) : "";
  el.style.justifyContent = { center: "center", right: "flex-end" }[p.params.align]
                            || "flex-start";

  const img = el.querySelector("img");
  const url = tileURL(p);
  if (!url) {
    img.hidden = true;
  } else {
    img.hidden = false;
    if (img.dataset.url !== url && !deferTile(p)) {
      img.dataset.url = url;
      img.onload = () => onTileLoaded(p, img);
      img.src = url;
    }
    sizeTileImg(p, img);
  }
  if (selIds.size === 1 && selIds.has(p.id) && !cropping) addHandles(el);
  else el.querySelectorAll(".handle").forEach((h) => h.remove());
}

/* Framing a background re-renders its tile server-side, and a pan is a stream of
   pointer events. Rate-limit those requests during the gesture and take the
   truthful one when it ends, so dragging stays responsive without firing a
   render per frame. */
const BG_FPS_MS = 90;
let framingUntil = 0, lastBgTile = 0;
/* A deadline rather than a counter: it lapses on its own shortly after the last
   gesture event, so a pointerup that never arrives cannot leave previews stuck
   on a stale tile. */
const beginFraming = () => { framingUntil = performance.now() + 400; };
const endFraming = () => { framingUntil = 0; };

function deferTile(p) {
  const now = performance.now();
  if (!isBg(p) || now > framingUntil) return false;
  if (now - lastBgTile < BG_FPS_MS) return true;
  lastBgTile = now;
  return false;
}

/* A re-wrapped text block is only as wide as its longest line, and sits inside
   its column per `align` — exactly how the renderer pastes it. Everything else
   fills its box. */
function sizeTileImg(p, img) {
  if (isText(p) && img.naturalWidth) {
    img.style.width = `${(img.naturalWidth / SS) * zoom}px`;
    img.style.height = `${(img.naturalHeight / SS) * zoom}px`;
  } else {
    img.style.width = "100%";
    img.style.height = "100%";
  }
}

function onTileLoaded(p, img) {
  if (!isText(p)) return;
  const h = img.naturalHeight / SS;             // wrapped height is emergent
  if (Math.abs(h - p.h) > 0.5) {
    p.h = h;
    clampInside(p);
  }
  syncBox(p);
  drawProps();
}

const DIRS = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
function handleEls(host) {
  for (const d of DIRS) {
    const h = document.createElement("div");
    h.className = "handle";
    h.dataset.dir = d;
    h.style.cursor = `${d}-resize`;
    h.style.left = d.includes("w") ? "0%" : d.includes("e") ? "100%" : "50%";
    h.style.top = d.includes("n") ? "0%" : d.includes("s") ? "100%" : "50%";
    h.style.transform = "translate(-50%, -50%)";
    host.appendChild(h);
  }
}
function addHandles(el) {
  if (!el.querySelector(".handle")) handleEls(el);
}

/* ---------------------------------------------------------- selection -- */
/* `mode` is "set" (replace), "toggle" (ctrl/cmd-click) or "range" (shift-click
   in the layers panel, which takes everything between the primary and here). */
function select(id, mode = "set") {
  if (id === null) {
    selIds.clear();
    selId = null;
  } else if (mode === "toggle") {
    if (selIds.has(id) && selIds.size > 1) {
      selIds.delete(id);
      if (selId === id) selId = [...selIds][selIds.size - 1];
    } else {
      selIds.add(id);
      selId = id;
    }
  } else if (mode === "range" && selId && selId !== id) {
    const order = byZ().map((p) => p.id);
    const a = order.indexOf(selId), b = order.indexOf(id);
    if (a >= 0 && b >= 0) {
      for (const q of order.slice(Math.min(a, b), Math.max(a, b) + 1)) selIds.add(q);
    }
    selId = id;
  } else {
    selIds.clear();
    selIds.add(id);
    selId = id;
  }
  for (const p of plan.placements) syncBox(p);
  drawGroupFrame();
  drawLayers();
  drawProps();
}

/* One frame with handles around the whole selection. Per-box handles would be
   ambiguous with several selected — dragging one would have to mean either
   "resize that box" or "resize the group". */
function drawGroupFrame() {
  $("stage").querySelector("#groupFrame")?.remove();
  const g = selIds.size > 1 && !cropping ? groupBox() : null;
  if (!g) return;
  const el = document.createElement("div");
  el.id = "groupFrame";
  el.style.cssText = `left:${g.x * zoom}px;top:${g.y * zoom}px;`
    + `width:${g.w * zoom}px;height:${g.h * zoom}px`;
  handleEls(el);
  $("stage").appendChild(el);
}

/* ------------------------------------------------------------- history -- */
function snapshot() {
  const s = JSON.stringify(plan);
  if (undoStack[undoStack.length - 1] === s) return;
  undoStack.push(s);
  if (undoStack.length > HISTORY) undoStack.shift();
  redoStack.length = 0;
  syncHistoryButtons();
}

function syncHistoryButtons() {
  $("undo").disabled = !undoStack.length;
  $("redo").disabled = !redoStack.length;
}

function step(from, to) {
  if (!from.length) return;
  to.push(JSON.stringify(plan));
  plan = JSON.parse(from.pop());
  $("cw").value = plan.width;
  $("ch").value = plan.height;
  rebuild();
  syncHistoryButtons();
  autosave();
}
const undo = () => step(undoStack, redoStack);
const redo = () => step(redoStack, undoStack);

/* ------------------------------------------------------- drag & resize -- */
let drag = null;

$("stage").addEventListener("pointerdown", (e) => {
  if (cropping) return;
  const handleEl = e.target.closest(".handle");

  // A handle on the group frame resizes the whole selection at once.
  if (handleEl && handleEl.parentElement.id === "groupFrame") {
    startGroupDrag(e, handleEl.dataset.dir);
    return;
  }

  const boxEl = e.target.closest(".box");
  if (!boxEl) { select(null); return; }
  const p = plan.placements.find((x) => x.id === boxEl.dataset.id);
  if (!p) return;
  const add = e.ctrlKey || e.metaKey || e.shiftKey;
  if (!handleEl) {
    // Clicking inside an existing multi-selection keeps it, so the whole group
    // can be dragged — replacing it would make a group impossible to move.
    if (add) select(p.id, "toggle");
    else if (!selIds.has(p.id)) select(p.id);
  }
  if (selIds.size > 1 && !handleEl) { startGroupDrag(e, null); return; }
  // Alt-drag inside a background box slides the imagery within the frame rather
  // than moving the frame — the box stays put, the picture behind it moves.
  const pan = !handleEl && e.altKey && isBg(p);
  if (pan && fitOf(p) === "stretch") p.params.fit = "cover";
  drag = {
    p, dir: handleEl ? handleEl.dataset.dir : null, moved: false, pan,
    sx: e.clientX, sy: e.clientY,
    o: { x: p.x, y: p.y, w: p.w, h: p.h, line_h: p.params.line_h || 0,
         fx: p.params.focus_x ?? 0.5, fy: p.params.focus_y ?? 0.5 },
  };
  e.target.setPointerCapture(e.pointerId);
  e.preventDefault();
});

$("stage").addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = (e.clientX - drag.sx) / zoom;
  const dy = (e.clientY - drag.sy) / zoom;
  if (!drag.moved) {
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;  // a click, not a drag
    drag.moved = true;
    undoStack.push(JSON.stringify(plan));       // snapshot the pre-drag state
    redoStack.length = 0;
    syncHistoryButtons();
  }
  if (drag.pan) applyPan(drag, dx, dy);
  else if (drag.dir) applyResize(drag, dx, dy, e.shiftKey);
  else applyMove(drag, dx, dy);
  syncBox(drag.p);
  drawProps();
});

function endDrag() {
  if (!drag) return;
  clearGuides();
  const { p, moved, pan } = drag;
  drag = null;
  if (pan) endFraming();
  if (!moved) return;
  if (isText(p)) syncBox(p);           // re-fetch the wrap at the new width
  if (pan) syncBox(p);                 // take the real tile now the drag is over
  markDirty();
}
$("stage").addEventListener("pointerup", endDrag);
$("stage").addEventListener("pointercancel", endDrag);

function applyMove(d, dx, dy) {
  const p = d.p;
  p.x = d.o.x + dx;
  p.y = d.o.y + dy;
  snap(p);
  clampInside(p);
}

/* ------------------------------------------------------ group gestures -- */
/* Several boxes move and resize as one. The group's union box is what the
   gesture acts on, and each member keeps its position and size *relative* to
   that box — so the arrangement inside the selection survives, which is the
   whole point of selecting several. */
let gdrag = null;

function startGroupDrag(e, dir) {
  const list = selected();
  if (!list.length) return;
  gdrag = {
    dir, moved: false, sx: e.clientX, sy: e.clientY,
    box: groupBox(list),
    items: list.map((p) => ({
      p, x: p.x, y: p.y, w: p.w, h: p.h, line_h: p.params.line_h || 0,
    })),
  };
  e.target.setPointerCapture(e.pointerId);
  e.preventDefault();
}

function groupMove(dx, dy) {
  const g = gdrag.box;
  // Clamp the whole group, not each box: clamping individually would squeeze
  // the members together against the frame edge and lose the arrangement.
  const ox = Math.max(-g.x, Math.min(dx, plan.width - g.w - g.x));
  const oy = Math.max(-g.y, Math.min(dy, plan.height - g.h - g.y));
  for (const it of gdrag.items) {
    it.p.x = it.x + ox;
    it.p.y = it.y + oy;
  }
}

function groupResize(dx, dy, freeAspect) {
  const g = gdrag.box, d = gdrag.dir;
  let fx = 1, fy = 1;
  if (d.includes("e")) fx = (g.w + dx) / g.w;
  if (d.includes("w")) fx = (g.w - dx) / g.w;
  if (d.includes("s")) fy = (g.h + dy) / g.h;
  if (d.includes("n")) fy = (g.h - dy) / g.h;
  // Corners scale uniformly unless Shift: a group has no single aspect to keep,
  // so distorting it by default would silently reshape every member.
  if (!freeAspect && d.length === 2) fx = fy = Math.min(fx, fy);
  const lo = MIN / Math.max(g.w, g.h);
  fx = Math.max(lo, fx);
  fy = Math.max(lo, fy);

  // Anchor the edge or corner opposite the one being dragged.
  const ax = d.includes("w") ? g.x + g.w : g.x;
  const ay = d.includes("n") ? g.y + g.h : g.y;
  const s = Math.min(fx, fy);

  for (const it of gdrag.items) {
    const p = it.p;
    p.x = ax + (it.x - ax) * fx;
    p.y = ay + (it.y - ay) * fy;
    // Same rules the server uses when rescaling a whole plan onto a new size
    // (pipeline.rescale_plan), so a group resize and a Copy-to agree.
    if (isText(p)) {
      p.w = it.w * fx;
      p.h = it.h * fy;
      p.params.line_h = Math.max(3, Math.round(it.line_h * s));
    } else if (p.lock_aspect) {
      p.w = it.w * s;
      p.h = it.h * s;
    } else {
      p.w = it.w * fx;
      p.h = it.h * fy;
    }
  }
  // Only pull back inside once the whole group is placed, so a member is not
  // clamped against an edge the group is still moving away from.
  const now = groupBox(gdrag.items.map((it) => it.p));
  const back = {
    x: Math.min(0, plan.width - (now.x + now.w)) - Math.min(0, now.x),
    y: Math.min(0, plan.height - (now.y + now.h)) - Math.min(0, now.y),
  };
  if (back.x || back.y) {
    for (const it of gdrag.items) { it.p.x += back.x; it.p.y += back.y; }
  }
}

$("stage").addEventListener("pointermove", (e) => {
  if (!gdrag) return;
  const dx = (e.clientX - gdrag.sx) / zoom;
  const dy = (e.clientY - gdrag.sy) / zoom;
  if (!gdrag.moved) {
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
    gdrag.moved = true;
    undoStack.push(JSON.stringify(plan));
    redoStack.length = 0;
    syncHistoryButtons();
  }
  if (gdrag.dir) groupResize(dx, dy, e.shiftKey);
  else groupMove(dx, dy);
  for (const it of gdrag.items) syncBox(it.p);
  drawGroupFrame();
  drawProps();
});

function endGroupDrag() {
  if (!gdrag) return;
  const { items, moved } = gdrag;
  gdrag = null;
  if (!moved) return;
  for (const it of items) if (isText(it.p)) syncBox(it.p);   // re-wrap at new width
  drawGroupFrame();
  markDirty();
}
$("stage").addEventListener("pointerup", endGroupDrag);
$("stage").addEventListener("pointercancel", endGroupDrag);

/* Dragging right should carry the imagery right, which means the viewport moves
   left over it — hence the sign. The divisor is the scaled picture, so a pan
   feels the same at any zoom. */
function applyPan(d, dx, dy) {
  const p = d.p, px = imageryPx(p);
  p.params.focus_x = clamp01(d.o.fx - dx / px.w);
  p.params.focus_y = clamp01(d.o.fy - dy / px.h);
  beginFraming();
}

/* Alt+wheel over a selected background zooms the imagery inside its frame. Alt
   is required so ordinary scrolling still scrolls the stage. */
let zoomHold = null;
$("stage").addEventListener("wheel", (e) => {
  const p = sel();
  if (!p || !isBg(p) || !e.altKey || cropping) return;
  e.preventDefault();
  if (!zoomHold) snapshot();
  clearTimeout(zoomHold);
  beginFraming();
  if (fitOf(p) === "stretch") p.params.fit = "cover";
  const z = (p.params.zoom ?? 1) * Math.pow(1.0015, -e.deltaY);
  p.params.zoom = Math.round(Math.max(0.1, Math.min(6, z)) * 1000) / 1000;
  syncBox(p);
  drawProps();
  zoomHold = setTimeout(() => {        // settle: fetch the true tile, then save
    zoomHold = null;
    endFraming();
    syncBox(p);
    markDirty();
  }, 220);
}, { passive: false });

function applyResize(d, dx, dy, freeAspect) {
  const p = d.p, o = d.o;
  let { x, y, w, h } = o;
  if (d.dir.includes("e")) w = o.w + dx;
  if (d.dir.includes("w")) { w = o.w - dx; x = o.x + dx; }
  if (d.dir.includes("s")) h = o.h + dy;
  if (d.dir.includes("n")) { h = o.h - dy; y = o.y + dy; }
  w = Math.max(MIN, w);
  h = Math.max(MIN, h);

  if (isText(p)) {
    // Horizontal edges re-wrap the column; vertical edges change the type size.
    const corner = d.dir.length === 2;
    if (corner || d.dir === "n" || d.dir === "s") {
      const f = corner ? w / o.w : h / o.h;
      p.params.line_h = Math.max(3, Math.round(o.line_h * f));
    }
    if (corner || d.dir === "e" || d.dir === "w") p.w = w;
    if (d.dir.includes("w")) p.x = x;
    if (d.dir.includes("n")) p.y = y;
  } else {
    if (p.lock_aspect && !freeAspect && o.w > 0 && o.h > 0) {
      const a = o.w / o.h;
      if (d.dir.length === 2) h = w / a;             // corners: keep aspect
      else if (d.dir === "n" || d.dir === "s") w = h * a;
      else h = w / a;
      if (d.dir.includes("n")) y = o.y + (o.h - h);
      if (d.dir.includes("w")) x = o.x + (o.w - w);
    }
    Object.assign(p, { x, y, w, h });
  }
  clampInside(p);
}

/* Nothing may leave the frame — the same guarantee the renderer enforces. */
function clampInside(p) {
  p.w = Math.min(p.w, plan.width);
  p.h = Math.min(p.h, plan.height);
  p.x = Math.max(0, Math.min(p.x, plan.width - p.w));
  p.y = Math.max(0, Math.min(p.y, plan.height - p.h));
}

/* ------------------------------------------------------------ snapping -- */
const SNAP = 5;
function snap(p) {
  clearGuides();
  const tol = SNAP / zoom;
  const xs = [0, (plan.width - p.w) / 2, plan.width - p.w];
  const ys = [0, (plan.height - p.h) / 2, plan.height - p.h];
  for (const q of plan.placements) {
    if (q.id === p.id || !q.visible) continue;
    xs.push(q.x, q.x + q.w - p.w, q.x + (q.w - p.w) / 2);
    ys.push(q.y, q.y + q.h - p.h, q.y + (q.h - p.h) / 2);
  }
  for (const cx of xs) if (Math.abs(p.x - cx) < tol) { p.x = cx; guide("v", cx); break; }
  for (const cy of ys) if (Math.abs(p.y - cy) < tol) { p.y = cy; guide("h", cy); break; }
}

function guide(axis, at) {
  const g = document.createElement("div");
  g.className = "guide";
  g.style.cssText = axis === "v"
    ? `left:${at * zoom}px;top:0;width:1px;height:100%`
    : `top:${at * zoom}px;left:0;height:1px;width:100%`;
  $("stage").appendChild(g);
}
const clearGuides = () => $("stage").querySelectorAll(".guide").forEach((g) => g.remove());

/* ---------------------------------------------------------------- crop -- */
/* The crop is stored as fractions of the element's own image, so it keeps
   meaning at any box size (see tiles.crop_fractions). */
function fullRect(p) {
  const c = p.params.crop;
  if (!c) return { x: p.x, y: p.y, w: p.w, h: p.h };
  const [l, t, r, b] = c;
  const fw = p.w / Math.max(1e-6, r - l);
  const fh = p.h / Math.max(1e-6, b - t);
  return { x: p.x - l * fw, y: p.y - t * fh, w: fw, h: fh };
}

function startCrop(p) {
  cropping = { p, full: fullRect(p), rect: { x: p.x, y: p.y, w: p.w, h: p.h } };
  const layer = document.createElement("div");
  layer.id = "cropLayer";
  layer.innerHTML = `<img class="cdim">
    <div class="croprect"><img class="cfull"></div>`;
  $("stage").appendChild(layer);
  const src = stamped(`/api/element/${p.element}.png`);
  layer.querySelector(".cdim").src = src;
  layer.querySelector(".cfull").src = src;
  handleEls(layer.querySelector(".croprect"));
  layer.addEventListener("pointerdown", onCropDown);
  syncBox(p);
  paintCrop();
  drawProps();
}

function paintCrop() {
  const layer = $("cropLayer");
  if (!layer || !cropping) return;
  const { full, rect } = cropping;
  Object.assign(layer.style, {
    left: `${full.x * zoom}px`, top: `${full.y * zoom}px`,
    width: `${full.w * zoom}px`, height: `${full.h * zoom}px`,
  });
  const r = layer.querySelector(".croprect");
  Object.assign(r.style, {
    left: `${(rect.x - full.x) * zoom}px`, top: `${(rect.y - full.y) * zoom}px`,
    width: `${rect.w * zoom}px`, height: `${rect.h * zoom}px`,
  });
  Object.assign(r.querySelector(".cfull").style, {
    left: `${-(rect.x - full.x) * zoom}px`, top: `${-(rect.y - full.y) * zoom}px`,
    width: `${full.w * zoom}px`, height: `${full.h * zoom}px`,
  });
}

let cropDrag = null;
function onCropDown(e) {
  const h = e.target.closest(".handle");
  cropDrag = { dir: h ? h.dataset.dir : null, sx: e.clientX, sy: e.clientY,
               o: { ...cropping.rect } };
  e.target.setPointerCapture(e.pointerId);
  e.preventDefault();
  e.stopPropagation();
}

document.addEventListener("pointermove", (e) => {
  if (!cropDrag || !cropping) return;
  const dx = (e.clientX - cropDrag.sx) / zoom;
  const dy = (e.clientY - cropDrag.sy) / zoom;
  const { full } = cropping;
  const o = cropDrag.o;
  let { x, y, w, h } = o;
  if (!cropDrag.dir) { x = o.x + dx; y = o.y + dy; }
  else {
    if (cropDrag.dir.includes("e")) w = o.w + dx;
    if (cropDrag.dir.includes("w")) { w = o.w - dx; x = o.x + dx; }
    if (cropDrag.dir.includes("s")) h = o.h + dy;
    if (cropDrag.dir.includes("n")) { h = o.h - dy; y = o.y + dy; }
  }
  w = Math.max(MIN, Math.min(w, full.w));
  h = Math.max(MIN, Math.min(h, full.h));
  cropping.rect = {
    x: Math.max(full.x, Math.min(x, full.x + full.w - w)),
    y: Math.max(full.y, Math.min(y, full.y + full.h - h)),
    w, h,
  };
  paintCrop();
});
document.addEventListener("pointerup", () => { cropDrag = null; });

function endCrop(apply) {
  if (!cropping) return;
  const { p, full, rect } = cropping;
  if (apply) {
    snapshot();
    p.params.crop = [(rect.x - full.x) / full.w, (rect.y - full.y) / full.h,
                     (rect.x + rect.w - full.x) / full.w,
                     (rect.y + rect.h - full.y) / full.h];
    Object.assign(p, { x: rect.x, y: rect.y, w: rect.w, h: rect.h });
    clampInside(p);
    markDirty();
  }
  $("cropLayer")?.remove();
  cropping = null;
  cropDrag = null;
  syncBox(p);
  drawProps();
}

function resetCrop(p) {
  snapshot();
  const full = fullRect(p);
  delete p.params.crop;
  Object.assign(p, full);
  clampInside(p);
  syncBox(p);
  drawProps();
  markDirty();
}

/* -------------------------------------------------------------- layers -- */
function drawLayers() {
  const host = $("layers");
  host.innerHTML = "";
  for (const p of byZ().reverse()) {
    const row = document.createElement("div");
    row.className = `layer${selIds.has(p.id) ? " sel" : ""}${p.visible ? "" : " hidden"}`;
    row.draggable = true;
    row.dataset.id = p.id;
    row.innerHTML = `<span class="grip">⠿</span>
      <span class="lname">${label(p)}</span>
      <span class="lrole">${p.kind === "element" ? p.role : p.kind.replace("_", " ")}</span>
      <button class="eye stackbtn" data-stack="front" title="bring to front">⤒</button>
      <button class="eye stackbtn" data-stack="back" title="send to back">⤓</button>
      <button class="eye" title="show / hide">${p.visible ? "◉" : "○"}</button>`;
    row.onclick = (e) => {
      const where = e.target.dataset.stack;
      if (where) return stack(p, where);
      if (e.target.classList.contains("eye")) {
        snapshot();
        // The eye on a row inside a multi-selection acts on the whole selection,
        // so a dozen decorative layers go dark in one click.
        const targets = selIds.has(p.id) && selIds.size > 1 ? selected() : [p];
        const to = !p.visible;
        for (const q of targets) q.visible = to;
        targets.forEach(syncBox);
        drawLayers(); drawProps(); markDirty();
      } else {
        select(p.id, e.shiftKey ? "range"
                   : (e.ctrlKey || e.metaKey) ? "toggle" : "set");
      }
    };
    row.ondragstart = (e) => e.dataTransfer.setData("text/plain", p.id);
    row.ondragover = (e) => {
      e.preventDefault();
      const before = e.offsetY < row.offsetHeight / 2;
      row.classList.toggle("drop-before", before);
      row.classList.toggle("drop-after", !before);
    };
    row.ondragleave = () => row.classList.remove("drop-before", "drop-after");
    row.ondrop = (e) => {
      e.preventDefault();
      row.classList.remove("drop-before", "drop-after");
      reorder(e.dataTransfer.getData("text/plain"), p.id,
              e.offsetY < row.offsetHeight / 2);
    };
    host.appendChild(row);
  }
}

/* Rows read top-down as top-most first, so "before" in the list means above. */
function reorder(dragId, targetId, above) {
  if (dragId === targetId) return;
  snapshot();
  const order = byZ();                                  // bottom -> top
  const moving = order.find((p) => p.id === dragId);
  const rest = order.filter((p) => p.id !== dragId);
  const at = rest.findIndex((p) => p.id === targetId);
  rest.splice(above ? at + 1 : at, 0, moving);
  restackTo(rest);
}

/* Re-seat a bottom->top ordering onto the plan. Both `z` and the array order are
   kept in step so the plan reads the same way it draws. */
function restackTo(order) {
  order.forEach((p, i) => { p.z = i; });
  plan.placements = order;
  paint();
  markDirty();
}

/* Explicit stacking commands. Dragging a background out to full width puts it
   over whatever it now covers, and hunting for the right drop slot in the layers
   list is a poor way to fix that — so front/back/one-step are direct actions on
   the selection. */
function stack(p, where) {
  const order = byZ();
  const i = order.findIndex((q) => q.id === p.id);
  const j = { front: order.length - 1, back: 0,
              forward: i + 1, backward: i - 1 }[where];
  if (i < 0 || j === i || j < 0 || j > order.length - 1) return;
  snapshot();
  order.splice(i, 1);
  order.splice(j, 0, p);
  restackTo(order);
}

/* Properties for a multi-selection: the operations that make sense on several
   boxes at once. Per-element fields are deliberately absent — one x/y box would
   have to either edit all of them to the same value (destroying the
   arrangement) or silently edit only one. */
function drawGroupProps(host) {
  const list = selected();
  const g = groupBox(list);
  const hidden = list.filter((p) => !p.visible).length;
  host.innerHTML = `
    <div class="prop"><label>selected</label><b>${list.length} layers</b></div>
    <div class="prop"><label>bounds</label>
      <span class="lrole">${Math.round(g.w)} x ${Math.round(g.h)} at
      ${Math.round(g.x)}, ${Math.round(g.y)}</span></div>
    <div class="hint">Drag inside the frame to move them together; drag a handle
      to resize them together (Shift to stretch freely). Arrow keys nudge.</div>
    <div class="divider"></div>
    <div class="cap" style="padding:2px 12px">Align</div>
    <div class="rowbtns">
      <button data-g="left">Left</button>
      <button data-g="hcentre">Centre</button>
      <button data-g="right">Right</button>
      <button data-g="top">Top</button>
      <button data-g="vcentre">Middle</button>
      <button data-g="bottom">Bottom</button>
    </div>
    <div class="cap" style="padding:2px 12px">Arrange</div>
    <div class="rowbtns">
      <button data-g="row" title="Lay them out side by side, in their current order">Side by side</button>
      <button data-g="column" title="Stack them one above the other">Stacked</button>
      <button data-g="spreadH">Space across</button>
      <button data-g="spreadV">Space down</button>
    </div>
    <div class="cap" style="padding:2px 12px">Whole selection</div>
    <div class="rowbtns">
      <button data-g="centreInFrame">Centre in frame</button>
      <button data-g="visible">${hidden ? "Show all" : "Hide all"}</button>
      <button data-g="front">Bring to front</button>
      <button data-g="back">Send to back</button>
      <button data-g="remove">Remove</button>
    </div>`;
  host.querySelectorAll("[data-g]").forEach((b) => {
    b.onclick = () => groupOp(b.dataset.g, list);
  });
}

/* One place for every group action, so they all snapshot and repaint alike. */
function groupOp(op, list) {
  snapshot();
  const g = groupBox(list);
  const ordered = [...list].sort((a, b) => (a.x - b.x) || (a.y - b.y));
  const stacked = [...list].sort((a, b) => (a.y - b.y) || (a.x - b.x));

  if (op === "left") for (const p of list) p.x = g.x;
  if (op === "right") for (const p of list) p.x = g.x + g.w - p.w;
  if (op === "hcentre") for (const p of list) p.x = g.x + (g.w - p.w) / 2;
  if (op === "top") for (const p of list) p.y = g.y;
  if (op === "bottom") for (const p of list) p.y = g.y + g.h - p.h;
  if (op === "vcentre") for (const p of list) p.y = g.y + (g.h - p.h) / 2;

  if (op === "row") {                       // butt them up left to right
    let x = g.x;
    for (const p of ordered) { p.x = x; x += p.w; }
  }
  if (op === "column") {                    // ...or top to bottom
    let y = g.y;
    for (const p of stacked) { p.y = y; y += p.h; }
  }
  if (op === "spreadH") {                   // equal gaps across the group box
    const gap = (g.w - ordered.reduce((t, p) => t + p.w, 0))
                / Math.max(1, ordered.length - 1);
    let x = g.x;
    for (const p of ordered) { p.x = x; x += p.w + gap; }
  }
  if (op === "spreadV") {
    const gap = (g.h - stacked.reduce((t, p) => t + p.h, 0))
                / Math.max(1, stacked.length - 1);
    let y = g.y;
    for (const p of stacked) { p.y = y; y += p.h + gap; }
  }

  if (op === "centreInFrame") {
    const ox = (plan.width - g.w) / 2 - g.x;
    const oy = (plan.height - g.h) / 2 - g.y;
    for (const p of list) { p.x += ox; p.y += oy; }
  }
  if (op === "visible") {
    const to = list.some((p) => !p.visible);
    for (const p of list) p.visible = to;
  }
  if (op === "front" || op === "back") {
    // Move them as a block, keeping their order relative to each other.
    const rest = byZ().filter((p) => !selIds.has(p.id));
    const block = byZ().filter((p) => selIds.has(p.id));
    restackTo(op === "front" ? [...rest, ...block] : [...block, ...rest]);
    return;                                  // restackTo repaints and saves
  }
  if (op === "remove") {
    if (!confirm(`Remove ${list.length} layers from this layout? `
                 + `Ctrl+Z undoes it, and Reset to algorithm brings everything back.`))
      return;
    plan.placements = plan.placements.filter((p) => !selIds.has(p.id));
    normaliseZ();
    select(null);
    rebuild();
    markDirty();
    return;
  }

  list.forEach(clampInside);
  list.forEach(syncBox);
  drawGroupFrame();
  drawLayers();
  drawProps();
  markDirty();
}

/* ---------------------------------------------------------- properties -- */
function num(lbl, val, on, step = 1) {
  return `<div class="prop"><label>${lbl}</label>
    <input type="number" step="${step}" value="${Math.round(val * 100) / 100}"
           data-on="${on}"></div>`;
}

const bgIdFor = (p) => `bg-${p.id}`;
const hasBackdrop = (p) => plan.placements.some((q) => q.id === bgIdFor(p));

function drawProps() {
  const host = $("props");
  if (selIds.size > 1) return drawGroupProps(host);
  const p = sel();
  if (!p) {
    host.innerHTML = `<div class="hint">Select an element on the canvas.
      Ctrl+click (or Shift+click a layer row) to select several and move,
      resize, align or hide them together.</div>`;
    return;
  }
  if (cropping) {
    host.innerHTML = `<div class="prop"><label>cropping</label><b>${label(p)}</b></div>
      <div class="hint">Drag the bright rectangle, or its handles, to choose the
      part of the element to keep.</div>
      <div class="rowbtns">
        <button class="primary" data-do="cropApply">Apply crop</button>
        <button data-do="cropCancel">Cancel</button>
      </div>`;
    wireProps(host, p);
    return;
  }

  let html = `<div class="prop"><label>name</label><b>${label(p)}</b></div>
              <div class="prop"><label>kind</label>${p.kind}${p.role ? " · " + p.role : ""}</div>
              <div class="divider"></div>`;
  html += num("x", p.x, "x") + num("y", p.y, "y");
  html += num("width", p.w, "w");
  html += isText(p)
    ? `<div class="prop"><label>height</label><span class="lrole">auto (wraps)</span></div>`
      + num("line height", p.params.line_h || 12, "line_h")
      + `<div class="prop"><label>align</label>
           <select data-on="align">${["left", "center", "right", "justify"].map((a) =>
             `<option ${p.params.align === a ? "selected" : ""}>${a}</option>`).join("")}
         </select></div>`
    : num("height", p.h, "h");

  html += `<div class="prop"><label>opacity</label>
      <input type="range" min="0" max="1" step="0.05" value="${p.opacity ?? 1}"
             data-on="opacity" data-live="1">
      <span class="lrole">${Math.round((p.opacity ?? 1) * 100)}%</span></div>`;

  if (p.kind === "color_bar") {
    const [r, g, b] = p.params.color;
    const hex = "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
    html += `<div class="prop"><label>colour</label>
             <input type="color" value="${hex}" data-on="color"></div>`;
  }
  if (isBg(p)) {
    const fit = fitOf(p);
    html += `<div class="prop"><label>fit</label>
        <select data-on="fit">${["cover", "contain", "stretch"].map((a) =>
          `<option ${fit === a ? "selected" : ""}>${a}</option>`).join("")}
      </select></div>`;
    if (fit !== "stretch") {
      html += `<div class="prop"><label>zoom</label>
          <input type="range" min="0.1" max="4" step="0.01" value="${p.params.zoom ?? 1}"
                 data-on="zoom" data-live="1">
          <span class="lrole">${Math.round((p.params.zoom ?? 1) * 100)}%</span></div>`;
      html += num("focus x", p.params.focus_x ?? 0.5, "focus_x", 0.01);
      html += num("focus y", p.params.focus_y ?? 0.5, "focus_y", 0.01);
    }
    if (p.kind === "photo_band") {
      html += num("feather", p.params.feather ?? 0, "feather", 0.01);
    }
    html += `<div class="rowbtns">
        <button data-do="bgWhole" title="Show the whole master inside this box">Whole image</button>
        <button data-do="bgFill" title="Fill the box, cropping the overflow">Fill box</button>
      </div>
      <div class="hint">Alt+drag inside the box to slide the imagery,
        Alt+wheel to zoom it.</div>`;
  }
  if (!isText(p)) {
    html += `<div class="prop"><label>lock aspect</label>
             <input type="checkbox" ${p.lock_aspect ? "checked" : ""} data-on="lock"></div>`;
  }

  html += `<div class="divider"></div><div class="rowbtns">
      <button data-do="centerH">Centre H</button>
      <button data-do="centerV">Centre V</button>
      <button data-do="fullW">Full width</button>
      <button data-do="top">Top</button>
      <button data-do="bottom">Bottom</button>
      <button data-do="hide">${p.visible ? "Hide" : "Show"}</button>
    </div>`;

  const depth = byZ().findIndex((q) => q.id === p.id);
  const topmost = depth === plan.placements.length - 1;
  html += `<div class="prop"><label>stacking</label>
      <span class="lrole">${depth + 1} of ${plan.placements.length}${
        topmost ? " (front)" : depth === 0 ? " (back)" : ""}</span></div>
    <div class="rowbtns">
      <button data-do="toFront" ${topmost ? "disabled" : ""}
              title="Draw this on top of everything (Ctrl+Shift+])">Bring to front</button>
      <button data-do="toBack" ${depth === 0 ? "disabled" : ""}
              title="Draw this behind everything (Ctrl+Shift+[)">Send to back</button>
    </div>`;

  if (p.kind === "element") {
    html += `<div class="divider"></div><div class="rowbtns">
      <button data-do="backdrop">${hasBackdrop(p) ? "Remove backdrop" : "Background behind"}</button>`;
    if (canCrop(p)) {
      html += `<button data-do="crop">Crop…</button>`;
      if (p.params.crop) html += `<button data-do="uncrop">Reset crop</button>`;
      html += `<button data-do="split">Split…</button>`;
    }
    html += `</div>`;
    if (splitting === p.id) html += splitControls(p);
  }
  html += `<div class="hint">Arrow keys nudge (Shift = 10px). [ and ] move one
           step in the stack, Ctrl+Shift+[ / ] go all the way. H hides,
           Delete removes. Ctrl+Z undo.</div>`;
  host.innerHTML = html;
  wireProps(host, p);
}

function wireProps(host, p) {
  host.querySelectorAll("[data-split]").forEach((inp) => {
    inp.onchange = () => {
      const k = inp.dataset.split;
      if (k === "mode") splitMode = inp.value;
      if (k === "cols") splitCols = Math.max(1, Number(inp.value) || 1);
      if (k === "rows") splitRows = Math.max(1, Number(inp.value) || 1);
      if (k === "want") splitWant = Math.max(2, Number(inp.value) || 2);
      drawProps();
    };
  });

  host.querySelectorAll("[data-on]").forEach((inp) => {
    const commit = (live) => {
      const k = inp.dataset.on;
      if (!live) snapshot();
      if (k === "lock") p.lock_aspect = inp.checked;
      else if (k === "color") p.params.color = hexToRgb(inp.value);
      else if (k === "opacity") p.opacity = Number(inp.value);
      else if (["line_h", "align", "feather", "focus_x", "focus_y", "zoom", "fit"]
                 .includes(k))
        p.params[k] = (k === "align" || k === "fit") ? inp.value : Number(inp.value);
      else p[k] = Number(inp.value);
      clampInside(p);
      syncBox(p);
      if (!live) { drawProps(); markDirty(); }
      else if (inp.nextElementSibling) {
        const shown = k === "zoom" ? (p.params.zoom ?? 1) : (p.opacity ?? 1);
        inp.nextElementSibling.textContent = `${Math.round(shown * 100)}%`;
      }
    };
    if (inp.dataset.live) {
      // The zoom slider re-renders imagery server-side, so it is rate-limited
      // while dragging and takes the true tile once the value settles.
      const heavy = inp.dataset.on === "zoom";
      inp.oninput = () => { if (heavy) beginFraming(); commit(true); };
      inp.onchange = () => {
        if (heavy) { endFraming(); syncBox(p); }
        markDirty();
      };
      inp.onpointerdown = () => snapshot();
    } else {
      inp.onchange = () => commit(false);
    }
  });

  host.querySelectorAll("[data-do]").forEach((b) => {
    b.onclick = () => {
      const a = b.dataset.do;
      if (a === "crop") return startCrop(p);
      if (a === "cropApply") return endCrop(true);
      if (a === "cropCancel") return endCrop(false);
      if (a === "uncrop") return resetCrop(p);
      if (a === "backdrop") return toggleBackdrop(p);
      if (a === "split") { splitting = p.id; return drawProps(); }
      if (a === "splitCancel") { splitting = null; return drawProps(); }
      if (a === "splitGo") return runSplit(p);
      const moves = { toFront: "front", toBack: "back",
                      forward: "forward", backward: "backward" };
      if (moves[a]) return stack(p, moves[a]);   // takes its own snapshot
      snapshot();
      if (a === "bgWhole") {
        Object.assign(p.params, { fit: "contain", zoom: 1, focus_x: 0.5, focus_y: 0.5 });
      }
      if (a === "bgFill") {
        Object.assign(p.params, { fit: "cover", zoom: 1 });
      }
      if (a === "centerH") p.x = (plan.width - p.w) / 2;
      if (a === "centerV") p.y = (plan.height - p.h) / 2;
      if (a === "fullW") { p.x = 0; p.w = plan.width; }
      if (a === "top") p.y = 0;
      if (a === "bottom") p.y = plan.height - p.h;
      if (a === "hide") p.visible = !p.visible;
      clampInside(p);
      syncBox(p); drawLayers(); drawProps(); markDirty();
    };
  });
}

/* ---------------------------------------------------------------- split -- */
/* A part is another placement of the same element carrying a different crop, so
   the parts start exactly where the whole was — splitting changes nothing on
   screen until you move a piece. That is what makes it safe to try. */
let splitting = null;

function splitControls(p) {
  const m = splitMode;
  return `<div class="divider"></div>
    <div class="prop"><label>split</label>
      <select data-split="mode">
        ${[["auto", "where it divides"], ["x", "into columns"],
           ["y", "into rows"], ["grid", "even grid"]].map(([v, t]) =>
          `<option value="${v}" ${m === v ? "selected" : ""}>${t}</option>`).join("")}
      </select></div>
    ${m === "grid"
      ? `<div class="prop"><label>columns</label>
           <input type="number" min="1" max="12" value="${splitCols}" data-split="cols"></div>
         <div class="prop"><label>rows</label>
           <input type="number" min="1" max="12" value="${splitRows}" data-split="rows"></div>`
      : `<div class="prop"><label>at most</label>
           <input type="number" min="2" max="12" value="${splitWant}" data-split="want"></div>`}
    <div class="hint">${m === "grid"
      ? "Cuts an even grid — use this when the artwork has no gaps to find."
      : "Finds the gaps in the artwork itself and cuts there."}
      The parts land where the whole was; move them afterwards.</div>
    <div class="rowbtns">
      <button class="primary" data-do="splitGo">Split into parts</button>
      <button data-do="splitCancel">Cancel</button>
    </div>`;
}

let splitMode = "auto", splitCols = 2, splitRows = 1, splitWant = 4;

async function runSplit(p) {
  const body = { element: p.element, crop: p.params.crop || null, mode: splitMode };
  if (splitMode === "grid") { body.cols = splitCols; body.rows = splitRows; }
  else body.parts = splitWant;

  let rects;
  try {
    rects = (await api("/api/split", jsonReq("POST", body))).rects;
  } catch (e) { return toast(e.message, true); }

  if (rects.length < 2) {
    return toast("no natural divisions found — try 'even grid' instead", true);
  }

  snapshot();
  const base = { x: p.x, y: p.y, w: p.w, h: p.h };
  const existing = p.params.crop || null;
  const made = [];
  rects.forEach((r, i) => {
    const [l, t, rr, b] = r;
    const part = JSON.parse(JSON.stringify(p));
    part.id = `${p.id}~${i + 1}`;
    part.params.crop = composeCrop(existing, r);
    // Tile the original box exactly, so the composite is unchanged at the moment
    // of splitting and the pieces are already in the right relationship.
    part.x = base.x + l * base.w;
    part.y = base.y + t * base.h;
    part.w = Math.max(MIN, (rr - l) * base.w);
    part.h = Math.max(MIN, (b - t) * base.h);
    part.z = p.z + i;
    made.push(part);
  });

  const rest = byZ().filter((q) => q.id !== p.id);
  const at = rest.findIndex((q) => q.z > p.z);
  rest.splice(at < 0 ? rest.length : at, 0, ...made);
  restackTo(rest);                                  // repaints and saves

  splitting = null;
  selIds.clear();
  for (const q of made) selIds.add(q.id);
  selId = made[made.length - 1].id;
  rebuild();
  toast(`split into ${made.length} parts — they are all selected, so you can `
        + `arrange them together`);
}

/* A part is expressed against what the box shows; the stored crop is against
   the whole image. Splitting something already cropped composes the two. */
function composeCrop(existing, part) {
  const [l, t, r, b] = part;
  if (!existing) return [l, t, r, b];
  const [L, T, R, B] = existing;
  return [L + l * (R - L), T + t * (B - T), L + r * (R - L), T + b * (B - T)];
}

/* Put the master's own background imagery directly behind one element, as its
   own placement — so it can then be moved, faded or feathered independently. */
function toggleBackdrop(p) {
  snapshot();
  const id = bgIdFor(p);
  if (hasBackdrop(p)) {
    plan.placements = plan.placements.filter((q) => q.id !== id);
    boxes.get(id)?.remove();
    boxes.delete(id);
  } else {
    const at = p.z;                       // slot the backdrop directly beneath
    for (const q of plan.placements) if (q.z >= at) q.z += 1;
    plan.placements.push({
      id, kind: "photo_band", x: p.x, y: p.y, w: p.w, h: p.h, z: at,
      element: null, uid: "", name: "", role: "", visible: true, lock_aspect: false,
      opacity: 1, params: { feather: 0, focus_x: 0.5 },
    });
  }
  normaliseZ();
  rebuild();
  markDirty();
}

function normaliseZ() {
  byZ().forEach((p, i) => { p.z = i; });
}

const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.substr(i, 2), 16));

/* ------------------------------------------------- copy to other sizes -- */
/* Fixing a master the algorithm read badly is a lot of work, and repeating it
   per format is both the same work again and a good way to end up with three
   banners that do not match. Rescaling happens server-side (pipeline.rescale_plan)
   so re-wrapped text is genuinely re-measured at the new column width rather
   than having its height scaled arithmetically. */
function openCopy() {
  const here = family(info);
  const others = manifest.formats.filter((f) => f.name !== FMT);
  if (!others.length) return toast("there is no other size to copy to", true);

  $("copyList").innerHTML = others.map((f) => {
    const same = family(f) === here;
    return `<label class="copyrow">
      <input type="checkbox" value="${f.name}" ${same ? "checked" : ""}>
      <b>${f.name}</b>
      <span class="lrole">${same ? "same shape" : family(f)}</span>
      <span class="spacer"></span>
      ${f.edited ? '<span class="badge edited">has edits</span>' : ""}
    </label>`;
  }).join("");
  $("copyNote").textContent =
    `Sizes of the same shape are pre-selected. The layout is rescaled to each `
    + `target, then becomes that size's own — they can differ afterwards.`;
  $("copyBox").hidden = false;
}

const closeCopy = () => { $("copyBox").hidden = true; };

async function runCopy() {
  const targets = [...$("copyList").querySelectorAll("input:checked")]
    .map((i) => i.value);
  if (!targets.length) return toast("pick at least one size", true);

  // Anything already hand-edited is only overwritten on an explicit yes — the
  // server skips them otherwise, so a mis-click cannot destroy the other size.
  const edited = manifest.formats
    .filter((f) => targets.includes(f.name) && f.edited).map((f) => f.name);
  const overwrite = !edited.length || confirm(
    `${edited.join(", ")} already ${edited.length > 1 ? "have" : "has"} manual `
    + `edits. Replace ${edited.length > 1 ? "them" : "it"} with this layout?`);

  if (!(await save())) return;                  // copy what is actually stored
  try {
    const r = await api(`/api/plan/${FMT}/copy-to`,
                        jsonReq("POST", { targets, overwrite }));
    manifest = r.manifest;
    closeCopy();
    const parts = [];
    if (r.written.length) parts.push(`copied to ${r.written.join(", ")}`);
    if (r.skipped.length) parts.push(`kept the edits in ${r.skipped.join(", ")}`);
    toast(parts.join("; ") || "nothing to do");
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------- canvas resize -- */
/* Changing the canvas rescales the whole layout onto a new size — the same
   operation as copying to another format, so it goes through the same code. */
async function resizeCanvas(nw, nh) {
  if (nw === plan.width && nh === plan.height) return;
  const name = `${nw}x${nh}`;
  const existing = manifest.formats.find((f) => f.name === name);
  if (existing && existing.edited
      && !confirm(`${name} already has a manual layout. Replace it?`)) return;
  if (!(await save())) return;
  try {
    await api("/api/formats", jsonReq("POST", { width: nw, height: nh }));
    await api(`/api/plan/${FMT}/copy-to`,
              jsonReq("POST", { targets: [name], overwrite: true }));
    location.href = `/edit/${name}`;
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------- persist/export -- */
function markDirty() {
  $("editedBadge").hidden = false;
  autosave();
}

function autosave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 1500);          // Save forces it immediately
}

async function save() {
  clearTimeout(saveTimer);
  try {
    const d = await api(`/api/plan/${FMT}`, jsonReq("PUT", plan));
    showWarnings(d.warnings);
    toast("layout saved");
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

async function renderBlob() {
  let res;
  try {
    res = await fetch(`/api/render/${FMT}.png`, jsonReq("POST", plan));
  } catch (e) {
    // fetch() reports a dropped or timed-out connection as a bare TypeError, so
    // say what that actually means rather than surfacing "Failed to fetch".
    throw new Error("no response from the server — it may still be rendering. "
                    + "Wait a moment and try again.");
  }
  if (!res.ok) throw new Error(await reason(res, "render failed"));
  return res.blob();
}

$("save").onclick = save;
$("undo").onclick = undo;
$("redo").onclick = redo;
$("zoom").oninput = (e) => setZoom(Number(e.target.value));
$("resize").onclick = () => resizeCanvas(Number($("cw").value), Number($("ch").value));
$("copyTo").onclick = () => ($("copyBox").hidden ? openCopy() : closeCopy());
$("copyGo").onclick = runCopy;
$("copyCancel").onclick = closeCopy;

$("reset").onclick = async () => {
  if (!confirm(`Discard manual changes to ${FMT} and go back to the algorithm?`)) return;
  await loadPlan(await api(`/api/plan/${FMT}/reset`, { method: "POST" }));
  toast("reset to algorithm output");
};

$("explode").onclick = async () => {
  snapshot();
  const d = await api(`/api/plan/${FMT}/explode`, { method: "POST" });
  plan = d.plan;
  showWarnings(d.warnings);
  rebuild();
  markDirty();
  toast("exploded into per-element boxes");
};

$("compare").onclick = async () => {
  const box = $("compareBox");
  try {
    $("compareImg").src = URL.createObjectURL(await renderBlob());
    $("compareImg").style.width = `${plan.width * Math.min(zoom, 2)}px`;
    box.hidden = false;
  } catch (e) { toast(e.message, true); }
};

/* Save first, then let the browser fetch the attachment itself. Building the
   file in JS meant a slow render surfaced as "Failed to fetch" and lost the
   download; handing the URL to the browser gives it the usual retry, progress
   and resume behaviour instead. */
$("export").onclick = async () => {
  if (!(await save())) return;
  location.href = `/api/render/${FMT}.png?download=1&rev=${Date.now()}`;
  toast(`downloading ${FMT}.png`);
};

/* ------------------------------------------------------------ keyboard -- */
const BRACKET = { "]": "up", "}": "up", BracketRight: "up",
                  "[": "down", "{": "down", BracketLeft: "down" };

document.addEventListener("keydown", (e) => {
  // Before the input guard: the picker is full of checkboxes, and Escape should
  // close it whichever one has focus.
  if (e.key === "Escape" && !$("copyBox").hidden) {
    e.preventDefault();
    return closeCopy();
  }
  if (e.target.matches("input, select, textarea")) return;
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key.toLowerCase() === "z") {
    e.preventDefault(); return e.shiftKey ? redo() : undo();
  }
  if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); return redo(); }
  if (ctrl && e.key.toLowerCase() === "s") { e.preventDefault(); return save(); }
  if (cropping) {
    if (e.key === "Enter") { e.preventDefault(); endCrop(true); }
    if (e.key === "Escape") { e.preventDefault(); endCrop(false); }
    return;
  }
  if (ctrl && e.key.toLowerCase() === "a") {       // select everything
    e.preventDefault();
    selIds.clear();
    for (const q of plan.placements) selIds.add(q.id);
    selId = byZ().at(-1)?.id ?? null;
    for (const q of plan.placements) syncBox(q);
    drawGroupFrame(); drawLayers(); drawProps();
    return;
  }

  const list = selected();
  if (!list.length) return;
  const stepPx = e.shiftKey ? 10 : 1;
  const moves = { ArrowLeft: [-stepPx, 0], ArrowRight: [stepPx, 0],
                  ArrowUp: [0, -stepPx], ArrowDown: [0, stepPx] };
  if (moves[e.key]) {
    e.preventDefault();
    snapshot();
    const [mx, my] = moves[e.key];
    if (list.length > 1) {
      // Nudge the group as a block — clamping each box on its own would close
      // the gaps between them once the group meets an edge.
      const g = groupBox(list);
      const ox = Math.max(-g.x, Math.min(mx, plan.width - g.w - g.x));
      const oy = Math.max(-g.y, Math.min(my, plan.height - g.h - g.y));
      for (const q of list) { q.x += ox; q.y += oy; }
    } else {
      list[0].x += mx;
      list[0].y += my;
      clampInside(list[0]);
    }
    list.forEach(syncBox);
    drawGroupFrame(); drawProps(); markDirty();
    return;
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    snapshot();
    plan.placements = plan.placements.filter((q) => !selIds.has(q.id));
    normaliseZ();
    select(null);
    rebuild();
    markDirty();
    toast(`removed ${list.length} layer${list.length > 1 ? "s" : ""} — Ctrl+Z undoes it`);
    return;
  }

  const p = primary() || list[0];
  if (e.key === "Escape") {
    select(null);
  } else if (BRACKET[e.key] || BRACKET[e.code]) {
    // Ctrl+Shift goes all the way; bare [ / ] move one step. Matched on e.code
    // as well because Shift turns "[" into "{" on most layouts.
    e.preventDefault();
    const up = (BRACKET[e.key] || BRACKET[e.code]) === "up";
    const far = ctrl && e.shiftKey;
    if (list.length > 1) groupOp(far || up ? "front" : "back", list);
    else stack(p, up ? (far ? "front" : "forward") : (far ? "back" : "backward"));
  } else if (e.key.toLowerCase() === "h") {
    e.preventDefault();                          // hide/show, was Delete's job
    snapshot();
    const to = list.some((q) => !q.visible);
    for (const q of list) q.visible = to;
    list.forEach(syncBox);
    drawLayers(); drawProps(); markDirty();
  }
});

boot();
