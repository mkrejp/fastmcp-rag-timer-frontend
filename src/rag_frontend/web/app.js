const form = document.querySelector("#query-form");
const contextForm = document.querySelector("#context-form");
const queryInput = document.querySelector("#query");
const statusLine = document.querySelector("#status");
const CONTEXT_SECTION_LABELS = {
  catalog: "Katalog",
  lifecycle: "Životní cyklus",
  geo: "Geo",
  versions: "Verze",
  locality_links: "Lokality",
  related_documents: "Související dokumenty",
  neighbor_chunks: "Sousední chunky",
};

document.querySelectorAll(".mode-btn").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitQuery();
});

contextForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const submitter = event.submitter;
  const action = submitter?.value || "vector";
  void submitContextSearch(action);
});

document.querySelector("#close-doc-context").addEventListener("click", () => {
  const panel = document.querySelector("#document-context");
  panel.hidden = true;
  document.querySelector("#document-context-body").replaceChildren();
});

function setMode(mode) {
  document.querySelectorAll(".mode-btn").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll("[data-panel]").forEach((node) => {
    node.hidden = node.dataset.panel !== mode;
  });
  setStatus("");
}

async function submitQuery() {
  const query = queryInput.value.trim();
  setStatus("");
  if (!query) {
    setStatus("Napiš dotaz.");
    return;
  }
  clearTrace();
  appendTrace(`${stamp()} → dotaz odeslán`, "run");
  const response = await fetch("/api/answer/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, use_llm: true }),
  });
  if (response.status === 401) {
    window.location.href = "/auth/login";
    return;
  }
  if (!response.ok || !response.body) {
    const body = await readJson(response);
    setStatus(body.detail || "Dotaz se nepovedl.");
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) {
      break;
    }
    buffer += decoder.decode(chunk.value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data: "));
      if (!line) {
        continue;
      }
      const event = JSON.parse(line.slice(6));
      if (event.step === "done") {
        settleRunning();
        renderStructuredAnswer(event.answer || {});
      } else {
        appendEvent(event);
      }
    }
  }
}

function renderStructuredAnswer(body) {
  const box = document.querySelector("#answer");
  const note = document.querySelector("#answer-note");
  const figures = document.querySelector("#figures");
  const sources = document.querySelector("#sources-body");
  const qa = document.querySelector("#trace");
  box.hidden = false;
  box.innerHTML = renderMarkdown(body.answer_markdown || "");
  const debug = body.debug || {};
  if (!debug.llm_called) {
    note.hidden = false;
    note.textContent = `Odpověď je bez modelu. ${reasonText(debug.reason)}`;
  } else {
    note.hidden = true;
    note.textContent = "";
  }
  figures.replaceChildren();
  for (const image of body.images || []) {
    const figure = document.createElement("figure");
    figure.append(imageSlot(image.image_id, image.caption || "Obrázek"));
    const caption = document.createElement("figcaption");
    caption.textContent = image.caption || image.doc_id || "";
    figure.append(caption);
    figures.append(figure);
  }
  drawMap(body.objects || []);
  sources.replaceChildren();
  const items = [...(body.objects || []), ...(body.noise || [])];
  let previews = 0;
  items.forEach((item, index) => {
    if (item.page_image_id && previews < 5 && isRelevant(item)) {
      previews += 1;
      sources.append(pageSource(item, index));
      return;
    }
    sources.append(textSource(item, index));
  });
  appendTrace(
    [
      "souhrn",
      `llm=${debug.llm_called ? debug.model : "ne"}`,
      `tokeny=${debug.prompt_tokens || 0}/${debug.completion_tokens || 0}`,
      `důvod=${debug.reason || ""}`,
    ].join(" "),
  );
}

function isRelevant(item) {
  return item.label !== "noise" && item.label !== "homonym";
}

function textSource(item, index) {
  const row = document.createElement("p");
  row.textContent = `[${index + 1}] ${item.filename || item.title || "zdroj"} ${
    item.doc_id || ""
  } ${item.label || ""}`.trim();
  return row;
}

