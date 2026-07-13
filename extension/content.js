// Content script: runs in the page, executes click/type/read commands.

// Avoid double-registration if injected twice
if (!window.__aiBrowserContentRegistered) {
  window.__aiBrowserContentRegistered = true;

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    handle(msg).then(sendResponse).catch((e) =>
      sendResponse({ ok: false, error: String(e && e.message || e) })
    );
    return true; // async
  });

  // -------- page-facing presence handshake --------
  // Lets a webpage detect that the extension is installed WITHOUT giving the
  // page any ability to drive it. Real actions still go through the
  // authenticated localhost bridge — this only ever answers "I'm here".
  const BRIDGE_VERSION = "0.1.0";
  try { document.documentElement.setAttribute("data-nexus-bridge", BRIDGE_VERSION); } catch (_) {}
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (d && d.__nexusBridge === "ping") {
      window.postMessage({ __nexusBridge: "pong", version: BRIDGE_VERSION }, "*");
    }
  });

  async function handle(msg) {
    const { action, args = {} } = msg;
    try {
      switch (action) {
        case "click":      return { ok: true, result: await clickAction(args) };
        case "type":
        case "fill":       return { ok: true, result: await typeAction(args) };
        case "get_url":    return { ok: true, result: { url: location.href, title: document.title } };
        case "get_text":   return { ok: true, result: await getTextAction(args) };
        case "get_html":   return { ok: true, result: await getHtmlAction(args) };
        case "scroll_to":  return { ok: true, result: await scrollToAction(args) };
        case "wait_for":   return { ok: true, result: await waitForAction(args) };
        case "press":      return { ok: true, result: await pressKey(args) };
        case "query":      return { ok: true, result: await queryAction(args) };
        default:           return { ok: false, error: `unknown action: ${action}` };
      }
    } catch (e) {
      return { ok: false, error: String(e && e.message || e) };
    }
  }

  // -------- selector resolution --------
  function findOne(selector) {
    if (!selector) return null;
    if (typeof selector !== "string") return null;

    if (selector.startsWith("text=")) {
      return findByText(selector.slice(5).trim(), false);
    }
    if (selector.startsWith("text~=")) {
      return findByText(selector.slice(6).trim(), true);
    }
    try {
      return document.querySelector(selector);
    } catch (_) {
      return null;
    }
  }

  function findByText(needle, fuzzy) {
    if (!needle) return null;
    const want = needle.toLowerCase();
    // Walk all visible elements, prefer the most specific (leaf-most)
    const candidates = [];
    const all = document.body ? document.body.querySelectorAll("*") : [];
    for (const el of all) {
      if (!isVisible(el)) continue;
      const tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT") continue;
      const t = (el.innerText || el.textContent || "").trim().toLowerCase();
      if (!t) continue;
      const match = fuzzy ? t.includes(want) : (t === want || t.includes(want));
      if (match) candidates.push(el);
    }
    if (!candidates.length) return null;
    // Choose the deepest candidate that still actually contains the text directly
    candidates.sort((a, b) => depth(b) - depth(a));
    return candidates[0];
  }

  function depth(el) {
    let d = 0;
    while (el) { el = el.parentElement; d++; }
    return d;
  }

  function isVisible(el) {
    if (!el || !el.getClientRects) return false;
    const rects = el.getClientRects();
    if (!rects.length) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || style.opacity === "0") return false;
    return true;
  }

  function elementDescription(el) {
    if (!el) return null;
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      classes: el.className && typeof el.className === "string" ? el.className.split(/\s+/).filter(Boolean) : [],
      text: (el.innerText || el.textContent || "").trim().slice(0, 200),
      href: el.href || null,
      name: el.name || null,
      type: el.type || null,
    };
  }

  // -------- actions --------
  async function clickAction({ selector, x, y }) {
    let el = null;
    if (selector) {
      el = findOne(selector);
      if (!el) throw new Error(`selector not found: ${selector}`);
      el.scrollIntoView({ block: "center", inline: "center" });
      await new Promise((r) => setTimeout(r, 50));
      // Use a synthetic mouse sequence so React/Vue handlers see it
      const rect = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      for (const type of ["mousedown", "mouseup", "click"]) {
        el.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true, view: window,
          clientX: cx, clientY: cy, button: 0,
        }));
      }
      return { clicked: elementDescription(el) };
    }
    if (typeof x === "number" && typeof y === "number") {
      const target = document.elementFromPoint(x, y);
      if (!target) throw new Error(`no element at (${x},${y})`);
      for (const type of ["mousedown", "mouseup", "click"]) {
        target.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true, view: window,
          clientX: x, clientY: y, button: 0,
        }));
      }
      return { clicked: elementDescription(target), at: { x, y } };
    }
    throw new Error("click requires {selector} or {x,y}");
  }

  async function typeAction({ selector, text, append, submit }) {
    if (typeof text !== "string") throw new Error("type requires {text}");
    const el = selector ? findOne(selector) : document.activeElement;
    if (!el) throw new Error(`selector not found: ${selector}`);

    el.focus();
    const tag = el.tagName;
    const isInput = tag === "INPUT" || tag === "TEXTAREA";

    if (isInput) {
      const newValue = append ? (el.value || "") + text : text;
      // For React/Vue controlled inputs, set via the native setter to make them pick it up
      const proto = tag === "INPUT" ? window.HTMLInputElement.prototype : window.HTMLTextAreaElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, newValue);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (tag === "SELECT") {
      // Match by value attribute, exact option text, or case-insensitive substring
      const want = text.trim();
      const wantLower = want.toLowerCase();
      let found = null;
      for (const opt of el.options) {
        if (opt.value === want) { found = opt; break; }
      }
      if (!found) {
        for (const opt of el.options) {
          const t = (opt.textContent || "").trim();
          if (t === want) { found = opt; break; }
        }
      }
      if (!found) {
        for (const opt of el.options) {
          const t = (opt.textContent || "").trim().toLowerCase();
          if (t === wantLower || t.includes(wantLower)) { found = opt; break; }
        }
      }
      if (!found) throw new Error(`no <option> matches ${JSON.stringify(text)} in select`);
      const proto = window.HTMLSelectElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, found.value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (el.isContentEditable) {
      if (!append) el.textContent = "";
      el.textContent = (el.textContent || "") + text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
    } else {
      throw new Error(`element is not typable: <${tag.toLowerCase()}>`);
    }

    if (submit) {
      el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter", code: "Enter" }));
      const form = el.closest && el.closest("form");
      if (form && typeof form.requestSubmit === "function") form.requestSubmit();
      else if (form) form.submit();
    }
    return {
      typed_into: elementDescription(el),
      value: (isInput || tag === "SELECT") ? el.value : el.textContent,
    };
  }

  async function getTextAction({ selector, max_chars }) {
    const el = selector ? findOne(selector) : document.body;
    if (!el) throw new Error(`selector not found: ${selector}`);
    const text = (el.innerText || el.textContent || "").trim();
    return {
      text: max_chars ? text.slice(0, max_chars) : text,
      length: text.length,
      truncated: max_chars ? text.length > max_chars : false,
    };
  }

  async function getHtmlAction({ selector, max_chars }) {
    const el = selector ? findOne(selector) : document.documentElement;
    if (!el) throw new Error(`selector not found: ${selector}`);
    const html = el.outerHTML;
    return {
      html: max_chars ? html.slice(0, max_chars) : html,
      length: html.length,
      truncated: max_chars ? html.length > max_chars : false,
    };
  }

  async function scrollToAction({ selector, x, y, behavior }) {
    if (selector) {
      const el = findOne(selector);
      if (!el) throw new Error(`selector not found: ${selector}`);
      el.scrollIntoView({ block: "center", inline: "center", behavior: behavior || "auto" });
      return { scrolled_to: elementDescription(el) };
    }
    if (typeof x === "number" || typeof y === "number") {
      window.scrollTo({ left: x || 0, top: y || 0, behavior: behavior || "auto" });
      return { scrolled_to: { x: window.scrollX, y: window.scrollY } };
    }
    throw new Error("scroll_to requires {selector} or {x,y}");
  }

  async function waitForAction({ selector, timeout_ms = 10000, state = "visible" }) {
    if (!selector) throw new Error("wait_for requires {selector}");
    const deadline = Date.now() + timeout_ms;
    while (Date.now() < deadline) {
      const el = findOne(selector);
      if (el) {
        if (state === "attached") return { found: elementDescription(el) };
        if (state === "visible" && isVisible(el)) return { found: elementDescription(el) };
        if (state === "detached" && !el) return { found: null };
      } else if (state === "detached") {
        return { found: null };
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error(`wait_for timeout: ${selector} (${state})`);
  }

  async function pressKey({ key, selector }) {
    const target = selector ? findOne(selector) : document.activeElement || document.body;
    if (!target) throw new Error(`selector not found: ${selector}`);
    for (const type of ["keydown", "keypress", "keyup"]) {
      target.dispatchEvent(new KeyboardEvent(type, { bubbles: true, key, code: key }));
    }
    return { pressed: key };
  }

  async function queryAction({ selector, limit = 20 }) {
    if (!selector) throw new Error("query requires {selector}");
    let els = [];
    if (selector.startsWith("text=") || selector.startsWith("text~=")) {
      const one = findOne(selector);
      if (one) els = [one];
    } else {
      try { els = Array.from(document.querySelectorAll(selector)); } catch (_) {}
    }
    return {
      count: els.length,
      matches: els.slice(0, limit).map(elementDescription),
    };
  }
}
