// =============================================================================
// Index page: client-side search / filter / sort over the run-card grid.
// =============================================================================
(function () {
  const grid = document.getElementById("run-grid");
  if (!grid) return;

  const searchInput = document.getElementById("run-search");
  const sortSelect = document.getElementById("run-sort");
  const chips = document.querySelectorAll(".filter-chips .chip");
  const emptyMsg = document.getElementById("empty-search");

  // Persist user choices across reloads so coming back to the page keeps your
  // filter/sort state. localStorage on a same-origin local app is fine here.
  const STORAGE_KEY = "resumeTailor.indexState";
  const saved = (() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
    catch { return {}; }
  })();
  let activeFilter = saved.filter || "all";
  let activeSort = saved.sort || "recent";
  if (saved.search && searchInput) searchInput.value = saved.search;
  if (sortSelect) sortSelect.value = activeSort;
  chips.forEach((c) => c.classList.toggle("active", c.dataset.filter === activeFilter));

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        filter: activeFilter,
        sort: activeSort,
        search: searchInput ? searchInput.value : "",
      }));
    } catch { /* ignore quota errors */ }
  }

  function applyFilterAndSearch() {
    const q = (searchInput?.value || "").trim().toLowerCase();
    let visible = 0;
    grid.querySelectorAll(".run-card").forEach((card) => {
      const matchesFilter =
        activeFilter === "all" ||
        (activeFilter === "in_progress" && card.classList.contains("is-in-progress")) ||
        (activeFilter === "done"        && card.classList.contains("is-done")) ||
        (activeFilter === "markdown"    && card.classList.contains("is-markdown"));
      const haystack = (card.dataset.name + " " + card.dataset.title).toLowerCase();
      const matchesQuery = !q || haystack.includes(q);
      const show = matchesFilter && matchesQuery;
      card.classList.toggle("hidden", !show);
      if (show) visible++;
    });
    if (emptyMsg) emptyMsg.hidden = visible > 0 || grid.children.length === 0;
  }

  function applySort() {
    const cards = Array.from(grid.querySelectorAll(".run-card"));
    const compare = {
      recent: (a, b) => parseFloat(b.dataset.mtime) - parseFloat(a.dataset.mtime),
      name:   (a, b) => a.dataset.name.localeCompare(b.dataset.name),
      status: (a, b) => a.dataset.status.localeCompare(b.dataset.status),
    }[activeSort] || (() => 0);
    cards.sort(compare).forEach((c) => grid.appendChild(c));
  }

  searchInput?.addEventListener("input", () => { applyFilterAndSearch(); persist(); });
  sortSelect?.addEventListener("change", () => {
    activeSort = sortSelect.value;
    applySort();
    persist();
  });
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      chips.forEach((c) => {
        c.classList.toggle("active", c === chip);
        c.setAttribute("aria-selected", c === chip ? "true" : "false");
      });
      activeFilter = chip.dataset.filter;
      applyFilterAndSearch();
      persist();
    });
  });

  // Initial paint reflects restored state.
  applySort();
  applyFilterAndSearch();
})();

// =============================================================================
// Approve view: "Apply & open editor" button.
// Drives the POST via fetch, then explicitly navigates to the editor. Using
// fetch + manual nav side-steps any weirdness with browsers following the
// 302 from a same-origin POST inside an embedded form.
// =============================================================================
(function () {
  const form = document.getElementById("apply-and-open-form");
  if (!form) return;
  const editorUrl = form.dataset.editorUrl;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Applying…";
    }
    try {
      // We don't care about the response body — the server has done the
      // apply (or flashed an error). We just want to land in the editor.
      await fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        redirect: "manual",
      }).catch(() => {});
    } finally {
      window.location.assign(editorUrl);
    }
  });
})();

// =============================================================================
// Per-decision autosave for the approve view.
// One AJAX POST per click; the surrounding card updates on success.
// =============================================================================

(function () {
  const list = document.querySelector(".suggestions");
  if (!list) return;

  const runName = list.dataset.run;
  const summaryEl = document.querySelector('[data-role="summary"]');
  const totalSuggestions = list.querySelectorAll(".suggestion").length;

  list.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;

    const li = btn.closest(".suggestion");
    const sid = li.dataset.id;
    const action = btn.dataset.action;
    const ta = li.querySelector('textarea[data-role="suggested"]');
    const finalText = ta ? ta.value : "";
    const status = li.querySelector('[data-role="status"]');

    setStatus(status, "Saving…", "");
    btn.disabled = true;

    try {
      const resp = await fetch(`/runs/${runName}/decisions/${sid}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, final_text: finalText }),
      });
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `HTTP ${resp.status}`);
      }
      const data = await resp.json();

      // Activate this button, deactivate the others.
      li.querySelectorAll(".actions button").forEach((b) =>
        b.classList.toggle("active", b.dataset.action === action)
      );

      // Set the visual decision class on the card.
      li.classList.remove("decision-approve", "decision-edit", "decision-deny");
      li.classList.add("has-decision", `decision-${action}`);

      // Update or insert the decision tag in the header.
      const header = li.querySelector("header");
      let tag = header.querySelector(".decision-tag");
      if (!tag) {
        tag = document.createElement("span");
        tag.className = "decision-tag";
        header.appendChild(tag);
      }
      tag.className = `decision-tag tag-${action}`;
      tag.textContent = action;

      setStatus(status, `Saved as ${action}.`, "saved");
      updateSummary(data.summary);
    } catch (err) {
      setStatus(status, `Error: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  });

  function setStatus(el, text, cls) {
    el.textContent = text;
    el.className = "status " + cls;
  }

  function updateSummary(s) {
    if (!summaryEl || !s) return;
    summaryEl.textContent =
      `${s.total} / ${totalSuggestions} decided · ` +
      `${s.approved} approved · ${s.edited} edited · ${s.denied} denied`;
  }
})();
