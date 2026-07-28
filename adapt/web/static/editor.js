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
let undoStack = [], redoStack = [], cropping = null;
const boxes = new Map();             // placement id -> DOM node

const sel = () => plan.placements.find((p) => p.id === selId) || null;
const byZ = () => [...plan.placements].sort((a, b) => a.z - b.z);
const isText = (p) => p.kind === "element" && p.params.mode === "reflow";
const canCrop = (p) => p.kind === "element" && p.params.mode !== "reflow";

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
  if (selId && !plan.placements.some((p) => p.id === selId)) selId = null;
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
  el.classList.toggle("sel", p.id === selId && !cropping);
  el.classList.toggle("outline", p.id !== selId);
  el.style.background = p.kind === "color_bar" ? cssColor(p.params.color) : "";
  el.style.justifyContent = { center: "center", right: "flex-end" }[p.params.align]
                            || "flex-start";

  const img = el.querySelector("img");
  const url = tileURL(p);
  if (!url) {
    img.hidden = true;
  } else {
    img.hidden = false;
    if (img.dataset.url !== url) {
      img.dataset.url = url;
      img.onload = () => onTileLoaded(p, img);
      img.src = url;
    }
    sizeTileImg(p, img);
  }
  if (p.id === selId && !cropping) addHandles(el);
  else el.querySelectorAll(".handle").forEach((h) => h.remove());
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
function select(id) {
  selId = id;
  for (const p of plan.placements) syncBox(p);
  drawLayers();
  drawProps();
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
  const boxEl = e.target.closest(".box");
  if (!boxEl) { select(null); return; }
  const p = plan.placements.find((x) => x.id === boxEl.dataset.id);
  if (!p) return;
  if (!handleEl) select(p.id);
  drag = {
    p, dir: handleEl ? handleEl.dataset.dir : null, moved: false,
    sx: e.clientX, sy: e.clientY,
    o: { x: p.x, y: p.y, w: p.w, h: p.h, line_h: p.params.line_h || 0 },
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
  drag.dir ? applyResize(drag, dx, dy, e.shiftKey) : applyMove(drag, dx, dy);
  syncBox(drag.p);
  drawProps();
});