function pageSource(item, index) {
  const figure = document.createElement("figure");
  figure.className = "page-preview";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "page-frame";
  const img = document.createElement("img");
  img.className = "shot page-shot";
  img.alt = "";
  img.loading = "lazy";
  const marks = document.createElement("span");
  marks.className = "page-highlights";
  marks.append(...highlightMarks(item.highlights));
  button.append(img, marks);
  button.addEventListener("click", () => openLightbox(item, img));
  const caption = document.createElement("figcaption");
  const pageNo = item.page_one || pageNumberFromId(item.page_image_id);
  caption.textContent = `[${index + 1}] ${item.filename || item.title || "dokument"}, strana ${pageNo}`;
  figure.append(button, caption);
  queueMicrotask(() => loadWhenVisible(img, item.page_image_id));
  return figure;
}

function highlightMarks(boxes) {
  return (boxes || []).map((box) => {
    const mark = document.createElement("span");
    mark.className = "page-hit";
    const x0 = Number(box.x0) || 0;
    const y0 = Number(box.y0) || 0;
    const x1 = Number(box.x1) || 0;
    const y1 = Number(box.y1) || 0;
    mark.style.left = `${x0 * 100}%`;
    mark.style.top = `${y0 * 100}%`;
    mark.style.width = `${Math.max(0, x1 - x0) * 100}%`;
    mark.style.height = `${Math.max(0, y1 - y0) * 100}%`;
    return mark;
  });
}

function pageNumberFromId(pageImageId) {
  const match = /^page:[0-9a-f-]{36}:([1-9]\d{0,4})$/i.exec(pageImageId || "");
  return match ? match[1] : "";
}

function openLightbox(item, sourceImage) {
  const dialog = document.querySelector("#page-lightbox");
  const img = document.querySelector("#lightbox-image");
  const marks = document.querySelector("#lightbox-marks");
  const caption = document.querySelector("#lightbox-caption");
  img.src = sourceImage.currentSrc || sourceImage.src || "";
  img.alt = sourceImage.alt || "";
  marks.replaceChildren(...highlightMarks(item.highlights));
  caption.textContent = `${item.filename || item.title || "dokument"}, strana ${
    item.page_one || pageNumberFromId(item.page_image_id)
  }`;
  if (typeof dialog.showModal === "function" && !dialog.open) {
    dialog.showModal();
  }
}

function loadWhenVisible(img, imageId) {
  const start = () => {
    const started = performance.now();
    void loadImage(imageId).then((result) => {
      const elapsed = Math.round(performance.now() - started);
      appendTrace(`page image ${imageId} trvání ${elapsed} ms`);
      if (result.url) {
        img.src = result.url;
        return;
      }
      const problem = document.createElement("p");
      problem.className = "note";
      problem.textContent = result.error || "Náhled stránky není k dispozici.";
      img.replaceWith(problem);
    });
  };
  if (typeof IntersectionObserver !== "function") {
    start();
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) {
      observer.disconnect();
      start();
    }
  });
  observer.observe(img);
}

function reasonText(reason) {
  if (reason === "žádný relevantní hit") {
    return "Server nenašel relevantní záznam, model se nevolal.";
  }
  if (reason === "chybí klíč") {
    return "Chybí klíč k modelu.";
  }
  if (reason === "režim bez LLM") {
    return "Syntéza modelem je vypnutá.";
  }
  if (reason) {
    return reason;
  }
  return "Model se nevolal.";
}

