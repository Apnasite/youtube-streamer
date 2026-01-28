  // --- Streaming links panel ---
  const streamLinksPanel = document.getElementById("streamLinksPanel");
  const streamLinksList = document.getElementById("streamLinksList");
  const currentStreamingVideo = document.getElementById("currentStreamingVideo");

  async function updateStreamLinksPanel() {
    try {
      const resp = await fetch("/api/stream_links");
      const data = await resp.json();
      streamLinksList.innerHTML = "";
      (data.links || []).forEach((link, i) => {
        const li = document.createElement("li");
        li.textContent = link;
        if (data.current && data.ids && data.ids[i] === data.current) {
          li.className = "fw-bold text-success";
        }
        streamLinksList.appendChild(li);
      });
      if (data.current) {
        currentStreamingVideo.innerHTML = `<a href="https://youtube.com/watch?v=${data.current}" target="_blank">${data.current}</a>`;
      } else {
        currentStreamingVideo.textContent = "(none)";
      }
    } catch (e) {
      streamLinksList.innerHTML = "<li class='text-danger'>Error loading streaming list</li>";
      currentStreamingVideo.textContent = "(unknown)";
    }
  }

  // Poll every 5 seconds
  setInterval(updateStreamLinksPanel, 5000);
  window.addEventListener("DOMContentLoaded", updateStreamLinksPanel);
