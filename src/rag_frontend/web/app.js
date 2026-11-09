const form = document.querySelector("#query-form");
const queryInput = document.querySelector("#query");
const statusLine = document.querySelector("#status");

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitQuery();
});

async function submitQuery() {
  const query = queryInput.value.trim();
  setStatus("");
  if (!query) {
    setStatus("Napiš dotaz.");
    return;
  }
  clearTrace();
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
        renderStructuredAnswer(event.answer || {});
      } else if (event.step === "error") {
        appendTrace(`error ${event.error || ""}`);
      } else {
        appendTrace(formatTrace(event));
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

function formatTrace(event) {
  const copy = { ...event };
  delete copy.step;
  const clock = event.t_ms != null ? `${event.t_ms} ms` : "";
  const duration = event.duration_ms != null ? ` trvání ${event.duration_ms} ms` : "";
  return `${clock} ${event.step}${duration} ${JSON.stringify(copy)}`.trim();
}

function appendTrace(line) {
  const node = document.querySelector("#trace");
  node.textContent += `${line}\n`;
  node.scrollTop = node.scrollHeight;
}

function clearTrace() {
  document.querySelector("#trace").textContent = "";
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