function renderMarkdown(markdown) {
  const escaped = escapeHtml(markdown);
  const linked = escaped.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" rel="noopener noreferrer">$1</a>',
  );
  const bold = linked.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return bold
    .split(/\n\n+/)
    .map((paragraph) => `<p>${paragraph.replace(/\n/g, "<br>")}</p>`)
    .join("");
}

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function drawMap(objects) {
  const mapNode = document.querySelector("#map");
  const points = objects.filter((item) => Array.isArray(item.gps));
  if (!points.length || typeof L === "undefined") {
    mapNode.hidden = true;
    return;
  }
  mapNode.hidden = false;
  if (mapNode._map) {
    mapNode._map.remove();
  }
  const map = L.map(mapNode).setView(points[0].gps, 13);
  mapNode._map = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  for (const item of points) {
    L.circleMarker(item.gps, {
      radius: 8,
      color: item.gps_kind === "centroid" ? "#8a3b12" : "#0f6e6a",
    })
      .addTo(map)
      .bindPopup(item.title || item.filename || "");
  }
}

function imageSlot(imageId, alt) {
  const frame = document.createElement("div");
  const img = document.createElement("img");
  img.className = "shot";
  img.alt = alt;
  frame.append(img);
  void loadImage(imageId).then((result) => {
    if (result.url) {
      img.src = result.url;
      return;
    }
    const problem = document.createElement("p");
    problem.className = "note";
    problem.textContent = result.error || "Obrázek není k dispozici.";
    frame.append(problem);
  });
  return frame;
}

async function loadImage(imageId) {
  const response = await fetch(`/api/images/${encodeURIComponent(imageId)}`);
  if (!response.ok) {
    const body = await readJson(response);
    return { error: body.detail || "Obrázek není k dispozici." };
  }
  const blob = await response.blob();
  return { url: URL.createObjectURL(blob) };
}

function appendEvent(event) {
  const view = formatTrace(event);
  if (view.settle) {
    const running = [
      ...document.querySelectorAll(`#trace .run[data-kind="${view.kind}"]`),
    ];
    const match =
      running.find((node) => node.dataset.tool === (view.tool || "")) || running[0];
    if (match) {
      match.classList.remove("run");
    }
  }
  appendTrace(`${stamp(event.t_ms)} ${view.text}`, view.kind, view.run, view.tool);
}

function formatTrace(event) {
  if (event.step === "mcp" && event.phase === "start") {
    return {
      kind: "mcp",
      run: true,
      tool: event.tool || "",
      text: `→ ${event.tool}(${formatArgs(event.arguments)})`,
    };
  }
  if (event.step === "mcp" && event.phase === "end") {
    const status = event.error ? "chyba" : `${event.hits ?? 0} hitů`;
    return {
      kind: event.error ? "error" : "mcp",
      settle: true,
      tool: event.tool || "",
      text: `← ${event.tool}: ${status}, ${event.duration_ms ?? 0} ms`,
    };
  }
  if (event.step === "llm" && event.phase === "start") {
    return {
      kind: "llm",
      run: true,
      tool: "llm",
      text: `→ llm(${event.model || "model"})`,
    };
  }
  if (event.step === "llm" && event.phase === "end") {
    const status = event.called
      ? `${event.prompt_tokens || 0} tokenů`
      : event.reason || event.error || "bez modelu";
    return {
      kind: event.error ? "error" : "llm",
      settle: true,
      tool: "llm",
      text: `← llm: ${status}, ${event.duration_ms ?? 0} ms`,
    };
  }
  if (event.step === "error" || event.error) {
    return { kind: "error", text: `chyba ${event.error || event.step}` };
  }
  if (event.step === "status") {
    return { kind: "", run: true, text: "→ dotaz přijat" };
  }
  return { kind: "", text: event.step || "" };
}

function formatArgs(value) {
  if (!value || typeof value !== "object") {
    return "";
  }
  const text = JSON.stringify(value);
  return text.length > 120 ? `${text.slice(0, 117)}...` : text;
}

