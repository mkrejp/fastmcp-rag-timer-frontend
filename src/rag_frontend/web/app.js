const form = document.querySelector("#query-form");
const queryInput = document.querySelector("#query");
const limitInput = document.querySelector("#limit");
const imagesInput = document.querySelector("#include-images");
const statusLine = document.querySelector("#status");
const transcript = document.querySelector("#transcript");
const catalog = document.querySelector("#catalog");
const catalogNote = document.querySelector("#catalog-note");

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitQuery();
});

void loadCatalog();

async function submitQuery() {
  const query = queryInput.value.trim();
  setStatus("");
  if (!query) {
    setStatus("Enter a question.");
    return;
  }
  appendQuestion(query);
  const response = await fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      use_llm: document.querySelector("#use-llm").checked,
    }),
  });
  const body = await readJson(response);
  if (response.status === 401) {
    window.location.href = "/auth/login";
    return;
  }
  if (!response.ok) {
    setStatus(body.detail || "Search failed.");
    return;
  }
  renderStructuredAnswer(body);
}

async function loadCatalog() {
  const response = await fetch("/api/catalog");
  const body = await readJson(response);
  catalog.replaceChildren();
  if (response.status === 401) {
    window.location.href = "/auth/login";
    return;
  }
  if (!response.ok) {
    catalogNote.textContent = body.detail || "Catalog unavailable.";
    return;
  }
  catalogNote.textContent = body.message || "";
  for (const document of body.documents || []) {
    catalog.append(renderDocument(document));
  }
}

function appendQuestion(query) {
  const item = document.createElement("li");
  item.className = "turn question";
  item.append(label("You"), paragraph(query));
  transcript.append(item);
}

function appendAnswer(body) {
  const item = document.createElement("li");
  item.className = "turn answer";
  item.append(label("Datastore"));
  if (body.message) {
    item.append(note(body.message));
  }
  for (const passage of body.passages || []) {
    item.append(renderPassage(passage));
  }
  if ((body.image_passages || []).length || body.image_message) {
    item.append(label("Pictures"));
    if (body.image_message) {
      item.append(note(body.image_message));
    }
    for (const passage of body.image_passages || []) {
      item.append(renderPassage(passage));
    }
  }
  if (body.raw) {
    item.append(note(body.raw));
  }
  transcript.append(item);
}

function renderPassage(passage) {
  const block = document.createElement("article");
  block.className = "passage";
  const bits = [passage.source, passage.source_type].filter(Boolean);
  if (passage.page_number) {
    bits.push(`page ${passage.page_number}`);
  }
  block.append(meta(bits.join(" · ")));
  if (passage.content) {
    block.append(paragraph(passage.content, "content"));
  }
  if (passage.image_id) {
    block.append(imageSlot(passage.image_id, passage.source || "Stored picture"));
  }
  if (passage.doc_id) {
    block.append(openButton(passage.doc_id, passage.source || passage.doc_id));
  }
  return block;
}

function renderDocument(documentRow) {
  const item = document.createElement("li");
  item.className = "doc-row";
  const name = documentRow.filename || documentRow.doc_id || "Document";
  if (documentRow.thumbnail_image_id) {
    item.append(
      imageSlot(documentRow.thumbnail_image_id, name, "shot thumb"),
    );
  }
  const text = document.createElement("div");
  const bits = [documentRow.status, documentRow.uploaded_at].filter(Boolean);
  if (documentRow.chunk_count != null) {
    bits.push(`${documentRow.chunk_count} chunks`);
  }
  text.append(paragraph(name, "content"));
  if (bits.length) {
    text.append(meta(bits.join(" · ")));
  }
  if (documentRow.doc_id) {
    text.append(openButton(documentRow.doc_id, name));
  }
  item.append(text);
  return item;
}

function openButton(docId, name) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "link";
  button.textContent = "Open document";
  button.addEventListener("click", () => {
    void openDocument(docId, name);
  });
  return button;
}

