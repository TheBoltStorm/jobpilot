// Small progressive enhancements. Every page works without JavaScript.
document.addEventListener("DOMContentLoaded", () => {
  // Filter forms submit themselves when a control changes.
  document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
    let timer;
    form.addEventListener("change", () => form.requestSubmit());
    form.querySelectorAll("input[type=search]").forEach((el) =>
      el.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => form.requestSubmit(), 500); }));
  });

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
