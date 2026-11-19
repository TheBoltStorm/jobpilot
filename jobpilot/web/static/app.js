// Small progressive enhancements. Every page works without JavaScript.
document.addEventListener("DOMContentLoaded", () => {
  // Filter forms submit themselves when a control changes.
  document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
    let timer;
    form.addEventListener("change", () => form.requestSubmit());
    form.querySelectorAll("input[type=search]").forEach((el) =>
      el.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => form.requestSubmit(), 500); }));
  });

  // Copy a follow-up draft (subject + body) to the clipboard. Sending is up to you.
  document.querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
    const box = btn.closest("details, section") || document;
    const subj = box.querySelector("[data-copy-src]"), body = box.querySelector("[data-copy-body]");
    const text = (subj ? "Subject: " + subj.textContent.trim() + "\n\n" : "") + (body ? body.value : "");
    try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
    catch { if (body) { body.select(); } btn.textContent = "Select and copy"; }
  }));

  // Answer once: tick or untick every job for a question (you still choose which).
  document.querySelectorAll("[data-tick]").forEach((btn) => btn.addEventListener("click", () => {
    btn.closest("form").querySelectorAll("input[name=job]").forEach((b) => { b.checked = btn.dataset.tick === "all"; });
  }));

  // "/" jumps to the search box (unless you're typing somewhere).
  const hot = document.querySelector("[data-hotkey='/']");
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if (hot && e.key === "/" && !typing) { e.preventDefault(); hot.focus(); }
    // Single-key decisions where a page offers them (Sort jobs: Y keep, L later, N pass, O open).
    if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.length === 1) {
      const btn = document.querySelector(`[data-key="${CSS.escape(e.key.toLowerCase())}"]`);
      if (btn) { e.preventDefault(); btn.click(); return; }
    }
    // Ctrl+Enter on a job page: Approve & next (the confirm box still has to be ticked).
    const quick = document.querySelector("[data-hotkey-submit]");
    if (quick && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); quick.click(); }
  });

  // Applications pipeline: drag a card to another column to set its stage. You're the one moving it.
  const board = document.querySelector(".kanban");
  if (board) {
    let dragged = null;
    board.addEventListener("dragstart", (e) => {
      dragged = e.target.closest(".kcard");
      if (!dragged) return;
      dragged.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragged.dataset.job);
    });
    board.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      board.querySelectorAll(".kcol.over").forEach((c) => c.classList.remove("over"));
    });
    board.querySelectorAll(".kcol").forEach((col) => {
      col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("over"); });
      col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("over"); });
      col.addEventListener("drop", async (e) => {
        e.preventDefault();
        col.classList.remove("over");
        if (!dragged || dragged.closest(".kcol") === col) return;
        const from = dragged.closest(".kcol");
        const body = new FormData();
        body.append("csrf", board.dataset.csrf);
        body.append("action", "outcome");
        body.append("stage", col.dataset.stage);
        const card = dragged;
        try {
          const r = await fetch(`/applied/${card.dataset.job}`, { method: "POST", body, headers: { "x-jobpilot-fetch": "1" } });
          const data = await r.json();
          if (!r.ok || !data.ok) throw new Error(data.error || "Couldn't change the stage.");
          col.querySelector(".kcol-body").prepend(card);
          card.dataset.stage = col.dataset.stage;
          card.querySelectorAll(".kc-foot .tag:not(:first-child)").forEach((t) => t.remove());
          for (const c of [from, col]) {
            const n = c.querySelectorAll(".kcard").length;
            c.querySelector(".count").textContent = n;
            const empty = c.querySelector(".empty");
            if (empty) empty.hidden = n > 0;
          }
        } catch (err) { alert(err.message); }
      });
    });
  }

  // Review page: warn before leaving with unsaved edits; Reject asks to confirm.
  const review = document.getElementById("review");
  if (review) {
    let dirty = false, submitting = false;
    review.addEventListener("input", () => { dirty = true; });
    review.addEventListener("submit", (e) => {
      const action = e.submitter && e.submitter.value;
      if (action === "reject" && !confirm("Reject this job?")) { e.preventDefault(); return; }
      submitting = true;
    });
    window.addEventListener("beforeunload", (e) => { if (dirty && !submitting) { e.preventDefault(); e.returnValue = ""; } });
  }
});