// static/js/script.js
(() => {
  const openListBtn = document.getElementById("openListBtn");
  const listPanel = document.getElementById("listPanel");
  const cardsGrid = document.getElementById("cardsGrid");
  const loadingRow = document.getElementById("loadingRow");
  const pageInfo = document.getElementById("pageInfo");
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");
  const selectAllBtn = document.getElementById("selectAllBtn");
  const clearBtn = document.getElementById("clearBtn");
  const startBtn = document.getElementById("startBtn");
  const messageArea = document.getElementById("messageArea");

  let currentPage = 1;
  let totalCount = 0;
  let pageSize = 20;
  document.getElementById("pageSizeInput").value = "20";

  function showMessage(msg, type = "info") {
    messageArea.innerHTML = `<div class="alert alert-${type}">${msg}</div>`;
  }

  async function fetchPage(page = 1) {
    const channel = document.getElementById("channelInput").value.trim() || "";
    pageSize = parseInt(document.getElementById("pageSizeInput").value || "20", 10) || 20;
    if (!channel) { showMessage("Channel URL required", "warning"); return; }
    loadingRow.style.display = "block";
    cardsGrid.innerHTML = "";
    try {
      const url = new URL("/api/videos", window.location.origin);
      url.searchParams.set("channel_url", channel);
      url.searchParams.set("page", page);
      url.searchParams.set("page_size", pageSize);
      const res = await fetch(url.toString());
      if (!res.ok) throw new Error("Failed to fetch");
      const data = await res.json();
      totalCount = data.total_count || 0;
      renderCards(data.videos || []);
      currentPage = data.page || page;
      pageInfo.textContent = `Page ${currentPage} — ${totalCount} total`;
    } catch (err) {
      showMessage("Error: " + err.message, "danger");
    } finally {
      loadingRow.style.display = "none";
    }
  }

  // --- Modal logic ---
  let videoModal = null;
  function createVideoModal() {
    if (videoModal) return videoModal;
    videoModal = document.createElement("div");
    videoModal.className = "modal fade";
    videoModal.id = "videoModal";
    videoModal.tabIndex = -1;
    videoModal.innerHTML = `
      <div class="modal-dialog modal-lg modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title" id="videoModalTitle"></h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body" id="videoModalBody"></div>
        </div>
      </div>
    `;
    document.body.appendChild(videoModal);
    // Destroy iframe on modal close
    videoModal.addEventListener('hidden.bs.modal', function () {
      const bodyEl = videoModal.querySelector('#videoModalBody');
      if (bodyEl) bodyEl.innerHTML = '';
    });
    return videoModal;
  }

  function showVideoModal(video) {
    const modal = createVideoModal();
    const titleEl = modal.querySelector('#videoModalTitle');
    const bodyEl = modal.querySelector('#videoModalBody');
    titleEl.textContent = video.title;
    bodyEl.innerHTML = `<div class="ratio ratio-16x9"><iframe src="https://www.youtube.com/embed/${video.id}" allowfullscreen frameborder="0"></iframe></div>`;
    // Bootstrap 5 modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
  }

  function renderCards(videos) {
    if (!videos || videos.length === 0) {
      cardsGrid.innerHTML = `<div class="text-muted p-3">No videos on this page.</div>`;
      return;
    }
    cardsGrid.innerHTML = "";
    videos.forEach(v => {
      const card = document.createElement("div");
      card.className = "video-card card shadow-sm mb-2";
      // Info fields
      const views = v.views ? v.views.toLocaleString() : "N/A";
      const duration = v.duration || "N/A";
      const uploadDate = v.upload_date || "Unknown";
      card.innerHTML = `
      <div class="card-img-top ratio ratio-16x9">
        <img src="${v.thumbnail}" class="object-fit-cover" alt="Thumbnail">
      </div>
        <div class="card-body d-flex flex-column">
          <h5 class="card-title mb-2">${escapeHtml(v.title)}</h5>
          <div class="mb-1 small text-muted">
            <span><i class="bi bi-eye"></i> ${views} views</span>
            <span class="ms-3"><i class="bi bi-clock"></i> ${duration}</span>
            <span class="ms-3"><i class="bi bi-calendar"></i> ${uploadDate}</span>
          </div>
          <div class="mt-auto d-flex justify-content-between align-items-center">
            <button type="button" class="btn btn-sm btn-outline-secondary open-modal-btn">Open</button>
            <input type="checkbox" class="form-check-input select-checkbox" data-id="${v.id}" />
          </div>
        </div>
      `;
      // Add click handler for modal open
      card.querySelector('.open-modal-btn').addEventListener('click', () => {
        showVideoModal(v);
      });
      cardsGrid.appendChild(card);
    });
  }

  function escapeHtml(s){
    if(!s) return "";
    return s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
  }

  openListBtn.addEventListener("click", () => {
    listPanel.style.display = "block";
    currentPage = 1;
    fetchPage(1);
  });

  // Auto-fetch and show 20 videos on page load using default channel
  window.addEventListener("DOMContentLoaded", () => {
    listPanel.style.display = "block";
    currentPage = 1;
    fetchPage(1);
  });
  prevBtn.addEventListener("click", () => {
    if (currentPage > 1) {
      fetchPage(currentPage - 1);
    }
  });
  nextBtn.addEventListener("click", () => {
    const maxPage = Math.max(1, Math.ceil(totalCount / pageSize));
    if (currentPage < maxPage) fetchPage(currentPage + 1);
  });

  selectAllBtn.addEventListener("click", () => {
    document.querySelectorAll(".select-checkbox").forEach(cb => cb.checked = true);
  });
  clearBtn.addEventListener("click", () => {
    document.querySelectorAll(".select-checkbox").forEach(cb => cb.checked = false);
  });

  startBtn.addEventListener("click", async () => {
    const streamKey = document.getElementById("streamKeyInput").value.trim();
    if (!streamKey) { showMessage("Stream key is required", "warning"); return; }
    const selected = Array.from(document.querySelectorAll(".select-checkbox:checked")).map(cb => cb.dataset.id);
    if (!selected.length) { showMessage("Select at least one video", "warning"); return; }

    showMessage("Starting stream (server will download and stream each selected video). This can take a while.", "info");
    const form = new FormData();
    form.append("stream_key", streamKey);
    selected.forEach(id => form.append("selected", id));

    try {
      const resp = await fetch("/start", { method: "POST", body: form });
      const data = await resp.json();
      if (data.ok) {
        showMessage("Streaming completed: " + (data.message || ""), "success");
      } else {
        showMessage("Error: " + (data.error || "unknown"), "danger");
      }
    } catch (err) {
      showMessage("Error: " + err.message, "danger");
    }
  });


  // Stream pasted YouTube links
  const streamLinksBtn = document.getElementById("streamLinksBtn");
  const ytLinksTextarea = document.getElementById("ytLinksTextarea");
  if (streamLinksBtn && ytLinksTextarea) {
    streamLinksBtn.addEventListener("click", async () => {
      const streamKey = document.getElementById("streamKeyInput").value.trim();
      if (!streamKey) { showMessage("Stream key is required", "warning"); return; }
      const links = ytLinksTextarea.value.split(/\r?\n/).map(l => l.trim()).filter(l => l);
      if (!links.length) { showMessage("Paste at least one YouTube link", "warning"); return; }

      // Get mode (append or replace)
      const mode = document.querySelector('input[name="linksMode"]:checked')?.value || "replace";

      showMessage("Starting stream for pasted links. This can take a while.", "info");
      const form = new FormData();
      form.append("stream_key", streamKey);
      form.append("links", links.join("\n"));
      form.append("mode", mode);

      try {
        const resp = await fetch("/start_links", { method: "POST", body: form });
        const data = await resp.json();
        if (data.ok) {
          showMessage("Streaming started: " + (data.message || ""), "success");
        } else {
          showMessage("Error: " + (data.error || "unknown"), "danger");
        }
      } catch (err) {
        showMessage("Error: " + err.message, "danger");
      }
    });
  }

})();
