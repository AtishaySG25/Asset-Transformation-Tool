/* Gallery: pick a master asset, see every format grouped by shape, jump into one
   to edit. Custom sizes are added here and go through the same layout engine;
   the feasibility warnings come from adapt.pipeline.review. */

const $ = (id) => document.getElementById(id);
let manifest = null;
let actualSize = false;

/* Banners on one side; squares and skyscrapers on the other. Grouped by shape
   rather than by a fixed list, so custom sizes file themselves. */
const GROUPS = [
  { key: "banner", title: "Banners", match: (f) => family(f) === "banner" },
  { key: "block", title: "Squares & skyscrapers", match: (f) => family(f) !== "banner" },
];

async function refreshSources() {
  const { sources, current } = await api("/api/sources");
  const sel = $("sources");
  sel.innerHTML = "";
  if (!sources.length) sel.appendChild(new Option("no assets in input/", ""));
  for (const s of sources) sel.appendChild(new Option(s, s));
  if (current) sel.value = current;
  return current;
}

function card(f) {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.fmt = f.name;
  el.innerHTML = `
    <div class="card-head">
      <b>${f.name}</b>
      <span class="badge">${f.strategy}</span>
      ${f.edited ? '<span class="badge edited">edited</span>' : ""}
      ${f.custom ? '<span class="badge">custom</span>' : ""}
      <span class="spacer"></span>
      ${f.custom ? `<button data-drop="${f.name}" title="remove this size">&times;</button>` : ""}
      <a href="/edit/${f.name}"><button>Edit layout</button></a>
    </div>
    <div class="warnbox" hidden></div>
    <div class="shot">
      <div class="ph">waiting to render…</div>
      <img width="${f.width}" height="${f.height}" alt="${f.name}" hidden>
    </div>`;
  const drop = el.querySelector("[data-drop]");
  if (drop) drop.onclick = () => removeSize(f.name);
  return el;
}

function draw() {
  const g = $("gallery");
  g.innerHTML = "";
  $("asset").innerHTML = `<b>${manifest.name}</b> — ${manifest.width}x${manifest.height},
                          ${manifest.elements.length} elements`;
  initMaster(manifest);

  for (const grp of GROUPS) {
    const formats = manifest.formats.filter(grp.match);
    if (!formats.length) continue;
    const col = document.createElement("section");
    col.className = `group group-${grp.key}`;
    col.innerHTML = `<h3>${grp.title} <span class="lrole">${formats.length}</span></h3>`;
    for (const f of formats) col.appendChild(card(f));
    g.appendChild(col);
  }
  applyScale();
  loadPreviews();
}

/* Previews are requested one at a time. Eight parallel requests for a 24-megapixel
   master is what made them fail to appear: the browser caps connections, the
   server renders them serially anyway, and nothing shows until the last finishes.
   One at a time, each card fills in as soon as it is ready. */
async function loadPreviews() {
  const rev = manifest.rev;
  for (const f of manifest.formats) {
    await loadPreview(f, rev);
  }
  loadWarnings();
}

function loadPreview(f, rev) {
  return new Promise((done) => {
    const card = document.querySelector(`.card[data-fmt="${f.name}"]`);
    if (!card) return done();
    const img = card.querySelector("img");
    const ph = card.querySelector(".ph");
    ph.textContent = "rendering…";
    ph.classList.add("busy");
    img.onload = () => { ph.remove(); img.hidden = false; done(); };
    img.onerror = () => {
      ph.classList.remove("busy");
      ph.innerHTML = `could not render ${f.name} — <button>retry</button>`;
      ph.querySelector("button").onclick = () => loadPreview(f, Date.now());
      done();
    };
    img.src = `/api/render/${f.name}.png?rev=${rev}`;
  });
}

/* Warnings need every plan built, which is the work the previews already
   triggered — so they are fetched last, when it is nearly free. */
async function loadWarnings() {
  let review;
  try { review = await api("/api/review"); } catch (e) { return; }
  for (const [name, problems] of Object.entries(review)) {
    const box = document.querySelector(`.card[data-fmt="${name}"] .warnbox`);
    if (!box || !problems.length) continue;
    box.hidden = false;
    box.innerHTML = `<b>The algorithm struggled at this size.</b>
      <ul>${problems.map((p) => `<li>${p}</li>`).join("")}</ul>
      <a href="/edit/${name}?raw=1"><button>Edit manually (raw layout)</button></a>`;
  }
}

function applyScale() {
  document.body.classList.toggle("actual-size", actualSize);
  $("scale").textContent = actualSize ? "fit" : "1:1";
  $("scale").title = actualSize
    ? "Scale previews to fit their column" : "Show previews at actual pixel size";
}

$("scale").onclick = () => { actualSize = !actualSize; applyScale(); };

async function open(path) {
  if (!path) return;
  $("gallery").innerHTML = '<div class="empty">extracting layers… (a large PSD takes a while — watch the console for progress)</div>';
  try {
    manifest = await api("/api/open", jsonReq("POST", { path }));
    draw();
  } catch (e) {
    $("gallery").innerHTML = `<div class="empty">could not open ${path}</div>`;
    toast(e.message, true);
  }
}

$("open").onclick = () => open($("sources").value);

$("upload").onchange = async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  $("gallery").innerHTML = '<div class="empty">uploading and extracting…</div>';
  try {
    manifest = await api("/api/upload", { method: "POST", body: fd });
    await refreshSources();
    draw();
  } catch (e) { toast(e.message, true); }
};

$("addSize").onclick = async () => {
  if (!manifest) return toast("open an asset first", true);
  const width = Number($("nw").value), height = Number($("nh").value);
  if (!width || !height) return toast("enter a width and a height", true);
  try {
    const r = await api("/api/formats", jsonReq("POST", { width, height }));
    manifest = r.manifest;
    $("nw").value = ""; $("nh").value = "";
    draw();
    toast(`added ${r.format} — laid out by the algorithm`);
  } catch (e) { toast(e.message, true); }
};

async function removeSize(name) {
  if (!confirm(`Remove ${name} and any layout saved for it?`)) return;
  try {
    manifest = await api(`/api/formats/${name}`, { method: "DELETE" });
    draw();
  } catch (e) { toast(e.message, true); }
}

$("export").onclick = async () => {
  if (!manifest) return toast("open an asset first", true);
  try {
    const r = await api("/api/export", { method: "POST" });
    toast(`wrote ${r.written.length} PNGs to output/`);
  } catch (e) { toast(e.message, true); }
};

(async () => {
  const current = await refreshSources();
  if (current) {                       // a session is already loaded server-side
    manifest = await api("/api/manifest");
    draw();
  }
})();
