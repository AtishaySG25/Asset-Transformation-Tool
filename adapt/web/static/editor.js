/* Per-format layout editor.

   The plan (see adapt/layout.py) is the single source of truth: every gesture
   mutates a placement's box, the browser previews it with server-rendered tiles,
   and Export posts the same plan back to be rasterised by the pipeline's own
   renderer. Nothing about the layout is re-implemented here. */

const $ = (id) => document.getElementById(id);
const FMT = document.body.dataset.fmt;
const MIN = 4;                       // smallest allowed box, in output px

let manifest = null, plan = null, info = null;
let zoom = 1, selId = null, saveTimer = null;
const boxes = new Map();             // placement id -> DOM node

const sel = () => plan.placements.find((p) => p.id === selId) || null;
const byZ = () => [...plan.placements].sort((a, b) => a.z - b.z);
const isText = (p) => p.kind === "element" && p.params.mode === "reflow";

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
  $("strategy").textContent = info.strategy;
  buildTabs();
  await loadPlan();
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
  boxes.forEach((n) => n.remove());
  boxes.clear();
  selId = null;
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
  el.classList.toggle("hidden", !p.visible);
  el.classList.toggle("sel", p.id === selId);
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
  if (p.id === selId) addHandles(el);
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
function addHandles(el) {
  if (el.querySelector(".handle")) return;
  for (const d of DIRS) {
    const h = document.createElement("div");
    h.className = "handle";
    h.dataset.dir = d;
    h.style.cursor = `${d}-resize`;
    const x = d.includes("w") ? "0%" : d.includes("e") ? "100%" : "50%";
    const y = d.includes("n") ? "0%" : d.includes("s") ? "100%" : "50%";
    h.style.left = x; h.style.top = y;
    h.style.transform = "translate(-50%, -50%)";
    el.appendChild(h);
  }
}

/* ---------------------------------------------------------- selection -- */
function select(id) {
  selId = id;
  for (const p of plan.placements) syncBox(p);
  drawLayers();
  drawProps();
}

/* ------------------------------------------------------- drag & resize -- */
let drag = null;

$("stage").addEventListener("pointerdown", (e) => {
  const handleEl = e.target.closest(".handle");
  const boxEl = e.target.closest(".box");
  if (!boxEl) { select(null); return; }
  const p = plan.placements.find((x) => x.id === boxEl.dataset.id);
  if (!p) return;
  if (!handleEl) select(p.id);
  drag = {
    p, dir: handleEl ? handleEl.dataset.dir : null,
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
  drag.dir ? applyResize(drag, dx, dy, e.shiftKey) : applyMove(drag, dx, dy);
  syncBox(drag.p);
  drawProps();
});

function endDrag(e) {
  if (!drag) return;
  clearGuides();
  const p = drag.p;
  drag = null;
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
  if (axis === "v") {
    g.style.cssText = `left:${at * zoom}px;top:0;width:1px;height:100%`;
  } else {
    g.style.cssText = `top:${at * zoom}px;left:0;height:1px;width:100%`;
  }
  $("stage").appendChild(g);
}
const clearGuides = () => $("stage").querySelectorAll(".guide").forEach((g) => g.remove());

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

function drawProps() {
  const host = $("props");
  const p = sel();
  if (!p) {
    host.innerHTML = '<div class="hint">Select an element on the canvas.</div>';
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
           <select data-on="align">${["left", "center", "right"].map((a) =>
             `<option ${p.params.align === a ? "selected" : ""}>${a}</option>`).join("")}
         </select></div>`
    : num("height", p.h, "h");
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
    </div>
    <div class="hint">Arrow keys nudge (Shift = 10px). [ and ] restack.</div>`;
  host.innerHTML = html;

  host.querySelectorAll("[data-on]").forEach((inp) => {
    inp.onchange = () => {
      const k = inp.dataset.on;
      if (k === "lock") p.lock_aspect = inp.checked;
      else if (k === "color") p.params.color = hexToRgb(inp.value);
      else if (["line_h", "align", "feather", "focus_y"].includes(k))
        p.params[k] = k === "align" ? inp.value : Number(inp.value);
      else p[k] = Number(inp.value);
      clampInside(p);
      syncBox(p);
      markDirty();
    };
  });
  host.querySelectorAll("[data-do]").forEach((b) => {
    b.onclick = () => {
      const a = b.dataset.do;
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

const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.substr(i, 2), 16));

/* ------------------------------------------------------- persist/export -- */
function markDirty() {
  $("editedBadge").hidden = false;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 1500);          // autosave; Save forces it now
}

async function save() {
  clearTimeout(saveTimer);
  try {
    await api(`/api/plan/${FMT}`, jsonReq("PUT", plan));
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
$("zoom").oninput = (e) => setZoom(Number(e.target.value));

$("reset").onclick = async () => {
  if (!confirm(`Discard manual changes to ${FMT} and go back to the algorithm?`)) return;
  await loadPlan(await api(`/api/plan/${FMT}/reset`, { method: "POST" }));
  toast("reset to algorithm output");
};

$("explode").onclick = async () => {
  await loadPlan(await api(`/api/plan/${FMT}/explode`, { method: "POST" }));
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
  if (e.key === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); return save(); }
  const p = sel();
  if (!p) return;
  const step = e.shiftKey ? 10 : 1;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                  ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (moves[e.key]) {
    e.preventDefault();
    p.x += moves[e.key][0];
    p.y += moves[e.key][1];
    clampInside(p); syncBox(p); drawProps(); markDirty();
  } else if (e.key === "Escape") {
    select(null);
  } else if (e.key === "[" || e.key === "]") {
    const order = byZ();
    const i = order.indexOf(p);
    const j = e.key === "]" ? i + 1 : i - 1;
    if (j >= 0 && j < order.length) {
      [order[i].z, order[j].z] = [order[j].z, order[i].z];
      paint(); markDirty();
    }
  } else if (e.key === "Delete" || e.key === "Backspace") {
    p.visible = !p.visible;
    syncBox(p); drawLayers(); drawProps(); markDirty();
  }
});

boot();
