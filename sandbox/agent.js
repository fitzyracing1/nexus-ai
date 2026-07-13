/*
 * Nexus Sandbox Agent
 * -------------------
 * This is the SAME action logic that ships in extension/content.js — the
 * selector engine (text= / text~= / CSS), the synthetic mouse/keyboard event
 * sequences, and the React/Vue-safe native-setter trick for inputs.
 *
 * The only difference from the real extension: instead of running as a content
 * script inside a real Chrome tab (reached over the localhost bridge), it runs
 * in the page and operates on the sandbox browser's active tab — an <iframe>
 * served from the same origin, so it can be scripted directly. Everything it
 * does is real DOM manipulation, not a simulation.
 *
 * All events/setters use the iframe's OWN window realm (ctx.win) so that
 * `instanceof` checks and controlled-input setters match the target elements.
 */
(function (global) {
  "use strict";

  function ctxOf(iframe) {
    return { win: iframe.contentWindow, doc: iframe.contentDocument };
  }

  // -------- visibility / description --------
  function isVisible(win, el) {
    if (!el || !el.getClientRects) return false;
    if (!el.getClientRects().length) return false;
    const style = win.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || style.opacity === "0") return false;
    return true;
  }

  function depth(el) {
    let d = 0;
    while (el) { el = el.parentElement; d++; }
    return d;
  }

  function elementDescription(el) {
    if (!el) return null;
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      classes: el.className && typeof el.className === "string"
        ? el.className.split(/\s+/).filter(Boolean) : [],
      text: (el.innerText || el.textContent || "").trim().slice(0, 200),
      href: el.href || null,
      name: el.name || null,
      type: el.type || null,
    };
  }

  // -------- selector resolution (mirrors content.js) --------
  function findByText(doc, win, needle, fuzzy) {
    if (!needle) return null;
    const want = needle.toLowerCase();
    const candidates = [];
    const all = doc.body ? doc.body.querySelectorAll("*") : [];
    for (const el of all) {
      if (!isVisible(win, el)) continue;
      const tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT") continue;
      const t = (el.innerText || el.textContent || "").trim().toLowerCase();
      if (!t) continue;
      const match = fuzzy ? t.includes(want) : (t === want || t.includes(want));
      if (match) candidates.push(el);
    }
    if (!candidates.length) return null;
    candidates.sort((a, b) => depth(b) - depth(a));
    return candidates[0];
  }

  function findOne(doc, win, selector) {
    if (!selector || typeof selector !== "string") return null;
    if (selector.startsWith("text~=")) return findByText(doc, win, selector.slice(6).trim(), true);
    if (selector.startsWith("text=")) return findByText(doc, win, selector.slice(5).trim(), false);
    try { return doc.querySelector(selector); } catch (_) { return null; }
  }

  // -------- highlight (so a human can watch the agent work) --------
  function ensureHlStyle(doc) {
    if (doc.getElementById("__nexus_hl_style")) return;
    const s = doc.createElement("style");
    s.id = "__nexus_hl_style";
    s.textContent =
      ".__nexus_hl{outline:3px solid #34d399 !important;outline-offset:2px;" +
      "box-shadow:0 0 0 6px rgba(52,211,153,.25) !important;border-radius:4px;}";
    (doc.head || doc.documentElement).appendChild(s);
  }
  function highlight(ctx, el) {
    if (!el) return;
    try {
      ensureHlStyle(ctx.doc);
      el.classList.add("__nexus_hl");
      ctx.win.setTimeout(() => el.classList.remove("__nexus_hl"), 900);
    } catch (_) {}
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // -------- actions --------
  async function clickAction(ctx, args) {
    const { selector, x, y } = args;
    let el = null;
    if (selector) {
      el = findOne(ctx.doc, ctx.win, selector);
      if (!el) throw new Error(`selector not found: ${selector}`);
      el.scrollIntoView({ block: "center", inline: "center" });
      highlight(ctx, el);
      await wait(120);
      const rect = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      for (const type of ["mousedown", "mouseup", "click"]) {
        el.dispatchEvent(new ctx.win.MouseEvent(type, {
          bubbles: true, cancelable: true, view: ctx.win, clientX: cx, clientY: cy, button: 0,
        }));
      }
      return { clicked: elementDescription(el) };
    }
    if (typeof x === "number" && typeof y === "number") {
      const target = ctx.doc.elementFromPoint(x, y);
      if (!target) throw new Error(`no element at (${x},${y})`);
      highlight(ctx, target);
      for (const type of ["mousedown", "mouseup", "click"]) {
        target.dispatchEvent(new ctx.win.MouseEvent(type, {
          bubbles: true, cancelable: true, view: ctx.win, clientX: x, clientY: y, button: 0,
        }));
      }
      return { clicked: elementDescription(target), at: { x, y } };
    }
    throw new Error("click requires {selector} or {x,y}");
  }

  async function typeAction(ctx, args) {
    const { selector, text, append, submit } = args;
    if (typeof text !== "string") throw new Error("type requires {text}");
    const el = selector ? findOne(ctx.doc, ctx.win, selector) : ctx.doc.activeElement;
    if (!el) throw new Error(`selector not found: ${selector}`);
    el.focus();
    highlight(ctx, el);
    const tag = el.tagName;
    const isInput = tag === "INPUT" || tag === "TEXTAREA";

    if (isInput) {
      const newValue = append ? (el.value || "") + text : text;
      const proto = tag === "INPUT" ? ctx.win.HTMLInputElement.prototype : ctx.win.HTMLTextAreaElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, newValue);
      el.dispatchEvent(new ctx.win.Event("input", { bubbles: true }));
      el.dispatchEvent(new ctx.win.Event("change", { bubbles: true }));
    } else if (el.isContentEditable) {
      if (!append) el.textContent = "";
      el.textContent = (el.textContent || "") + text;
      el.dispatchEvent(new ctx.win.InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
    } else {
      throw new Error(`element is not typable: <${tag.toLowerCase()}>`);
    }

    if (submit) {
      el.dispatchEvent(new ctx.win.KeyboardEvent("keydown", { bubbles: true, key: "Enter", code: "Enter" }));
      const form = el.closest && el.closest("form");
      if (form && typeof form.requestSubmit === "function") form.requestSubmit();
      else if (form) form.submit();
    }
    return { typed_into: elementDescription(el), value: isInput ? el.value : el.textContent, submitted: !!submit };
  }

  function getTextAction(ctx, args) {
    const { selector, max_chars } = args;
    const el = selector ? findOne(ctx.doc, ctx.win, selector) : ctx.doc.body;
    if (!el) throw new Error(`selector not found: ${selector}`);
    highlight(ctx, el);
    const text = (el.innerText || el.textContent || "").trim();
    return { text: max_chars ? text.slice(0, max_chars) : text, length: text.length,
             truncated: max_chars ? text.length > max_chars : false };
  }

  function getHtmlAction(ctx, args) {
    const { selector, max_chars } = args;
    const el = selector ? findOne(ctx.doc, ctx.win, selector) : ctx.doc.documentElement;
    if (!el) throw new Error(`selector not found: ${selector}`);
    const html = el.outerHTML;
    return { html: max_chars ? html.slice(0, max_chars) : html, length: html.length,
             truncated: max_chars ? html.length > max_chars : false };
  }

  function queryAction(ctx, args) {
    const { selector, limit = 20 } = args;
    if (!selector) throw new Error("query requires {selector}");
    let els = [];
    if (selector.startsWith("text=") || selector.startsWith("text~=")) {
      const one = findOne(ctx.doc, ctx.win, selector);
      if (one) els = [one];
    } else {
      try { els = Array.from(ctx.doc.querySelectorAll(selector)); } catch (_) {}
    }
    return { count: els.length, matches: els.slice(0, limit).map(elementDescription) };
  }

  function scrollToAction(ctx, args) {
    const { selector, x, y, behavior } = args;
    if (selector) {
      const el = findOne(ctx.doc, ctx.win, selector);
      if (!el) throw new Error(`selector not found: ${selector}`);
      el.scrollIntoView({ block: "center", inline: "center", behavior: behavior || "smooth" });
      highlight(ctx, el);
      return { scrolled_to: elementDescription(el) };
    }
    if (typeof x === "number" || typeof y === "number") {
      ctx.win.scrollTo({ left: x || 0, top: y || 0, behavior: behavior || "smooth" });
      return { scrolled_to: { x: ctx.win.scrollX, y: ctx.win.scrollY } };
    }
    throw new Error("scroll_to requires {selector} or {x,y}");
  }

  async function pressKey(ctx, args) {
    const { key, selector } = args;
    const target = selector ? findOne(ctx.doc, ctx.win, selector) : (ctx.doc.activeElement || ctx.doc.body);
    if (!target) throw new Error(`selector not found: ${selector}`);
    for (const type of ["keydown", "keypress", "keyup"]) {
      target.dispatchEvent(new ctx.win.KeyboardEvent(type, { bubbles: true, key, code: key }));
    }
    return { pressed: key };
  }

  // -------- dispatcher --------
  // opts: { navigate(url)->Promise, currentUrl()->string, screenshot()->Promise<dataUrl> }
  async function execute(iframe, cmd, opts = {}) {
    const action = cmd.action;
    const args = cmd.args || {};

    if (action === "navigate") {
      if (!opts.navigate) throw new Error("navigate not supported here");
      await opts.navigate(args.url);
      return { url: opts.currentUrl ? opts.currentUrl() : args.url };
    }
    if (action === "get_url") {
      const ctx = ctxOf(iframe);
      return { url: opts.currentUrl ? opts.currentUrl() : ctx.win.location.href, title: ctx.doc.title };
    }
    if (action === "list_tabs") {
      return { tabs: [{ id: 1, title: ctxOf(iframe).doc.title, url: opts.currentUrl ? opts.currentUrl() : "", active: true }] };
    }
    if (action === "screenshot") {
      if (!opts.screenshot) throw new Error("screenshot not supported here");
      return { data_url: await opts.screenshot() };
    }

    const ctx = ctxOf(iframe);
    if (!ctx.doc || !ctx.doc.body) throw new Error("no active page loaded");
    switch (action) {
      case "click": return await clickAction(ctx, args);
      case "type":
      case "fill": return await typeAction(ctx, args);
      case "get_text": return getTextAction(ctx, args);
      case "get_html": return getHtmlAction(ctx, args);
      case "query": return queryAction(ctx, args);
      case "scroll_to": return scrollToAction(ctx, args);
      case "press": return await pressKey(ctx, args);
      default: throw new Error(`unknown action: ${action}`);
    }
  }

  global.NexusAgent = { execute };
})(window);