function endDrag() {
  if (!drag) return;
  clearGuides();
  const { p, moved } = drag;
  drag = null;
  if (!moved) return;
  if (isText(p)) syncBox(p);           // re-fetch the wrap at the new width
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
  const src = `/api/element/${p.element}.png`;
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
    row.className = `layer${p.id === selId ? " sel" : ""}${p.visible ? "" : " hidden"}`;
    row.draggable = true;
    row.dataset.id = p.id;
    row.innerHTML = `<span class="grip">⠿</span>
      <span class="lname">${label(p)}</span>
      <span class="lrole">${p.kind === "element" ? p.role : p.kind.replace("_", " ")}</span>
      <button class="eye" title="show / hide">${p.visible ? "◉" : "○"}</button>`;
    row.onclick = (e) => {
      if (e.target.classList.contains("eye")) {
        snapshot();
        p.visible = !p.visible;
        syncBox(p); drawLayers(); markDirty();
      } else select(p.id);
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
  rest.forEach((p, i) => { p.z = i; });
  plan.placements = rest;
  paint();
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
  const p = sel();
  if (!p) {
    host.innerHTML = '<div class="hint">Select an element on the canvas.</div>';
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
  if (p.kind === "photo_band") {
    html += num("feather", p.params.feather ?? 0, "feather", 0.01);
    html += num("focus y", p.params.focus_y ?? 0.5, "focus_y", 0.01);
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

  if (p.kind === "element") {
    html += `<div class="divider"></div><div class="rowbtns">
      <button data-do="backdrop">${hasBackdrop(p) ? "Remove backdrop" : "Background behind"}</button>`;
    if (canCrop(p)) {
      html += `<button data-do="crop">Crop…</button>`;
      if (p.params.crop) html += `<button data-do="uncrop">Reset crop</button>`;
    }
    html += `</div>`;
  }
  html += `<div class="hint">Arrow keys nudge (Shift = 10px). [ and ] restack.
           Ctrl+Z undo.</div>`;
  host.innerHTML = html;
  wireProps(host, p);
}

function wireProps(host, p) {
  host.querySelectorAll("[data-on]").forEach((inp) => {
    const commit = (live) => {
      const k = inp.dataset.on;
      if (!live) snapshot();
      if (k === "lock") p.lock_aspect = inp.checked;
      else if (k === "color") p.params.color = hexToRgb(inp.value);
      else if (k === "opacity") p.opacity = Number(inp.value);
      else if (["line_h", "align", "feather", "focus_y"].includes(k))
        p.params[k] = k === "align" ? inp.value : Number(inp.value);
      else p[k] = Number(inp.value);
      clampInside(p);
      syncBox(p);
      if (!live) { drawProps(); markDirty(); }
      else if (inp.nextElementSibling)
        inp.nextElementSibling.textContent = `${Math.round(p.opacity * 100)}%`;
    };
    if (inp.dataset.live) {
      inp.oninput = () => commit(true);
      inp.onchange = () => { markDirty(); };
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
      snapshot();
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
      element: null, name: "", role: "", visible: true, lock_aspect: false,
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

/* ------------------------------------------------------- canvas resize -- */
/* Changing the canvas rescales the whole layout: positions follow each axis,
   aspect-locked graphics scale uniformly so they are never distorted, and type
   size follows the smaller axis so text stays in proportion. */
async function resizeCanvas(nw, nh) {
  if (nw === plan.width && nh === plan.height) return;
  const sx = nw / plan.width, sy = nh / plan.height, s = Math.min(sx, sy);
  snapshot();
  for (const p of plan.placements) {
    p.x *= sx; p.y *= sy;
    if (isText(p)) {
      p.w *= sx; p.h *= sy;
      p.params.line_h = Math.max(3, Math.round((p.params.line_h || 12) * s));
    } else if (p.lock_aspect) {
      p.w *= s; p.h *= s;
    } else {
      p.w *= sx; p.h *= sy;
    }
  }
  plan.width = nw; plan.height = nh;
  plan.placements.forEach(clampInside);

  try {
    await api("/api/formats", jsonReq("POST", { width: nw, height: nh }));
    await api(`/api/plan/${nw}x${nh}`, jsonReq("PUT", plan));
    location.href = `/edit/${nw}x${nh}`;
  } catch (e) {
    undo();
    toast(e.message, true);
  }
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
  } catch (e) { toast(e.message, true); }
}

async function renderBlob(download) {
  const res = await fetch(`/api/render/${FMT}.png${download ? "?download=1" : ""}`,
                          jsonReq("POST", plan));
  if (!res.ok) throw new Error(`render failed (${res.status})`);
  return res.blob();
}

$("save").onclick = save;
$("undo").onclick = undo;
$("redo").onclick = redo;
$("zoom").oninput = (e) => setZoom(Number(e.target.value));
$("resize").onclick = () => resizeCanvas(Number($("cw").value), Number($("ch").value));

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
    $("compareImg").src = URL.createObjectURL(await renderBlob(false));
    $("compareImg").style.width = `${plan.width * Math.min(zoom, 2)}px`;
    box.hidden = false;
  } catch (e) { toast(e.message, true); }
};

$("export").onclick = async () => {
  try {
    const url = URL.createObjectURL(await renderBlob(true));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${FMT}.png`;
    a.click();
    URL.revokeObjectURL(url);
    toast(`exported ${FMT}.png`);
  } catch (e) { toast(e.message, true); }
};

/* ------------------------------------------------------------ keyboard -- */
document.addEventListener("keydown", (e) => {
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
  const p = sel();
  if (!p) return;
  const stepPx = e.shiftKey ? 10 : 1;
  const moves = { ArrowLeft: [-stepPx, 0], ArrowRight: [stepPx, 0],
                  ArrowUp: [0, -stepPx], ArrowDown: [0, stepPx] };
  if (moves[e.key]) {
    e.preventDefault();
    snapshot();
    p.x += moves[e.key][0];
    p.y += moves[e.key][1];
    clampInside(p); syncBox(p); drawProps(); markDirty();
  } else if (e.key === "Escape") {
    select(null);
  } else if (e.key === "[" || e.key === "]") {
    snapshot();
    const order = byZ();
    const i = order.indexOf(p);
    const j = e.key === "]" ? i + 1 : i - 1;
    if (j >= 0 && j < order.length) {
      [order[i].z, order[j].z] = [order[j].z, order[i].z];
      paint(); markDirty();
    }
  } else if (e.key === "Delete" || e.key === "Backspace") {
    snapshot();
    p.visible = !p.visible;
    syncBox(p); drawLayers(); drawProps(); markDirty();
  }
});

boot();
