/* Gallery: pick a master asset, see all six formats, jump into one to edit. */

const $ = (id) => document.getElementById(id);
let manifest = null;

async function refreshSources() {
  const { sources, current } = await api("/api/sources");
  const sel = $("sources");
  sel.innerHTML = "";
  if (!sources.length) {
    sel.appendChild(new Option("no assets in input/", ""));
  }
  for (const s of sources) sel.appendChild(new Option(s, s));
  if (current) sel.value = current;
  return current;
}

function card(f, rev) {
  const el = document.createElement("div");
  el.className = "card";
  el.innerHTML = `
    <div class="card-head">
      <b>${f.name}</b>
      <span class="badge">${f.strategy}</span>
      ${f.edited ? '<span class="badge edited">edited</span>' : ""}
      <span class="spacer"></span>
      <a href="/edit/${f.name}"><button>Edit layout</button></a>
    </div>
    <div class="shot"><img src="/api/render/${f.name}.png?rev=${rev}"
         width="${f.width}" height="${f.height}" alt="${f.name}"></div>`;
  return el;
}

function draw() {
  const g = $("gallery");
  g.innerHTML = "";
  $("asset").innerHTML = `<b>${manifest.name}</b> — ${manifest.width}x${manifest.height},
                          ${manifest.elements.length} elements`;
  for (const f of manifest.formats) g.appendChild(card(f, manifest.rev));
}

async function open(path) {
  if (!path) return;
  $("gallery").innerHTML = '<div class="empty">extracting layers…</div>';
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
