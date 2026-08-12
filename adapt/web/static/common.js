/* Shared helpers: API calls, toasts, and the placement -> image URL mapping. */

const SS = 2;                       // tiles are fetched at 2x for crisp zooming

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).message || msg; } catch (e) { /* html error */ }
    throw new Error(`${res.status} ${msg}`);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}

const jsonReq = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/* Why a response failed, in words. The server sends {"message": ...} for its own
   errors; anything else (a proxy page, a truncated body) falls back to the code. */
async function reason(res, prefix = "request failed") {
  try {
    const m = (await res.json()).message;
    if (m) return `${prefix} (${res.status}) — ${m}`;
  } catch (e) { /* not JSON */ }
  return `${prefix} (${res.status} ${res.statusText})`;
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.toggle("err", isError);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 5000 : 2200);
}

/* Which master these images belong to. The element / tile / master endpoints are
   cached for an hour and their URLs are otherwise identical for every asset, so
   without this a second PSD opened in the same session is drawn with the first
   one's pictures — the browser never asks again. Set from the manifest before
   any of those URLs is built. */
let SRC_TOKEN = "";
const setSourceToken = (t) => { SRC_TOKEN = t || ""; };
const stamped = (url) =>
  SRC_TOKEN ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(SRC_TOKEN)}`
            : url;

/* The URL that renders a placement. Plain (non-reflowed) elements are served
   as-extracted so the browser can scale them; everything whose pixels depend on
   its box size is rasterised server-side by the same code the exporter uses. */
function tileURL(p) {
  if (p.kind === "color_bar") return null;              // drawn as a CSS colour
  // An uncropped, unwrapped element is served as extracted and scaled by CSS;
  // anything whose pixels depend on the box goes through the tile renderer.
  if (p.kind === "element" && p.params.mode !== "reflow" && !p.params.crop)
    return stamped(`/api/element/${p.element}.png`);
  const q = new URLSearchParams({
    kind: p.kind,
    element: p.element ?? "",
    uid: p.uid || "",
    name: p.name || "",
    w: Math.max(1, Math.round(p.w)),
    h: Math.max(1, Math.round(p.h)),
    ss: SS,
    params: JSON.stringify(p.params || {}),
  });
  return stamped(`/api/tile.png?${q}`);
}

function cssColor(rgb) {
  const [r, g, b] = rgb || [0, 0, 0];
  return `rgb(${r},${g},${b})`;
}

/* The master asset, rasterised, kept beside whatever you are working on. Its
   collapsed state is remembered so it stays out of the way if you don't want it. */
function initMaster(manifest) {
  // Before the early return: every cached image URL needs this, not just the
  // ones on a page that happens to show the master panel.
  setSourceToken(manifest.token);
  const img = document.getElementById("master");
  if (!img) return;
  img.src = stamped("/api/master.png?w=560");
  const link = document.getElementById("masterLink");
  if (link) link.href = stamped("/api/master.png");
  document.getElementById("masterCap").textContent =
    `${manifest.name} — ${manifest.width}x${manifest.height}, click to open full size`;
  const box = document.getElementById("masterBox");
  const btn = document.getElementById("masterToggle");
  const apply = () => {
    const hidden = localStorage.getItem("adapt.master") === "hidden";
    box.hidden = hidden;
    btn.textContent = hidden ? "show" : "hide";
  };
  btn.onclick = () => {
    localStorage.setItem("adapt.master",
      localStorage.getItem("adapt.master") === "hidden" ? "shown" : "hidden");
    apply();
  };
  apply();
}

/* Which family a target belongs to, relative to nothing but its own shape —
   so custom sizes group themselves without a lookup table. */
function family(f) {
  const a = f.width / f.height;
  if (a >= 2.5) return "banner";
  if (a <= 0.7) return "skyscraper";
  return "square";
}

function label(p) {
  if (p.kind === "element") return p.name || p.role || p.id;
  if (p.kind === "base_image")
    return p.params?.src === "background" ? "background image" : "flattened composite";
  return { photo_band: "background imagery", color_bar: "colour bar" }[p.kind] || p.kind;
}
