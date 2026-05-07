// Per-decision autosave for the approve view.
// One AJAX POST per click; the surrounding card updates on success.

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