function stamp(tMs) {
  const clock = new Intl.DateTimeFormat("cs-CZ", {
    timeZone: "Europe/Prague",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date());
  return tMs == null ? clock : `${clock} +${tMs} ms`;
}

function appendTrace(line, kind, running, tool) {
  const node = document.querySelector("#trace");
  const row = document.createElement("p");
  row.className = `trace-line${kind ? ` ${kind}` : ""}${running ? " run" : ""}`;
  if (kind) {
    row.dataset.kind = kind;
  }
  if (tool) {
    row.dataset.tool = tool;
  }
  row.textContent = line;
  node.append(row);
  node.scrollTop = node.scrollHeight;
}

function settleRunning() {
  document.querySelectorAll("#trace .run").forEach((node) => {
    node.classList.remove("run");
  });
}

function clearTrace() {
  document.querySelector("#trace").replaceChildren();
}

document.querySelector("#copy-trace").addEventListener("click", () => {
  const text = document.querySelector("#trace").textContent || "";
  if (navigator.clipboard) {
    void navigator.clipboard.writeText(text);
  }
});

function setStatus(text) {
  statusLine.textContent = text;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_error) {
    return {};
  }
}

function contextFlags() {
  const flags = {};
  contextForm.querySelectorAll(".section-flags input[type=checkbox]").forEach((box) => {
    flags[box.name] = box.checked;
  });
  return flags;
}

async function submitContextSearch(action) {
  const query = document.querySelector("#context-query").value.trim();
  const limit = Number(document.querySelector("#context-limit").value) || 3;
  setStatus("");
  document.querySelector("#context-note").hidden = true;
  document.querySelector("#context-hits").replaceChildren();
  document.querySelector("#document-context").hidden = true;
  if (action === "vector" && !query) {
    setStatus("Napiš dotaz pro vektorové hledání.");
    return;
  }
  const response =
    action === "catalog"
      ? await fetch("/api/catalog/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query,
            limit: Math.min(Math.max(limit, 1), 100),
            include_context: document.querySelector("#catalog-wrap").checked,
          }),
        })
      : await fetch("/api/search/context", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query,
            limit: Math.min(Math.max(limit, 1), 20),
            ...contextFlags(),
          }),
        });
  if (response.status === 401) {
    window.location.href = "/auth/login";
    return;
  }
  const body = await readJson(response);
  if (!response.ok) {
    setStatus(body.detail || "Hledání se nepovedlo.");
    return;
  }
  renderContextHits(body.hits || [], body.message || null);
}

function renderContextHits(hits, message) {
  const root = document.querySelector("#context-hits");
  const note = document.querySelector("#context-note");
  root.replaceChildren();
  if (message) {
    note.hidden = false;
    note.textContent = message;
  } else {
    note.hidden = true;
    note.textContent = "";
  }
  if (!hits.length && !message) {
    note.hidden = false;
    note.textContent = "Nic se nenašlo.";
    return;
  }
  hits.forEach((hit, index) => {
    root.append(contextHitCard(hit, index));
  });
}

