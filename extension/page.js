// JobPilot browser button: reads and fills the form on the page you have open.
// Runs only when you click the button. It never clicks Submit, never presses Enter, never touches
// password fields or file uploads, and only fills what you ticked in the popup.
(() => {
  if (window.__jobpilot) return;

  const SKIP = new Set(["hidden", "submit", "button", "reset", "image", "search", "range", "color"]);
  const txt = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  function textWithout(node) {
    const c = node.cloneNode(true);
    c.querySelectorAll("input, select, textarea, option, button, script, style, svg").forEach((x) => x.remove());
    return txt(c.textContent);
  }

  function firstLine(s, max = 300) {
    return txt(s).slice(0, max);
  }

  function labelText(el) {
    if (el.labels && el.labels.length) {
      const t = txt([...el.labels].map(textWithout).join(" "));
      if (t) return t;
    }
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const t = txt(by.split(/\s+/).map((id) => (document.getElementById(id) || {}).textContent || "").join(" "));
      if (t) return t;
    }
    const al = el.getAttribute("aria-label");
    if (txt(al)) return txt(al);
    // Text next to the field: the nearest wrapper with some words in it.
    let node = el;
    for (let i = 0; i < 3 && node.parentElement; i++) {
      node = node.parentElement;
      if (node.querySelectorAll("input, select, textarea").length > 1) break;
      const t = textWithout(node);
      if (t) return firstLine(t);
    }
    return txt(el.getAttribute("placeholder") || el.title || el.name || el.id);
  }

  function optionLabel(i) {
    if (i.labels && i.labels.length) {
      const t = textWithout(i.labels[0]);
      if (t) return t;
    }
    return txt(i.getAttribute("aria-label") || i.value);
  }

  function groupLabel(inputs, options) {
    const first = inputs[0];
    const fs = first.closest("fieldset");
    if (fs && inputs.every((i) => fs.contains(i))) {
      const lg = fs.querySelector("legend");
      if (lg && txt(lg.textContent)) return txt(lg.textContent);
    }
    const grp = first.closest("[role=radiogroup], [role=group]");
    if (grp) {
      const t = txt(grp.getAttribute("aria-label")) ||
        txt(((grp.getAttribute("aria-labelledby") || "").split(/\s+/).map((id) => (document.getElementById(id) || {}).textContent || "")).join(" "));
      if (t) return t;
    }
    let node = first.parentElement;
    while (node && !inputs.every((i) => node.contains(i))) node = node.parentElement;
    for (let up = 0; node && up < 3; up++, node = node.parentElement) {
      let t = textWithout(node);
      for (const o of options) t = t.replace(o, " ");
      t = txt(t);
      if (t) return firstLine(t);
    }
    return txt(first.name);
  }

  function scan() {
    const fields = [];
    const groups = new Map();
    let n = 0;
    let hasPassword = false;
    for (const el of document.querySelectorAll("input, select, textarea")) {
      const type = (el.getAttribute("type") || el.type || "").toLowerCase();
      if (type === "password") { if (visible(el)) hasPassword = true; continue; }
      if (SKIP.has(type) || el.disabled || el.readOnly) continue;
      if (type === "radio" || type === "checkbox") {
        const name = el.name || el.id;
        if (!name) continue;
        const k = type + ":" + name;
        if (!groups.has(k)) groups.set(k, { type, name, inputs: [] });
        groups.get(k).inputs.push(el);
        continue;
      }
      if (!visible(el)) continue;
      const key = "jp" + n++;
      el.dataset.jobpilot = key;
      const kind = el.tagName === "TEXTAREA" ? "textarea"
        : el.tagName === "SELECT" ? (el.multiple ? "multi_select" : "select")
        : type === "file" ? "file" : "text";
      const options = el.tagName === "SELECT"
        ? [...el.options].filter((o) => o.value !== "" && !o.disabled).map((o) => txt(o.text)) : [];
      fields.push({ key, kind, label: labelText(el), name: el.name || "", id: el.id || "",
        required: el.required || el.getAttribute("aria-required") === "true", options });
    }
    for (const g of groups.values()) {
      const shown = g.inputs.filter((i) => visible(i) || (i.labels && [...i.labels].some(visible)));
      if (!shown.length) continue;
      const key = "jp" + n++;
      g.inputs.forEach((i) => { i.dataset.jobpilot = key; });
      const kind = g.type === "radio" ? "radio" : (g.inputs.length > 1 ? "multi_select" : "checkbox");
      const options = kind === "checkbox" ? [] : g.inputs.map(optionLabel);
      const label = kind === "checkbox" ? labelText(g.inputs[0]) : groupLabel(g.inputs, options);
      fields.push({ key, kind, label, name: g.name, id: "", required: g.inputs.some((i) => i.required), options });
    }
    return { url: location.href, title: document.title, hasPassword, fields };
  }

  function setValue(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype
      : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v); // works with React-style inputs too
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function mark(el, ok) {
    if (!el) return;
    el.style.outline = ok ? "2px solid #1d9a5b" : "2px solid #e08a00";
    el.style.outlineOffset = "2px";
  }

  // answers: [{key, value, kind}] you ticked. left: keys of fields left for you (outlined orange).
  function fill(answers, left) {
    const res = { filled: 0, failed: [], left: 0 };
    for (const a of answers) {
      const els = [...document.querySelectorAll(`[data-jobpilot="${CSS.escape(a.key)}"]`)];
      const el = els[0];
      try {
        if (!el) throw new Error("gone");
        const want = String(a.value).split("; ");
        if (el.tagName === "INPUT" && (el.type === "radio" || el.type === "checkbox")) {
          let hit = false;
          for (const i of els) {
            if (want.includes(optionLabel(i))) {
              if (!i.checked) i.click(); // a radio or checkbox option, never a button
              mark(i.closest("label") || i, true);
              hit = true;
            }
          }
          if (!hit) throw new Error("no such option");
        } else if (el.tagName === "SELECT") {
          if (el.multiple) {
            for (const o of el.options) o.selected = want.includes(txt(o.text));
            el.dispatchEvent(new Event("change", { bubbles: true }));
          } else {
            const opt = [...el.options].find((o) => txt(o.text) === a.value) || [...el.options].find((o) => o.value === a.value);
            if (!opt) throw new Error("no such option");
            setValue(el, opt.value);
          }
          mark(el, true);
        } else {
          setValue(el, a.value);
          mark(el, true);
        }
        res.filled++;
      } catch (e) {
        res.failed.push(a.key);
        mark(el, false);
      }
    }
    for (const k of left) {
      const el = document.querySelector(`[data-jobpilot="${CSS.escape(k)}"]`);
      if (!el) continue;
      const box = (el.type === "radio" || el.type === "checkbox") ? (el.closest("fieldset") || el.parentElement) : el;
      mark(box, false);
      res.left++;
    }
    return res;
  }

  function pageInfo() {
    const meta = (p) => (document.querySelector(`meta[property="${p}"], meta[name="${p}"]`) || {}).content || "";
    const h1 = document.querySelector("h1");
    return {
      url: location.href,
      title: txt(meta("og:title") || (h1 && h1.textContent) || document.title).slice(0, 300),
      company: txt(meta("og:site_name")).slice(0, 200) || location.hostname.replace(/^(www|jobs|careers)\./, ""),
      description: txt(document.body ? document.body.innerText : "").slice(0, 50000),
    };
  }

  window.__jobpilot = { scan, fill, pageInfo };
})();