async function openDocument(docId, name) {
  setStatus("");
  const [textResponse, imageResponse] = await Promise.all([
    fetch(`/api/documents/${encodeURIComponent(docId)}/text`),
    fetch(`/api/documents/${encodeURIComponent(docId)}/images`),
  ]);
  const textBody = await readJson(textResponse);
  const imageBody = await readJson(imageResponse);
  if (!textResponse.ok && !imageResponse.ok) {
    setStatus(textBody.detail || imageBody.detail || "Document unavailable.");
    return;
  }
  const item = document.createElement("li");
  item.className = "turn answer";
  item.append(label("Document"), paragraph(name, "content"));
  if (textResponse.ok) {
    item.append(paragraph(textBody.text || "", "content"));
  } else if (textBody.detail) {
    item.append(note(textBody.detail));
  }
  if (imageResponse.ok) {
    item.append(await renderGallery(imageBody.images || [], imageBody.message));
  } else if (imageBody.detail) {
    item.append(note(imageBody.detail));
  }
  transcript.append(item);
}

async function renderGallery(images, message) {
  const wrap = document.createElement("div");
  wrap.className = "gallery";
  if (message) {
    wrap.append(note(message));
  }
  for (const image of images) {
    const figure = document.createElement("figure");
    figure.append(imageSlot(image.image_id, image.ocr_text || "Stored picture"));
    const captionBits = [];
    if (image.page_number != null) {
      captionBits.push(`page ${image.page_number}`);
    }
    if (image.width && image.height) {
      captionBits.push(`${image.width}×${image.height}`);
    }
    if (captionBits.length) {
      const caption = document.createElement("figcaption");
      caption.textContent = captionBits.join(" · ");
      figure.append(caption);
    }
    wrap.append(figure);
  }
  return wrap;
}

function imageSlot(imageId, alt, className) {
  const frame = document.createElement("div");
  const img = document.createElement("img");
  img.className = className || "shot";
  img.alt = alt;
  frame.append(img);
  void loadImage(imageId).then((result) => {
    if (result.url) {
      img.src = result.url;
      return;
    }
    frame.append(note(result.error || "Image unavailable."));
  });
  return frame;
}

async function loadImage(imageId) {
  const response = await fetch(`/api/images/${encodeURIComponent(imageId)}`);
  if (!response.ok) {
    const body = await readJson(response);
    return { error: body.detail || "Image unavailable." };
  }
  const blob = await response.blob();
  return { url: URL.createObjectURL(blob) };
}

function label(text) {
  return paragraph(text, "who");
}

function meta(text) {
  return paragraph(text, "meta");
}

function note(text) {
  return paragraph(text, "note");
}

function paragraph(text, className) {
  const node = document.createElement("p");
  if (className) {
    node.className = className;
  }
  node.textContent = text;
  return node;
}

function setStatus(text) {
  statusLine.textContent = text;
}

function renderStructuredAnswer(body) {
  const box = document.querySelector("#answer");
  const qa = document.querySelector("#qa-body");
  box.hidden = false;
  box.replaceChildren();
  box.append(paragraph(body.answer_markdown || "", "content"));
  for (const item of body.objects || []) {
    const card = document.createElement("article");
    card.className = "passage";
    const gps = item.gps ? `GPS ${item.gps.join(", ")} (${item.gps_kind || "bod"})` : "";
    card.append(paragraph(item.title || item.filename || "Záznam", "content"));
    card.append(meta([item.address, item.filename, gps].filter(Boolean).join(" · ")));
    for (const imageId of item.image_ids || []) {
      card.append(imageSlot(imageId, item.title || "Obrázek"));
    }
    box.append(card);
  }
  drawMap(body.objects || []);
  const debug = body.debug || {};
  qa.textContent = [
    `varianty: ${(debug.variants || []).join(" | ")}`,
    `llm: ${debug.llm_called ? debug.model : "ne"}`,
    `tokeny: ${debug.prompt_tokens || 0} in / ${debug.completion_tokens || 0} out`,
    `důvod: ${debug.reason || ""}`,
    `latence: ${JSON.stringify(debug.timings_ms || {})}`,
  ].join("\n");
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
  const map = L.map(mapNode).setView(points[0].gps, 12);
  mapNode._map = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  for (const item of points) {
    L.marker(item.gps).addTo(map).bindPopup(item.title || item.filename || "");
  }
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_error) {
    return {};
  }
}