function contextHitCard(hit, index) {
  const article = document.createElement("article");
  article.className = "context-hit";
  const heading = document.createElement("h3");
  const catalog = hit.catalog || {};
  const title =
    catalog.filename ||
    hit.parent_document ||
    hit.doc_id ||
    `Hit ${index + 1}`;
  heading.textContent = title;
  article.append(heading);
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = [
    hit.source_type,
    hit.doc_id,
    hit.chunk_index != null ? `chunk ${hit.chunk_index}` : "",
    hit.page_number != null ? `strana ${hit.page_number}` : "",
    hit.score != null ? `score ${Number(hit.score).toFixed(3)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  article.append(meta);
  if (hit.content) {
    const content = document.createElement("p");
    content.className = "content";
    content.textContent = hit.content;
    article.append(content);
  }
  if (hit.doc_id) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "link";
    open.textContent = "Načíst kontext dokumentu";
    open.addEventListener("click", () => {
      void loadDocumentContext(hit.doc_id, hit.chunk_index);
    });
    article.append(open);
  }
  for (const key of Object.keys(CONTEXT_SECTION_LABELS)) {
    if (!(key in hit) || hit[key] == null) {
      continue;
    }
    article.append(contextSection(key, hit[key]));
  }
  return article;
}

function contextSection(key, value) {
  const details = document.createElement("details");
  details.className = "context-section";
  details.open = key === "catalog" || key === "neighbor_chunks";
  const summary = document.createElement("summary");
  summary.textContent = sectionSummary(key, value);
  details.append(summary);
  const body = document.createElement("div");
  body.className = "context-section-body";
  body.append(renderContextValue(key, value));
  details.append(body);
  return details;
}

function sectionSummary(key, value) {
  const label = CONTEXT_SECTION_LABELS[key] || key;
  if (key === "locality_links" && value && typeof value === "object") {
    return `${label} (${value.returned ?? (value.links || []).length})`;
  }
  if (key === "related_documents" && Array.isArray(value)) {
    return `${label} (${value.length})`;
  }
  if (key === "neighbor_chunks" && Array.isArray(value)) {
    return `${label} (${value.length})`;
  }
  if (key === "versions" && value && typeof value === "object") {
    return `${label} (${value.count ?? (value.versions || []).length})`;
  }
  return label;
}

function renderContextValue(key, value) {
  if (key === "neighbor_chunks" && Array.isArray(value)) {
    const list = document.createElement("div");
    value.forEach((chunk) => {
      const row = document.createElement("p");
      row.className = "neighbor";
      const head = document.createElement("span");
      head.className = "meta";
      head.textContent = `chunk ${chunk.chunk_index}${
        chunk.page != null ? `, strana ${chunk.page}` : ""
      }`;
      const text = document.createElement("span");
      text.className = "content";
      text.textContent = chunk.text || "";
      row.append(head, document.createElement("br"), text);
      list.append(row);
    });
    return list;
  }
  if (key === "locality_links" && value && typeof value === "object") {
    const list = document.createElement("ul");
    list.className = "locality-list";
    for (const link of value.links || []) {
      const item = document.createElement("li");
      item.textContent = [
        link.name || link.obec_id,
        link.cast_obce_name || link.cast_obce,
        link.matched_text,
        link.verification_status,
      ]
        .filter(Boolean)
        .join(" · ");
      list.append(item);
    }
    if (!list.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "quiet";
      empty.textContent = "Žádné lokality.";
      return empty;
    }
    return list;
  }
  if (key === "related_documents" && Array.isArray(value)) {
    const list = document.createElement("ul");
    value.forEach((doc) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "link";
      button.textContent =
        doc.filename || doc.catalog?.filename || doc.doc_id || "dokument";
      if (doc.doc_id) {
        button.addEventListener("click", () => {
          void loadDocumentContext(doc.doc_id);
        });
      }
      item.append(button);
      list.append(item);
    });
    if (!list.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "quiet";
      empty.textContent = "Žádné související dokumenty.";
      return empty;
    }
    return list;
  }
  const pre = document.createElement("pre");
  pre.className = "context-json";
  pre.textContent = JSON.stringify(value, null, 2);
  return pre;
}

async function loadDocumentContext(docId, chunkIndex) {
  const params = new URLSearchParams(contextFlags());
  if (chunkIndex != null && chunkIndex !== "") {
    params.set("chunk_index", String(chunkIndex));
  }
  const response = await fetch(
    `/api/documents/${encodeURIComponent(docId)}/context?${params}`,
  );
  if (response.status === 401) {
    window.location.href = "/auth/login";
    return;
  }
  const body = await readJson(response);
  if (!response.ok) {
    setStatus(body.detail || "Kontext dokumentu se nepovedl načíst.");
    return;
  }
  const panel = document.querySelector("#document-context");
  const root = document.querySelector("#document-context-body");
  root.replaceChildren();
  const context = body.context || {};
  const title = document.createElement("p");
  title.className = "meta";
  title.textContent = context.doc_id || docId;
  root.append(title);
  for (const key of Object.keys(CONTEXT_SECTION_LABELS)) {
    if (!(key in context) || context[key] == null) {
      continue;
    }
    root.append(contextSection(key, context[key]));
  }
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
