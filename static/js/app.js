(() => {
  "use strict";

  // ------------------------------------------------------------- elements
  const uploadScreen = document.getElementById("uploadScreen");
  const editorScreen = document.getElementById("editorScreen");
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");
  const chooseFileBtn = document.getElementById("chooseFileBtn");
  const uploadError = document.getElementById("uploadError");

  const pageNav = document.getElementById("pageNav");
  const rightTools = document.getElementById("rightTools");
  const prevPageBtn = document.getElementById("prevPage");
  const nextPageBtn = document.getElementById("nextPage");
  const pageInput = document.getElementById("pageInput");
  const pageCountEl = document.getElementById("pageCount");
  const zoomInBtn = document.getElementById("zoomIn");
  const zoomOutBtn = document.getElementById("zoomOut");
  const zoomIndicator = document.getElementById("zoomIndicator");

  const addTextBtn = document.getElementById("addTextBtn");
  const undoBtn = document.getElementById("undoBtn");
  const resetBtn = document.getElementById("resetBtn");
  const saveBtn = document.getElementById("saveBtn");

  const thumbRail = document.getElementById("thumbRail");
  const pageStage = document.getElementById("pageStage");
  const pageFrame = document.getElementById("pageFrame");
  const pageImage = document.getElementById("pageImage");
  const overlayLayer = document.getElementById("overlayLayer");
  const pageLoading = document.getElementById("pageLoading");
  const toast = document.getElementById("toast");

  // --------------------------------------------------------------- state
  let fileId = null;
  let pageCount = 0;
  let pageSizes = [];       // [{width,height}] in pt
  let deletedPages = new Set();
  let currentPage = 1;
  let zoom = 1;             // pure CSS zoom, image always rendered at RENDER_SCALE
  const RENDER_SCALE = 2.0; // px per pt baked into the PNG (crispness)
  const BASE_WIDTH = 760;   // display px at zoom=1 (roughly a comfortable reading width)
  let addTextMode = false;
  let activeEditBox = null; // the <input>/<div> currently being edited

  const uid = () => "add-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

  function showToast(msg, isError) {
    toast.textContent = msg;
    toast.className = "toast" + (isError ? " error" : "");
    toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => (toast.hidden = true), 2600);
  }

  // ------------------------------------------------------------- upload
  ["dragenter", "dragover"].forEach(ev =>
    dropZone.addEventListener(ev, e => { e.preventDefault(); dropZone.classList.add("drag"); })
  );
  ["dragleave", "drop"].forEach(ev =>
    dropZone.addEventListener(ev, e => { e.preventDefault(); dropZone.classList.remove("drag"); })
  );
  dropZone.addEventListener("drop", e => {
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });
  chooseFileBtn.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("click", (e) => {
    if (e.target === chooseFileBtn) return;
    fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) uploadFile(fileInput.files[0]);
  });

  async function uploadFile(file) {
    uploadError.hidden = true;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      uploadError.textContent = "Please choose a .pdf file.";
      uploadError.hidden = false;
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    chooseFileBtn.textContent = "Uploading…";
    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");

      fileId = data.file_id;
      pageCount = data.page_count;
      pageSizes = data.pages;
      deletedPages = new Set();
      currentPage = 1;
      zoom = 1;

      uploadScreen.hidden = true;
      editorScreen.hidden = false;
      pageNav.hidden = false;
      rightTools.hidden = false;
      pageCountEl.textContent = pageCount;
      pageInput.max = pageCount;

      buildThumbRail();
      updateZoomLabel();
      loadPage(1);
    } catch (err) {
      uploadError.textContent = err.message || "Something went wrong reading that PDF.";
      uploadError.hidden = false;
    } finally {
      chooseFileBtn.textContent = "Choose a PDF";
    }
  }

  // --------------------------------------------------------- thumb rail
  function buildThumbRail() {
    thumbRail.innerHTML = "";
    for (let i = 1; i <= pageCount; i++) {
      const thumb = document.createElement("div");
      thumb.className = "thumb"
        + (i === currentPage ? " active" : "")
        + (deletedPages.has(i) ? " deleted" : "");
      thumb.dataset.page = i;

      const img = document.createElement("img");
      img.src = `/api/render/${fileId}/${i}?scale=0.35&t=${Date.now()}`;
      img.loading = "lazy";

      const num = document.createElement("span");
      num.className = "thumb-num";
      num.textContent = i;

      const del = document.createElement("button");
      del.className = "thumb-del";
      del.title = "Delete page";
      del.textContent = "×";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        togglePageDeleted(i);
      });

      thumb.append(img, num, del);
      thumb.addEventListener("click", () => goToPage(i));
      thumbRail.appendChild(thumb);
    }
  }

  function refreshThumb(pageNum) {
    const thumb = thumbRail.querySelector(`.thumb[data-page="${pageNum}"]`);
    if (!thumb) return;
    thumb.classList.toggle("deleted", deletedPages.has(pageNum));
    const img = thumb.querySelector("img");
    img.src = `/api/render/${fileId}/${pageNum}?scale=0.35&t=${Date.now()}`;
  }

  function highlightActiveThumb() {
    thumbRail.querySelectorAll(".thumb").forEach(t =>
      t.classList.toggle("active", Number(t.dataset.page) === currentPage)
    );
  }

  async function togglePageDeleted(pageNum) {
    const willDelete = !deletedPages.has(pageNum);
    const res = await fetch(`/api/page/${fileId}/${pageNum}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deleted: willDelete }),
    });
    const data = await res.json();
    if (data.deleted) deletedPages.add(pageNum); else deletedPages.delete(pageNum);
    refreshThumb(pageNum);
    if (pageNum === currentPage) loadPage(pageNum, true);
  }

  // ------------------------------------------------------------- paging
  function goToPage(n) {
    n = Math.max(1, Math.min(pageCount, n));
    if (n === currentPage) return;
    currentPage = n;
    pageInput.value = n;
    highlightActiveThumb();
    loadPage(n);
  }
  prevPageBtn.addEventListener("click", () => goToPage(currentPage - 1));
  nextPageBtn.addEventListener("click", () => goToPage(currentPage + 1));
  pageInput.addEventListener("change", () => goToPage(Number(pageInput.value) || 1));

  // -------------------------------------------------------------- zoom
  function applyZoom() {
    pageFrame.style.width = (BASE_WIDTH * zoom) + "px";
    updateZoomLabel();
    positionOverlays();
  }
  function updateZoomLabel() { zoomIndicator.textContent = Math.round(zoom * 100) + "%"; }
  zoomInBtn.addEventListener("click", () => { zoom = Math.min(2.2, zoom + 0.15); applyZoom(); });
  zoomOutBtn.addEventListener("click", () => { zoom = Math.max(0.5, zoom - 0.15); applyZoom(); });

  // -------------------------------------------------------- page loading
  let currentSpans = [];

  async function loadPage(n, skipSpinner) {
    currentPage = n;
    pageInput.value = n;
    cancelActiveEdit(false);
    if (!skipSpinner) pageLoading.hidden = false;
    highlightActiveThumb();

    const dims = pageSizes[n - 1];
    pageFrame.style.width = (BASE_WIDTH * zoom) + "px";

    const imgUrl = `/api/render/${fileId}/${n}?scale=${RENDER_SCALE}&t=${Date.now()}`;
    const spansPromise = fetch(`/api/spans/${fileId}/${n}`).then(r => r.json());

    await new Promise((resolve) => {
      pageImage.onload = resolve;
      pageImage.onerror = resolve;
      pageImage.src = imgUrl;
    });

    const spanData = await spansPromise;
    currentSpans = spanData.spans || [];
    pageLoading.hidden = true;
    renderOverlays(dims);
  }

  window.addEventListener("resize", () => positionOverlays());

  // ----------------------------------------------------------- overlays
  function scaleFactor() {
    const dims = pageSizes[currentPage - 1];
    return pageImage.clientWidth / dims.width;
  }

  function renderOverlays(dims) {
    overlayLayer.innerHTML = "";
    const scale = scaleFactor();
    currentSpans.forEach(span => overlayLayer.appendChild(makeSpanEl(span, scale)));

    if (addTextMode) attachAddTextHandler();
  }

  function positionOverlays() {
    if (!currentSpans.length) return;
    const scale = scaleFactor();
    [...overlayLayer.querySelectorAll(".text-span")].forEach(el => {
      const span = currentSpans.find(s => s.id === el.dataset.id);
      if (span) applyBoxStyle(el, span.bbox, scale);
    });
  }

  function applyBoxStyle(el, bbox, scale) {
    const [x0, top, x1, bottom] = bbox;
    el.style.left = (x0 * scale) + "px";
    el.style.top = (top * scale) + "px";
    el.style.width = Math.max((x1 - x0) * scale, 14) + "px";
    el.style.height = Math.max((bottom - top) * scale, 10) + "px";
  }

  function makeSpanEl(span, scale) {
    const el = document.createElement("div");
    el.className = "text-span";
    el.dataset.id = span.id;
    applyBoxStyle(el, span.bbox, scale);

    const del = document.createElement("button");
    del.className = "del-btn";
    del.textContent = "×";
    del.title = "Delete this text";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      submitEdit({ page: currentPage, id: span.id, type: "delete", bbox: span.bbox, text: "" });
    });
    el.appendChild(del);

    el.addEventListener("click", (e) => {
      if (e.target === del) return;
      startEditing(span, el);
    });

    return el;
  }

  // ------------------------------------------------------------- editing
  function startEditing(span, el) {
    cancelActiveEdit(false);
    const scale = scaleFactor();
    const box = document.createElement("input");
    box.type = "text";
    box.className = "text-edit-box";
    box.value = span.text;
    const [x0, top, x1] = span.bbox;
    box.style.left = (x0 * scale) + "px";
    box.style.top = (top * scale - 2) + "px";
    box.style.fontSize = Math.max(span.size * scale, 9) + "px";
    box.style.width = Math.max((x1 - x0) * scale + 40, 60) + "px";
    box.style.height = (span.size * scale * 1.35) + "px";
    box.style.color = colorCss(span.color);

    overlayLayer.appendChild(box);
    el.style.visibility = "hidden";
    box.focus();
    box.select();

    activeEditBox = { el: box, span, sourceEl: el };

    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commitActiveEdit(); }
      if (e.key === "Escape") { e.preventDefault(); cancelActiveEdit(true); }
    });
    box.addEventListener("blur", () => commitActiveEdit());
  }

  function colorCss(rgb) {
    if (!rgb) return "#000";
    const [r, g, b] = rgb;
    return `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`;
  }

  function commitActiveEdit() {
    if (!activeEditBox) return;
    const { el, span, sourceEl } = activeEditBox;
    const newText = el.value;
    activeEditBox = null;
    el.remove();
    if (sourceEl) sourceEl.style.visibility = "";

    if (newText === span.text) return; // unchanged, nothing to do
    submitEdit({
      page: currentPage,
      id: span.id,
      type: newText.trim() ? "replace" : "delete",
      bbox: span.bbox,
      text: newText,
      font: span.font,
      size: span.size,
      color: span.color,
    });
  }

  function cancelActiveEdit(restoreFocusless) {
    if (!activeEditBox) return;
    const { el, sourceEl } = activeEditBox;
    activeEditBox = null;
    el.remove();
    if (sourceEl) sourceEl.style.visibility = "";
  }

  async function submitEdit(payload) {
    pageLoading.hidden = false;
    try {
      await fetch(`/api/edit/${fileId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await loadPage(currentPage, true);
      refreshThumb(currentPage);
    } catch (err) {
      showToast("Couldn't save that edit", true);
    } finally {
      pageLoading.hidden = true;
    }
  }

  // ---------------------------------------------------------- add text
  addTextBtn.addEventListener("click", () => {
    addTextMode = !addTextMode;
    addTextBtn.classList.toggle("active", addTextMode);
    if (addTextMode) attachAddTextHandler(); else detachAddTextHandler();
  });

  function onCanvasClickForAdd(e) {
    if (e.target !== overlayLayer && e.target !== pageImage) return;
    const rect = overlayLayer.getBoundingClientRect();
    const scale = scaleFactor();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const size = 13;
    const x0 = px / scale;
    const bottom = py / scale + size * 0.9;

    const box = document.createElement("input");
    box.type = "text";
    box.className = "text-edit-box";
    box.placeholder = "Type text…";
    box.style.left = px + "px";
    box.style.top = (py - 3) + "px";
    box.style.fontSize = (size * scale) + "px";
    box.style.width = "160px";
    box.style.height = (size * scale * 1.35) + "px";
    box.style.color = "#000";
    overlayLayer.appendChild(box);
    box.focus();

    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      const text = box.value;
      box.remove();
      if (text && text.trim()) {
        submitEdit({
          page: currentPage,
          id: uid(),
          type: "add",
          bbox: [x0, py / scale, x0 + Math.max(text.length * size * 0.55, 40), bottom],
          text,
          font: "Helvetica",
          size,
          color: [0, 0, 0],
        });
      }
      addTextMode = false;
      addTextBtn.classList.remove("active");
      detachAddTextHandler();
    };
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); finish(); }
      if (ev.key === "Escape") { ev.preventDefault(); box.value = ""; finish(); }
    });
    box.addEventListener("blur", finish);
  }
  function attachAddTextHandler() { overlayLayer.addEventListener("click", onCanvasClickForAdd); }
  function detachAddTextHandler() { overlayLayer.removeEventListener("click", onCanvasClickForAdd); }

  // -------------------------------------------------------- undo/reset
  async function resyncStateAndReload() {
    const res = await fetch(`/api/state/${fileId}`);
    const data = await res.json();
    deletedPages = new Set(data.deleted_pages);
    await loadPage(currentPage, true);
    buildThumbRail();
  }

  undoBtn.addEventListener("click", async () => {
    const res = await fetch(`/api/undo/${fileId}`, { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      await resyncStateAndReload();
      showToast("Undid last change");
    } else {
      showToast(data.message || "Nothing to undo", true);
    }
  });

  resetBtn.addEventListener("click", async () => {
    if (!confirm("Discard all changes made to this document?")) return;
    await fetch(`/api/reset/${fileId}`, { method: "POST" });
    await resyncStateAndReload();
    showToast("All changes discarded");
  });

  // -------------------------------------------------------------- save
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    const orig = saveBtn.textContent;
    saveBtn.textContent = "Saving…";
    try {
      const res = await fetch(`/api/save/${fileId}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Save failed");
      const a = document.createElement("a");
      a.href = data.download_url;
      a.download = data.filename || "edited.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      showToast("Saved — download starting");
    } catch (err) {
      showToast(err.message || "Couldn't save the document", true);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = orig;
    }
  });

  // ------------------------------------------------------- keyboard nav
  document.addEventListener("keydown", (e) => {
    if (activeEditBox) return;
    if (uploadScreen.hidden === false) return;
    if (e.key === "ArrowRight") goToPage(currentPage + 1);
    if (e.key === "ArrowLeft") goToPage(currentPage - 1);
  });

})();
