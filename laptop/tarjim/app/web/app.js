"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const svg = (paths, cls) => {
  const t = document.createElement("template");
  t.innerHTML = `<svg viewBox="0 0 24 24"${cls ? ` class="${cls}"` : ""}>${paths}</svg>`;
  return t.content.firstChild;
};

/* ------------------------------ who is asking ------------------------------ */
// On the laptop the page talks to 127.0.0.1 and needs nothing. On a phone it
// carries the token it was given when it was paired.

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } },
};
let TOKEN = store.get("tarjim-token");
const withKey = url => TOKEN ? `${url}${url.includes("?") ? "&" : "?"}k=${encodeURIComponent(TOKEN)}` : url;

async function api(path, body) {
  const opt = body === undefined ? { headers: {} } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  if (TOKEN) opt.headers["X-Tarjim-Token"] = TOKEN;
  let res;
  try { res = await fetch(path, opt); } catch { throw new Error(S.app ? "ما قدرت أوصل لترجِم. قفّل التطبيق وافتحه من جديد." : "ما قدرت أوصل لترجِم. تأكد إن البرنامج شغّال على اللابتوب."); }
  if (res.status === 403 && S.remote) { needPairing(); throw new Error("الجوال مو مربوط."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "صار خطأ غير متوقع.");
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  // A popover sits above an open sheet (the library, a question), so the message is never hidden behind it.
  try {
    if (t.matches(":popover-open")) t.hidePopover();
    t.showPopover?.();
  } catch { /* no popovers in this browser: the plain element still shows */ }
  requestAnimationFrame(() => t.classList.add("on"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.classList.remove("on");
    toastTimer = setTimeout(() => { try { t.hidePopover?.(); } catch { /* already closed */ } }, 450);
  }, 2600);
}

function ask(title, text, yes = "نعم", danger = true) {
  const d = $("#dlg-ask");
  $("#ask-title").textContent = title;
  $("#ask-text").textContent = text;
  $("#ask-yes").textContent = yes;
  $("#ask-yes").classList.toggle("danger", danger);
  return new Promise(resolve => {
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.returnValue = "cancel";
    d.showModal();
    $("#ask-no").focus();
  });
}

const S = {
  live: { state: "idle" }, check: null, profiles: [], profile: null, guideStep: 0, task: null,
  remote: false, skew: 0, latencies: [], still: false, summary: {}, nodes: {}, recent: [],
};

/* ------------------------------- navigation ------------------------------- */

function go(view) {
  if (view === "game") view = "look";  // the look page's old name (links and screenshots)
  if (!$(`#view-${view}`)) return;
  $$(".view").forEach(v => v.classList.toggle("on", v.id === `view-${view}`));
  $$(".nav[data-view]").forEach(b => b.classList.toggle("on", b.dataset.view === view));
  document.body.dataset.view = view;
  document.body.classList.remove("show-devices", "scrolled");
  $("main").scrollTo({ top: 0 });
  if (view === "look" || view === "names") loadProfile();
  if (view === "guide") renderGuide();
  if (view === "phone") loadPhone();
  if (view === "setup") loadSetup();
  if (!S.app) window.TarjimApp?.keepAwake?.(view === "screen");
  syncLive();
}
$$(".nav[data-view]").forEach(b => b.addEventListener("click", () => go(b.dataset.view)));
document.addEventListener("click", e => {
  const t = e.target.closest("[data-goto]");
  if (t) go(t.dataset.goto);
});
document.addEventListener("keydown", e => {
  if (!e.ctrlKey || e.altKey || e.metaKey) return;
  const visible = $$(".nav[data-view]").filter(b => b.offsetParent !== null);
  const i = Number(e.key) - 1;
  if (Number.isInteger(i) && visible[i]) { e.preventDefault(); visible[i].click(); }
});
$("main").addEventListener("scroll", () => document.body.classList.toggle("scrolled", $("main").scrollTop > 8), { passive: true });

// The live picture behind the page: only on the home page, only while translating a PS5 game
// (the Xbox picture reaches the program cut down to the subtitle area), and only if wanted.
// ?every=<ms> or ?every=off: another pace, only to measure what the live picture costs.
const LIVE_PACE = new URLSearchParams(location.search).get("every");
// How wide the PS5 picture must be, in real screen pixels, to fill a box this big without
// being enlarged (enlarged, it looks pixelated). The program never sends more than the PS5's own.
const frameWidth = (w, h) => Math.ceil(Math.max(w, h * 16 / 9) * (window.devicePixelRatio || 1) / 160) * 160;
function syncLive() {
  const want = UI.liveWanted() && S.live.state === "running" && document.body.dataset.view === "home"
    && !S.still && S.kind !== "xbox" && LIVE_PACE !== "off";
  UI.live(want, () => withKey(`/api/frame?w=${frameWidth(innerWidth, innerHeight)}&t=${Date.now()}`), Number(LIVE_PACE) || (S.app || S.remote ? 500 : 250));
}

/* ---------------------------------- home ---------------------------------- */

const STEP_TEXT = { models: "أحمّل القارئ والمترجم…", tv: "أشغّل تطبيق التلفزيون…", ps5: "أتصل بصورة الجهاز…" };
let mirrorTimer, clockTimer;

// The line the TV shows right now, over the picture, for about as long as the TV shows it.
function playLine(en, ar, seconds) {
  $("#tv-en").textContent = en;
  $("#tv-ar").textContent = ar;
  clearTimeout(mirrorTimer);
  $("#mirror").classList.add("on");
  if (seconds) mirrorTimer = setTimeout(() => $("#mirror").classList.remove("on"), seconds * 1000);
}
function clearSubs() {
  clearTimeout(mirrorTimer);
  $("#mirror").classList.remove("on");
}
const readTime = ar => Math.min(6, Math.max(1.6, String(ar).length / 15));

function renderClock() {
  const at = S.live.started_at;
  if (S.live.state !== "running" || !at) { $("#tv-clock").textContent = ""; return; }
  const sec = Math.max(0, Math.floor(Date.now() / 1000 + S.skew - at));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  $("#tv-clock").textContent = `${h ? `${h}:` : ""}${String(m).padStart(h ? 2 : 1, "0")}:${String(s).padStart(2, "0")}`;
}

let lastState = null;
function renderLive() {
  const { state, step, error } = S.live;
  $("#stage").dataset.state = state;
  document.body.classList.toggle("running", state === "running");
  document.body.classList.toggle("starting", state === "starting" || state === "stopping");
  $("#orb-title").textContent = { idle: "ابدأ الترجمة", starting: "لحظات…", running: "إيقاف الترجمة", stopping: "أوقّف…" }[state];
  $("#orb-step").textContent = state === "starting" ? (STEP_TEXT[step] || "") : state === "stopping" ? "أمسح الشاشة" : "";
  $("#orb").setAttribute("aria-label", state === "running" ? "إيقاف الترجمة" : "تشغيل الترجمة");
  const alert = $("#alert");
  alert.hidden = !error;
  alert.textContent = error || "";
  $$(".game-select").forEach(s => { s.disabled = state !== "idle"; });

  const order = ["models", "tv", "ps5"];
  const at = state === "starting" ? order.indexOf(step) : -1;
  $$("#progress i").forEach((seg, i) => {
    seg.classList.toggle("done", at > i);
    seg.classList.toggle("now", at === i);
  });
  $("#second-state").textContent = state === "running" ? "مباشر" : "الترجمة واقفة";
  if (S.app) {
    // Android slows a hidden app to a crawl: keep the screen on for as long as the translation runs.
    window.TarjimApp?.keepAwake?.(state !== "idle");
    if (state === "idle" && !$("#rest-screen").hidden) rest(false);
  }

  if (state !== lastState) {
    if (state === "idle" && lastState !== null) clearSubs();
    clearInterval(clockTimer);
    if (state === "running") clockTimer = setInterval(renderClock, 1000);
    renderClock();
    lastState = state;
  }
  renderStatus();
  syncLive();
}

// One line above the game: what is happening, or the first device that is not ready.
function renderStatus() {
  const { state } = S.live;
  let cls = "wait", text = "أفحص الأجهزة…";
  if (state === "running") { cls = "ok"; text = "الترجمة شغّالة على التلفزيون"; }
  else if (state === "starting") text = STEP_TEXT[S.live.step] || "أجهّز كل شي…";
  else if (state === "stopping") text = "أوقّف وأمسح الشاشة…";
  else {
    const nodes = ["ps5", "pc", "tv"].map(id => S.nodes[id]).filter(Boolean);
    const bad = nodes.find(n => n.cls === "bad"), warn = nodes.find(n => n.cls === "warn");
    if (bad) { cls = "bad"; text = `${bad.name}: ${bad.text}`; }
    else if (warn) { cls = "warn"; text = `${warn.name}: ${warn.text}`; }
    else if (nodes.length === 3 && nodes.every(n => n.cls === "ok")) { cls = "ok"; text = "جاهز للترجمة · كل الأجهزة متصلة"; }
  }
  $("#status").className = `status ${cls}`;
  $("#status-t").textContent = text;
}

function renderStats(st) {
  const set = (n, v) => {
    if (n.textContent !== String(v)) {
      n.textContent = v;
      n.classList.remove("bump");
      void n.offsetWidth;
      n.classList.add("bump");
    }
  };
  const ms = st.latency_ms != null ? `${(st.latency_ms / 1000).toFixed(2)}s` : "—";
  set($("#s-ms"), ms);
  $$("[data-speed]").forEach(n => { n.textContent = ms; });
  set($("#s-lines"), st.lines ?? 0);
  set($("#s-fps"), st.fps ?? "—");
  if (st.latency_ms != null) {
    S.latencies.push(st.latency_ms);
    if (S.latencies.length > 60) S.latencies.shift();
    drawSpark();
  }
}
function drawSpark() {
  const v = S.latencies;
  const line = $("#spark .line"), area = $("#spark .area");
  if (v.length < 2) { line.setAttribute("d", ""); area.setAttribute("d", ""); return; }
  const top = Math.max(400, ...v), W = 200, H = 40;
  const pts = v.map((y, i) => [W - (i / (v.length - 1)) * W, H - 3 - (y / top) * (H - 6)]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  line.setAttribute("d", d);
  area.setAttribute("d", `${d} L${pts[pts.length - 1][0]} ${H} L${pts[0][0]} ${H} Z`);
}

const PEN = '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m14 8 3 3"/>';
const PLUS = '<path d="M12 5v14M5 12h14"/>';
const BOOKS = '<path d="M5 19.5v-14A1.5 1.5 0 0 1 6.5 4H9v15.5M9 4h3.5v15.5"/><path d="m14.6 5.3 3-.8 3.3 14.4-3 .8z"/>';

function feedItem(item) {
  const li = el("li");
  const ar = el("div", "ar", item.ar), en = el("div", "en", item.en);
  const edit = el("button", "edit");
  edit.title = "عدّل الترجمة";
  edit.setAttribute("aria-label", "عدّل الترجمة");
  edit.append(svg(PEN));
  edit.addEventListener("click", () => editLine(li, item));
  li.append(ar, edit, en);
  return li;
}
function editLine(li, item) {
  if (li.classList.contains("editing")) return;
  li.classList.add("editing");
  const input = el("input");
  input.value = item.ar;
  input.dir = "rtl";
  input.maxLength = 300;
  const ok = el("button", "mini", "احفظ"), no = el("button", "mini ghost", "إلغاء");
  const close = () => li.replaceWith(Object.assign(feedItem(item), { className: item.fixed ? "fixed" : "" }));
  const save = async () => {
    const value = input.value.trim();
    if (!value || value === item.ar) return close();
    try {
      await api("/api/line/fix", { en: item.en, ar: value, profile: S.live.profile || S.selected });
      item.ar = value;
      item.fixed = true;
      toast("انحفظت. المرة الجاية تطلع بصياغتك ✓");
    } catch (e) { toast(e.message); }
    close();
  };
  ok.addEventListener("click", save);
  no.addEventListener("click", close);
  input.addEventListener("keydown", e => { if (e.key === "Enter") save(); if (e.key === "Escape") close(); });
  li.replaceChildren(input, ok, no, el("div", "en", item.en));
  input.focus();
  input.select();
}

function addLine(item, animate = true) {
  // a new line shows over the picture for its reading time; lines from before the page opened do not
  if (S.still) playLine(item.en, item.ar, 0);
  else if (animate && S.live.state !== "idle") playLine(item.en, item.ar, readTime(item.ar));
  $("#rest-line").textContent = item.ar;
  const sar = $("#second-ar");
  sar.textContent = item.ar;
  $("#second-en").textContent = item.en;
  if (animate) { sar.classList.remove("in"); void sar.offsetWidth; sar.classList.add("in"); }
  const feed = $("#feed");
  $(".empty", feed)?.remove();
  feed.prepend(feedItem(item));
  while (feed.children.length > 30) feed.lastChild.remove();
  $("#feed-count").textContent = `${feed.children.length} سطر`;
  document.body.classList.add("has-lines");
}

$("#orb").addEventListener("click", async () => {
  try {
    if (S.live.state === "idle") {
      S.live = { ...S.live, state: "starting", step: "models", error: "" };
      renderLive();
      remember(S.selected);
      renderShelf();
      await api("/api/start", { profile: S.selected });
    } else if (S.live.state === "running" || S.live.state === "starting") {
      await api("/api/stop", {});
    }
  } catch (e) {
    S.live = { state: "idle", error: e.message };
    renderLive();
  }
});

/* ---- the three devices: circles in the bar (cinema), a card (shelf, and on phones) ---- */

const NODE_NAME = { ps5: () => (S.kind === "xbox" ? "الإكس بوكس" : "الـPS5"), pc: () => (S.app ? "الجوال" : "اللابتوب"), tv: () => "التلفزيون" };
function setNode(id, cls, text, extra) {
  const name = NODE_NAME[id]();
  S.nodes[id] = { cls, text, name };
  $$(`[data-node="${id}"]`).forEach(n => {
    n.classList.remove("ok", "warn", "bad", "wait");
    n.classList.add(cls);
    n.title = `${name}: ${text}${extra ? ` (${extra})` : ""}`;
  });
  $$(`[data-node-t="${id}"]`).forEach(n => { n.textContent = text; });
  renderStatus();
}
async function runCheck() {
  if (!S.check) ["ps5", "pc", "tv"].forEach(id => setNode(id, "wait", "أفحص…"));
  try { S.check = await api("/api/check"); } catch { return; }
  const c = S.check;
  const by = Object.fromEntries(c.items.map(i => [i.id, i]));
  const ps5ok = by.ps5.ok && by.link.ok;
  const xbox = c.kind === "xbox";
  S.kind = xbox ? "xbox" : "ps5";
  $$('[data-node-n="ps5"]').forEach(n => { n.textContent = NODE_NAME.ps5(); });
  setNode("ps5", ps5ok ? "ok" : "bad",
    ps5ok ? `${c.ps5_name || "متصل"} · ${xbox ? "مربوط" : "شغّال"}` : (!by.link.ok ? by.link.hint : by.ps5.hint), c.ps5_host);
  setNode("pc", !by.models.ok ? "bad" : by.power.ok ? "ok" : "warn",
    !by.models.ok ? by.models.hint : by.power.ok ? "على الشاحن · يقرأ ويترجم" : "على البطارية: الترجمة أبطأ. شبّك الشاحن.");
  setNode("tv", by.tv.ok ? "ok" : "bad", by.tv.ok ? `${c.tv_model || "متصل"} · جاهز` : by.tv.hint, c.tv_ip);
  if ($("#view-guide").classList.contains("on")) {
    const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);
    if (!typing && !S.task?.running) renderGuide(); else renderSteps();
  }
}
$("#recheck").addEventListener("click", () => { S.check = null; runCheck(); });
$$("[data-goto-devices]").forEach(b => b.addEventListener("click", () => {
  if (document.body.dataset.view !== "home") go("home");
  if (matchMedia("(max-width: 640px)").matches) $("#devices").scrollIntoView({ behavior: "smooth", block: "center" });
  else document.body.classList.toggle("show-devices");
}));
document.addEventListener("pointerdown", e => {
  if (!e.target.closest("#devices, [data-goto-devices]")) document.body.classList.remove("show-devices");
});

/* ---- the chosen game: its picture, its name, what it is set to ---- */

const BG_TEXT = { cover: "يغطّي الإنجليزي", plate: "خلفية داكنة", none: "فوق الإنجليزي" };
const hasArabic = t => /[؀-ۿ]/.test(t || "");
const summaryOf = p => ({ names: (p.glossary || []).length, font: p.font, background: p.background });

async function renderGame() {
  const p = S.profiles.find(x => x.name === S.selected);
  if (!p) return;
  const a = UI.art(p.name, p.title);
  UI.backdrop(a);
  const ar = a?.ar || (hasArabic(p.title) ? p.title : "");
  const en = hasArabic(p.title) ? (a?.en || "") : p.title;
  const title = $("#g-pick"), logo = $("#g-logo");
  $("#g-en").textContent = en;
  $("#g-name").textContent = ar;
  if (a?.logo) { logo.src = a.logo; logo.alt = en; logo.hidden = false; } else { logo.hidden = true; logo.removeAttribute("src"); }
  title.classList.toggle("has-logo", !!a?.logo);
  title.classList.toggle("latin-only", !ar);
  title.classList.toggle("arabic-only", !en && !a?.logo);
  title.title = S.live.state === "idle" ? "غيّر اللعبة" : p.title;
  renderMeta(p.name);
  if (!S.summary[p.name]) {
    try { S.summary[p.name] = summaryOf(await api(`/api/profile?name=${encodeURIComponent(p.name)}`)); } catch { return; }
    renderMeta(p.name);
  }
}
function renderMeta(name) {
  if (name !== S.selected) return;
  const s = S.summary[name];
  const chips = [];
  if (s) {
    chips.push(s.names ? `${s.names} اسم` : "بدون أسماء");
    const f = FONTS.find(x => x.id === s.font);
    if (f) chips.push(`خط ${f.name}`);
    if (BG_TEXT[s.background]) chips.push(BG_TEXT[s.background]);
  }
  $("#g-meta").replaceChildren(...chips.map(t => el("span", "chip", t)));
}

function gameTile(p, tag = "button") {
  const a = UI.art(p.name, p.title);
  const t = el(tag, `tile ${a ? "" : "plain"} ${p.name === S.selected ? "sel" : ""}`);
  t.title = p.title;
  if (a) t.style.backgroundImage = `url("${a.cover}")`;
  else t.append(el("b", "", p.title));
  if (tag === "button") t.append(el("span", "cap", a?.en || p.title));
  return t;
}
function addTile(tag, icon, label) {
  const t = el(tag, "tile add glass");
  t.append(svg(icon), el("span", "", label));
  return t;
}
// The shelf keeps the games in the order they were last played, as a console does;
// picking one does not move it, starting the translation does.
function remember(name) {
  S.recent = [name, ...S.recent.filter(n => n !== name)].slice(0, 40);
  if (!S.still) UI.store.set("tarjim-recent", JSON.stringify(S.recent));
}
function shelfOrder() {
  const rank = p => (p.name === "default" ? 1e6 : S.recent.includes(p.name) ? S.recent.indexOf(p.name) : 1e3);
  return [...S.profiles].sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title));
}
function renderShelf() {
  const tiles = shelfOrder().map(p => {
    const t = gameTile(p);
    t.addEventListener("click", () => pickGame(p.name));
    return t;
  });
  const add = addTile("button", PLUS, "لعبة");
  add.title = "أضف لعبة";
  add.addEventListener("click", newGame);
  const lib = addTile("button", BOOKS, "المكتبة");
  lib.title = "قوائم أسماء جاهزة لألعاب ما فيها عربي";
  lib.addEventListener("click", openLibrary);
  $("#shelf").replaceChildren(...tiles, add, lib);
}
function pickGame(name) {
  if (S.live.state !== "idle" && name !== S.selected) return toast("وقّف الترجمة أول، بعدين غيّر اللعبة");
  selectGame(name);
}
function newGame() {
  $("#new-title").value = "";
  $("#dlg-new").showModal();
}

// My games, as covers: from the game's name on the home page (and on a phone, where the shelf is small)
function openGames() {
  if (S.live.state !== "idle") return toast("وقّف الترجمة أول، بعدين غيّر اللعبة");
  const d = $("#dlg-games");
  const items = shelfOrder().map(p => {
    const b = el("button", "pick");
    b.append(gameTile(p, "div"), el("b", "", p.title));
    b.addEventListener("click", () => { d.close(); selectGame(p.name); });
    return b;
  });
  const add = el("button", "pick");
  add.append(addTile("div", PLUS, "لعبة جديدة"), el("b", "", "أضف لعبة"));
  add.addEventListener("click", () => { d.close(); newGame(); });
  const lib = el("button", "pick");
  lib.append(addTile("div", BOOKS, "المكتبة"), el("b", "", "من المكتبة"));
  lib.addEventListener("click", () => { d.close(); openLibrary(); });
  $("#games-grid").replaceChildren(...items, add, lib);
  d.showModal();
  $(".sheet", d).focus();
}
$("#g-pick").addEventListener("click", openGames);
$$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
// a tap outside a sheet closes it
for (const d of $$("dialog.wide")) d.addEventListener("click", e => { if (e.target === d) d.close(); });

/* --------------------------------- settings --------------------------------- */

const FONTS = [
  { id: "harmattan", name: "حرمتان", css: "Harmattan" },
  { id: "harmattan-bold", name: "حرمتان عريض", css: "Harmattan Bold" },
  { id: "noto-naskh", name: "نسخ", css: "Noto Naskh" },
  { id: "noto-sans-arabic", name: "عصري", css: "Tarjim UI" },
];

function fillGames() {
  for (const sel of $$(".game-select")) {
    sel.replaceChildren(...S.profiles.map(p => {
      const o = el("option", "", p.title);
      o.value = p.name;
      return o;
    }));
    if (S.profiles.some(p => p.name === S.selected)) sel.value = S.selected;
  }
  renderShelf();
}
function selectGame(name) {
  if (!S.profiles.some(p => p.name === name)) name = (S.profiles.find(p => p.name !== "default") || S.profiles[0])?.name;
  if (!name) return;
  S.selected = name;
  if (!S.still) UI.store.set("tarjim-game", name);
  $$(".game-select").forEach(sel => { sel.value = name; });
  renderShelf();
  renderGame();
  if (["look", "names"].includes(document.body.dataset.view)) loadProfile();
}
$$(".game-select").forEach(sel => sel.addEventListener("change", e => selectGame(e.target.value)));

let saveTimer, previewTimer, pending = {};
function save(patch, quiet) {
  Object.assign(S.profile, patch);
  Object.assign(pending, patch);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const send = pending;
    pending = {};
    try {
      const fresh = await api("/api/profile", { name: S.profile.name, ...send });
      S.profile.font_size = fresh.font_size;
      S.summary[S.profile.name] = summaryOf(S.profile);
      renderMeta(S.profile.name);
      if (!quiet) toast(S.live.state === "running" ? "تطبّق على التلفزيون ✓" : "انحفظ ✓");
    } catch (e) { toast(e.message); }
  }, 350);
  refreshPreview();
}
function refreshPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    const p = S.profile;
    if (!p) return;
    const o = {
      font: p.font, font_size: p.font_auto ? null : p.font_size, background: p.background,
      plate_opacity: p.plate_opacity, text_color: p.text_color, effect: p.effect, y_offset: p.y_offset,
    };
    const q = new URLSearchParams({ name: p.name, text: $("#preview-text").value, o: JSON.stringify(o), t: Date.now() });
    const img = new Image();
    $("#preview-load").classList.add("on");
    img.onload = () => { $("#preview").src = img.src; $("#preview-load").classList.remove("on"); };
    img.onerror = () => $("#preview-load").classList.remove("on");
    img.src = withKey(`/api/preview?${q}`);
  }, 180);
}

async function loadProfile() {
  const name = S.selected || "default";
  let p;
  try { p = S.profile = await api(`/api/profile?name=${encodeURIComponent(name)}`); } catch (e) { return toast(e.message); }
  S.summary[name] = summaryOf(p);
  renderMeta(name);

  $("#fonts").replaceChildren(...FONTS.map(f => {
    const b = el("button", `font ${p.font === f.id ? "on" : ""}`);
    const sample = el("b", "", "مرحباً بك");
    sample.style.fontFamily = `"${f.css}"`;
    b.append(sample, el("small", "", f.name));
    b.addEventListener("click", () => {
      $$(".font").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      save({ font: f.id });
    });
    return b;
  }));

  const size = $("#size"), auto = $("#size-auto");
  auto.checked = p.font_auto;
  size.value = p.font_size || 40;
  size.disabled = p.font_auto;
  const sizeLabel = () => $("#size-val").textContent = auto.checked
    ? (p.font_size ? `الحجم ${p.font_size} (تلقائي)` : "الحجم تلقائي") : `الحجم ${size.value}`;
  sizeLabel();
  auto.onchange = () => {
    size.disabled = auto.checked;
    S.profile.font_auto = auto.checked;
    save({ font_size: auto.checked ? null : Number(size.value) });
    sizeLabel();
  };
  size.oninput = () => { S.profile.font_auto = false; S.profile.font_size = Number(size.value); sizeLabel(); refreshPreview(); };
  size.onchange = () => save({ font_size: Number(size.value) });

  renderLook();
  renderRegion();
  $("#typewriter").checked = p.settle_ms >= 200;
  $("#typewriter").onchange = e => save({ settle_ms: e.target.checked ? 280 : 40 });
  $("#screen-text-box").hidden = p.screen_text == null;  // GTA VI only
  $("#screen-text").checked = !!p.screen_text;
  $("#screen-text").onchange = e => save({ screen_text: e.target.checked });
  const read = $("#read");
  read.value = p.read_scale;
  const readLabel = () => $("#read-val").textContent = Number(read.value) === 1 ? "عادي" : `×${Number(read.value).toFixed(1)}`;
  readLabel();
  read.oninput = readLabel;
  read.onchange = () => save({ read_scale: Number(read.value) });

  renderNames();
  refreshPreview();
  UI.ranges();
  loadGemini();
}

/* ---- the stronger translator (Gemini): the key, the switch, each game's Arabic ---- */
const GEMINI_STATE = { ok: "شغّال ✓", waiting: "خلص الحد حالياً، يرجع لحاله", bad_key: "المفتاح مرفوض" };
function showGemini(g) {
  S.gemini = g;
  $("#gemini-card").hidden = false;
  $("#gemini-on").checked = g.enabled;
  $("#gemini-wait").checked = g.wait !== false;
  $("#gemini-on").disabled = !g.has_key;
  $("#gemini-model").value = g.model;
  $("#gemini-forget").hidden = !g.has_key;
  $("#gemini-key").placeholder = g.has_key ? `محفوظ (آخره ${g.tail || "…"})` : "AIza...";
  $("#gemini-note").textContent = g.stored ? `محفوظ من Gemini: ${g.stored} سطر.` : "";
  const st = g.status;
  const any = g.has_key || (g.extra || []).length > 0;
  $("#gemini-on").disabled = !any;
  $("#gemini-state").textContent = !any ? "ما فيه مفتاح" : !g.enabled ? "مطفي"
    : st ? `${st.spent ? "خلص الحد اليومي" : GEMINI_STATE[st.state] || ""}${st.done ? ` · ${st.done} سطر` : ""}` : "جاهز";
  // the other keys, each with its own state while translating
  const live = Object.fromEntries(((st && st.providers) || []).map(p => [p.id, p]));
  $("#extra-list").replaceChildren(...(g.extra || []).map(x => {
    const row = el("div", "row extra-row");
    const p = live[x.id];
    const state = p ? ` · ${GEMINI_STATE[p.state] || ""}${p.done ? ` · ${p.done} سطر` : ""}` : "";
    row.append(el("b", "", x.name), el("span", "muted", `${x.model} · آخره ${x.tail}${state}`));
    const del = el("button", "textbtn", "احذف");
    del.onclick = async () => {
      try { showGemini(await api("/api/gemini/extra/remove", { id: x.id })); } catch (e) { toast(e.message); }
    };
    row.append(del);
    return row;
  }));
}
/* ---- catching up: the lines already seen, sent to the stronger translator in large batches ---- */
function showBackfill(b) {
  const go = $("#backfill-go"), text = $("#backfill-text"), meter = $("#backfill-meter");
  go.textContent = b.running ? "وقّف" : "ابدأ التحسين";
  go.disabled = false;
  meter.hidden = !b.running;
  if (b.running) {
    const pct = b.total ? Math.round(b.done * 100 / b.total) : 0;
    $("#backfill-fill").style.width = pct + "%";
    text.textContent = b.total ? b.done + " من " + b.total + " سطر" : "أجهّز القائمة…";
    $("#backfill-count").textContent = "";
    return;
  }
  if (b.error) text.textContent = b.error + (b.stored ? " (انحفظ " + b.stored + " سطر)" : "");
  else if (b.total && b.done >= b.total) text.textContent = "خلص ✓ انحفظ " + b.stored + " سطر" + (b.skipped ? "، و" + b.skipped + " ما انترجم" : "");
  else if (b.done) text.textContent = "وقف عند " + b.done + " من " + b.total + ". انحفظ " + b.stored + " سطر.";
  else text.textContent = "";
  if (b.pending != null) {
    $("#backfill-count").textContent = b.pending ? b.pending + " سطر ينتظر" : "ما فيه سطور تنتظر";
    go.disabled = !b.pending;
  }
}
async function loadBackfill() {
  const name = S.profile.name;
  try { showBackfill(await api("/api/gemini/backfill?profile=" + encodeURIComponent(name))); } catch { return; }
  $("#backfill-go").onclick = async () => {
    const go = $("#backfill-go");
    go.disabled = true;
    try {
      if (go.textContent === "وقّف") await api("/api/gemini/backfill/stop", {});
      else {
        const r = await api("/api/gemini/backfill", { profile: name });
        if (!r.ok) { toast(r.error); go.disabled = false; return; }
        showBackfill(r);
      }
    } catch (e) { toast(e.message); go.disabled = false; }
  };
}

function wireExtraKeys() {
  const preset = $("#extra-preset"), model = $("#extra-model"), base = $("#extra-base");
  const sync = () => {
    const p = (S.gemini.presets || {})[preset.value] || {};
    model.value = p.model || "";
    base.hidden = !p.needs_base;
  };
  preset.onchange = sync;
  sync();
  $("#extra-add").onclick = async () => {
    const btn = $("#extra-add");
    btn.disabled = true;
    try {
      const r = await api("/api/gemini/extra", {
        preset: preset.value, key: $("#extra-key").value.trim(), model: model.value.trim(), base: base.value.trim(),
      });
      if (!r.ok) return toast(r.error);
      $("#extra-key").value = "";
      showGemini(r);
      toast(`انضاف المفتاح ✓ «${r.sample}»`);
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
}
async function loadGemini() {
  if (S.remote || S.app) return;  // the key is typed on the laptop only
  let g;
  try { g = await api("/api/gemini"); } catch { return; }
  showGemini(g);
  wireExtraKeys();
  loadBackfill();
  $$("#dialect button").forEach(b => {
    b.classList.toggle("on", b.dataset.v === S.profile.dialect);
    b.onclick = () => {
      $$("#dialect button").forEach(x => x.classList.toggle("on", x === b));
      save({ dialect: b.dataset.v });
    };
  });
  $("#gemini-save").onclick = async () => {
    const key = $("#gemini-key").value.trim(), model = $("#gemini-model").value.trim();
    if (!key && !g.has_key) return toast("الصق المفتاح أول.");
    const btn = $("#gemini-save");
    btn.disabled = true;
    try {
      const r = await api("/api/gemini/test", { key, model });
      if (!r.ok) return toast(r.error);
      const body = { model, enabled: true };
      if (key) body.key = key;
      g = await api("/api/gemini", body);
      $("#gemini-key").value = "";
      showGemini(g);
      toast(`المفتاح شغّال ✓ «${r.sample}»`);
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
  $("#gemini-wait").onchange = async e => {
    try { showGemini(g = await api("/api/gemini", { wait: e.target.checked })); toast("انحفظ ✓"); } catch (err) { toast(err.message); }
  };
  $("#gemini-on").onchange = async e => {
    try { showGemini(g = await api("/api/gemini", { enabled: e.target.checked })); toast("انحفظ ✓"); } catch (err) { toast(err.message); }
  };
  $("#gemini-forget").onclick = async () => {
    if (!await ask("حذف المفتاح", "ينحذف المفتاح من هذا اللابتوب. الترجمات المحفوظة تبقى.", "احذف")) return;
    try { showGemini(g = await api("/api/gemini", { key: "" })); } catch (err) { toast(err.message); }
  };
}
/* ---- small updates: only what changed is downloaded (tarjim/update.py) ---- */
async function checkUpdate() {
  if (S.remote || S.app || S.still) return;  // the laptop's own window only
  let u;
  try { u = await api("/api/update"); } catch { return; }
  if (!u.available) return;
  const bar = $("#update-bar"), go = $("#update-go");
  const mb = u.size ? ` (${Math.max(1, Math.round(u.size / 1e6))} ميقا)` : "";
  bar.hidden = false;
  $("#update-later").onclick = () => { bar.hidden = true; };
  if (u.needs_installer) {
    $("#update-text").textContent = `النسخة ${u.latest} نزلت، وتحتاج ملف التثبيت الكامل هالمرة.`;
    go.textContent = "افتح صفحة التحميل";
    go.onclick = () => window.open(u.page, "_blank");
    return;
  }
  $("#update-text").textContent = `تحديث جديد: ${u.latest}${mb}`;
  go.onclick = async () => {
    go.disabled = true;
    $("#update-later").hidden = true;
    updateProgress({ stage: "download", pct: 0 });
    try {
      const r = await api("/api/update/apply", {});
      if (!r.ok) updateProgress({ stage: "error", error: r.error });
    } catch (e) { updateProgress({ stage: "error", error: e.message }); }
  };
}
/* The update as it goes: the share downloaded, then the program closing and opening again. */
function updateProgress(u) {
  const bar = $("#update-bar"), text = $("#update-text"), meter = $("#update-meter");
  bar.hidden = false;
  if (u.stage === "error") {
    text.textContent = u.error;
    meter.hidden = true;
    $("#update-go").disabled = false;
    $("#update-later").hidden = false;
    return;
  }
  $("#update-go").hidden = true;
  $("#update-later").hidden = true;
  meter.hidden = false;
  $("#update-fill").style.width = `${u.pct || 0}%`;
  if (u.stage === "done") {
    text.textContent = u.restart ? `تم التحديث إلى ${u.version}. يقفل ويفتح لحاله الحين…`
      : `تم التحديث إلى ${u.version}. اقفل ترجِم وافتحه من جديد.`;
  } else {
    text.textContent = `أنزّل التحديث… ${u.pct || 0}٪`;
  }
}

/* A line Gemini rewrote: the list and the big second-screen line take its new Arabic. */
function betterLine(item) {
  for (const li of $$("#feed li")) {
    if ($(".en", li)?.textContent === item.en && $(".ar", li)) { $(".ar", li).textContent = item.ar; break; }
  }
  if ($("#second-en").textContent === item.en) {
    $("#second-ar").textContent = item.ar;
    $("#rest-line").textContent = item.ar;
  }
}
$("#preview-text").addEventListener("input", refreshPreview);

/* ---- look: presets, background, colour, effect, position ---- */

const COLORS = [
  { v: "auto", name: "تلقائي (لون ترجمة اللعبة)" },
  { v: "#ffffff", name: "أبيض" }, { v: "#ffe14d", name: "أصفر" }, { v: "#7cf3ff", name: "سماوي" },
  { v: "#c9ffb0", name: "أخضر فاتح" }, { v: "#ffb3d1", name: "وردي" }, { v: "#ffb36b", name: "برتقالي" },
];
const PRESETS = [
  { name: "كلاسيك", hint: "مثل ترجمة اللعبة", set: { background: "cover", effect: "shadow", text_color: "auto", plate_opacity: 0.62 },
    mini: "color:#f2f7f1;text-shadow:2px 2px 4px #000" },
  { name: "سينما", hint: "خلفية داكنة", set: { background: "plate", effect: "none", text_color: "#ffffff", plate_opacity: 0.75 },
    mini: "color:#fff;background:rgba(0,0,0,.72)" },
  { name: "أصفر واضح", hint: "ينقرأ على أي مشهد", set: { background: "cover", effect: "outline", text_color: "#ffe14d", plate_opacity: 0.62 },
    mini: "color:#ffe14d;-webkit-text-stroke:3px #000;paint-order:stroke fill" },
  { name: "خفيف", hint: "فوق الإنجليزي", set: { background: "none", effect: "outline", text_color: "#ffffff", plate_opacity: 0.62 },
    mini: "color:#fff;-webkit-text-stroke:3px #000;paint-order:stroke fill" },
];
const samePreset = (p, set) => Object.entries(set).every(([k, v]) => k === "plate_opacity" || p[k] === v);

function renderLook() {
  const p = S.profile;
  $("#presets").replaceChildren(...PRESETS.map(pr => {
    const b = el("button", `preset ${samePreset(p, pr.set) ? "on" : ""}`);
    const mini = el("div", "mini");
    const word = el("span", "", "مرحباً");
    word.style.cssText = pr.mini;
    mini.append(word);
    b.append(mini, el("b", "", pr.name), el("small", "", pr.hint));
    b.addEventListener("click", () => { save({ ...pr.set }); renderLook(); });
    return b;
  }));

  $$("#bg button").forEach(b => {
    b.classList.toggle("on", b.dataset.v === p.background);
    b.onclick = () => { save({ background: b.dataset.v }); renderLook(); };
  });
  $("#plate-row").classList.toggle("off", p.background !== "plate");
  const plate = $("#plate");
  plate.value = p.plate_opacity;
  plate.oninput = () => { S.profile.plate_opacity = Number(plate.value); refreshPreview(); };
  plate.onchange = () => save({ plate_opacity: Number(plate.value) });

  const known = COLORS.some(c => c.v === p.text_color);
  const sw = COLORS.map(c => {
    const b = el("button", `sw ${c.v === "auto" ? "auto" : ""} ${p.text_color === c.v ? "on" : ""}`);
    if (c.v !== "auto") b.style.background = c.v;
    b.title = c.name;
    b.setAttribute("aria-label", c.name);
    b.addEventListener("click", () => { save({ text_color: c.v }); renderLook(); });
    return b;
  });
  const pick = el("label", `sw pick ${known ? "" : "on"}`);
  pick.title = "لون ثاني";
  const input = el("input");
  input.type = "color";
  input.value = known || p.text_color === "auto" ? "#ffffff" : p.text_color;
  input.addEventListener("input", () => { S.profile.text_color = input.value; refreshPreview(); });
  input.addEventListener("change", () => { save({ text_color: input.value }); renderLook(); });
  pick.append(input);
  $("#colors").replaceChildren(...sw, pick);
  $("#color-val").textContent = p.text_color === "auto" ? "تلقائي من اللعبة" : (COLORS.find(c => c.v === p.text_color)?.name || p.text_color);

  $$("#effect button").forEach(b => {
    b.classList.toggle("on", b.dataset.v === p.effect);
    b.onclick = () => { save({ effect: b.dataset.v }); renderLook(); };
  });

  const pos = $("#pos");
  pos.value = p.y_offset;  // the slider runs right-to-left, so its right end (smallest value) is "higher"
  const posLabel = () => {
    const v = Number(pos.value);
    $("#pos-val").textContent = v === 0 ? "مكان ترجمة اللعبة" : v < 0 ? `أعلى بـ${-v}` : `أسفل بـ${v}`;
  };
  posLabel();
  pos.oninput = () => { S.profile.y_offset = Number(pos.value); posLabel(); refreshPreview(); };
  pos.onchange = () => save({ y_offset: Number(pos.value) });
  UI.ranges();
}

$("#try-tv").addEventListener("click", async e => {
  const b = e.currentTarget;
  b.disabled = true;
  try {
    await api("/api/tv/try", { name: S.profile.name, text: $("#preview-text").value });
    toast("شوف التلفزيون: السطر يطلع 8 ثواني");
    setTimeout(() => { b.disabled = false; }, 9000);
  } catch (err) { toast(err.message); b.disabled = false; }
});

/* ---- region: where on the screen subtitles are read ---- */

function renderRegion() {
  const stage = $("#region-stage"), box = $("#region-box");
  let r = { ...S.profile.region };
  const place = () => {
    box.style.left = `${r.left * 100}%`;
    box.style.top = `${r.top * 100}%`;
    box.style.width = `${(r.right - r.left) * 100}%`;
    box.style.height = `${(r.bottom - r.top) * 100}%`;
  };
  place();
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const MIN_W = 0.12, MIN_H = 0.08;

  box.onpointerdown = ev => {
    ev.preventDefault();
    const handle = ev.target.dataset?.h || "move";
    const rect = stage.getBoundingClientRect();
    const start = { x: ev.clientX, y: ev.clientY, ...r };
    box.classList.add("drag");
    box.setPointerCapture(ev.pointerId);
    box.onpointermove = mv => {
      const dx = (mv.clientX - start.x) / rect.width, dy = (mv.clientY - start.y) / rect.height;
      if (handle === "move") {
        const w = start.right - start.left, h = start.bottom - start.top;
        r.left = clamp(start.left + dx, 0, 1 - w); r.right = r.left + w;
        r.top = clamp(start.top + dy, 0, 1 - h); r.bottom = r.top + h;
      } else {
        if (handle.includes("w")) r.left = clamp(start.left + dx, 0, start.right - MIN_W);
        if (handle.includes("e")) r.right = clamp(start.right + dx, start.left + MIN_W, 1);
        if (handle.includes("n")) r.top = clamp(start.top + dy, 0, start.bottom - MIN_H);
        if (handle.includes("s")) r.bottom = clamp(start.bottom + dy, start.top + MIN_H, 1);
      }
      place();
    };
    box.onpointerup = box.onpointercancel = () => {
      box.classList.remove("drag");
      box.onpointermove = box.onpointerup = box.onpointercancel = null;
      const round = v => Math.round(v * 1000) / 1000;
      r = { left: round(r.left), top: round(r.top), right: round(r.right), bottom: round(r.bottom) };
      save({ region: r });
    };
  };
  $("#region-reset").onclick = () => {
    r = { left: 0.08, top: 0.7, right: 0.92, bottom: 1 };
    place();
    save({ region: null });
    S.profile.region = r;
  };
  const shot = () => {
    const img = new Image();
    img.onload = () => { $("#region-img").src = img.src; $("#region-hint").hidden = true; };
    img.onerror = () => { $("#region-img").removeAttribute("src"); $("#region-hint").hidden = false; };
    const box = $("#region-img").parentElement;
    img.src = withKey(`/api/frame?w=${frameWidth(box.clientWidth || innerWidth, box.clientHeight || 0)}&t=${Date.now()}`);
  };
  $("#region-shot").onclick = shot;
  shot();
}

/* ---- names ---- */

function renderNames() {
  const box = $("#names");
  const collect = () => {
    const glossary = $$(".name", box).map(r => ({ en: $(".en", r).value, ar: $(".ar", r).value, f: $(".fem", r).classList.contains("on") }));
    S.profile.glossary = glossary.filter(g => g.en.trim() && g.ar.trim());
    countNames();
    save({ glossary }, true);
  };
  box.replaceChildren(...S.profile.glossary.map(row => nameRow(row, collect)));
  $("#names-find").value = "";
  countNames();
  filterNames();
  $("#add-name").onclick = () => {
    $("#names-find").value = "";
    filterNames();
    const r = nameRow({ en: "", ar: "", f: false }, collect);
    box.prepend(r);
    $(".en", r).focus();
  };
}
function countNames() {
  const n = S.profile.glossary.length;
  $("#names-count").textContent = n ? `· ${n}` : "";
  $("#names-find-box").hidden = n < 12;  // a search box only where a list is long enough to need one
}
function filterNames() {
  const q = $("#names-find").value.trim().toLowerCase();
  let shown = 0;
  for (const r of $$(".name", $("#names"))) {
    const hit = !q || $(".en", r).value.toLowerCase().includes(q) || $(".ar", r).value.includes(q);
    r.hidden = !hit;
    shown += hit;
  }
  $("#names-none").hidden = !q || shown > 0;
}
$("#names-find").addEventListener("input", filterNames);

/* ---- the game library and game files ---- */

const LIB = { items: [] };
const hueOf = text => [...String(text)].reduce((h, c) => (h * 31 + c.codePointAt(0)) % 360, 7);
// A game without its own art (art/) gets a plain poster in a colour that suits its series. [hue, pattern, saturation]
const SERIES_LOOK = {
  "Red Dead": [14, 0, 1], "Grand Theft Auto": [292, 4, 1], "Yakuza": [338, 1, 1], "Like a Dragon": [338, 1, 1],
  "The Last of Us": [128, 2, .75], "Silent Hill": [222, 1, .4], "Max Payne": [206, 2, .7], "L.A. Noire": [38, 0, .7],
  "Bully": [22, 2, .9], "Baldur's Gate": [268, 1, 1], "Final Fantasy": [188, 4, 1], "Kingdom Come": [80, 3, .7],
  "Mafia": [30, 0, .85], "Persona": [356, 2, 1],
};

async function openLibrary() {
  const d = $("#dlg-library");
  $("#lib-find").value = "";
  $("#lib-grid").replaceChildren(el("p", "muted lib-wait", "أجيب القائمة…"));
  if (!d.open) d.showModal();
  $(".lib", d).focus();  // not the close button: its focus ring on opening looked like a warning
  try { LIB.items = (await api("/api/packs")).packs; } catch (e) {
    return $("#lib-grid").replaceChildren(el("p", "alert lib-wait", e.message));
  }
  renderLibrary();
}
function renderLibrary() {
  const q = $("#lib-find").value.trim().toLowerCase();
  const items = LIB.items.filter(p => !q || [p.title, p.title_ar, p.series].some(t => (t || "").toLowerCase().includes(q)));
  $("#lib-grid").replaceChildren(...(items.length ? items.map(libCard) : [el("p", "muted lib-wait", "ما لقيت لعبة بهالاسم.")]));
}
function libCard(p) {
  const card = el("article", `lib-card ${p.installed ? "on" : ""}`);
  const family = p.series || p.title;
  const [hue, , sat] = SERIES_LOOK[family] || [hueOf(family), 0, 1];
  card.style.setProperty("--hue", hue);
  card.style.setProperty("--sat", sat);
  const poster = el("div", "poster");
  const a = UI.art(p.id, p.title);
  if (a) {
    const img = el("img");
    img.src = a.cover;
    img.alt = "";
    img.loading = "lazy";
    poster.append(img);
  }
  poster.append(el("b", "poster-ar", p.title_ar || p.title));
  if (p.installed) poster.append(el("span", "poster-ok", "✓ مضافة"));
  const title = el("b", "lib-title", p.title);
  title.dir = "ltr";
  const foot = el("div", "lib-foot");
  const chips = el("div", "chips");
  chips.append(el("span", "chip", `${p.names} اسم`));
  if (p.women) chips.append(el("span", "chip f", `♀ ${p.women}`));
  let b;
  if (!p.installed) b = el("button", "btn small", "أضف");
  else if (p.new > 0) b = el("button", "btn small", `حدّث +${p.new}`);
  else { b = el("button", "btn soft small done", "مضافة ✓"); b.disabled = true; }
  b.title = !p.installed ? "تنضاف كلعبة جديدة بأسمائها" : p.new > 0 ? "أسماء جديدة تنضاف للعبتك، واللي كتبته أنت يبقى" : "";
  b.addEventListener("click", () => addFromLibrary(p, b));
  foot.append(chips, b);
  card.append(poster, el("div", "lib-top", [p.series, p.year].filter(Boolean).join(" · ")), title,
    el("span", "lib-ar", p.title_ar), el("p", "lib-note", p.note || ""), foot);
  return card;
}
async function addFromLibrary(p, b) {
  b.disabled = true;
  b.textContent = "…";
  try {
    const r = await api("/api/packs/add", { id: p.id });
    Object.assign(p, { installed: true, new: 0 });
    renderLibrary();
    afterPackIn(r);
  } catch (e) { toast(e.message); renderLibrary(); }
}
function packSummary(r) {
  if (r.created) return `انضافت «${r.title}» بـ${r.added} اسم${r.fixes ? ` و${r.fixes} سطر مترجم` : ""} ✓`;
  const parts = [];
  if (r.added) parts.push(`${r.added} اسم جديد`);
  if (r.fixes) parts.push(`${r.fixes} سطر مترجم`);
  if (r.updated) parts.push(`وتعدّلت كتابة ${r.updated}`);
  let msg = parts.length ? `انضاف ${parts.join(" و")} ✓` : "كل اللي في الملف موجود عندك ✓";
  if (r.kept) msg += ` · ${r.kept} خلّيتها مثل ما كتبتها`;
  return msg;
}
function afterPackIn(r) {
  toast(packSummary(r));
  delete S.summary[r.name];
  S.profiles = r.profiles;
  fillGames();
  selectGame(r.name);
}
$$(".lib-open").forEach(b => b.addEventListener("click", openLibrary));
$("#lib-close").addEventListener("click", () => $("#dlg-library").close());
$("#lib-find").addEventListener("input", renderLibrary);

$("#pack-export").addEventListener("click", async () => {
  const name = S.profile?.name || S.selected || "default";
  let r;
  try { r = await api(`/api/pack/export?name=${encodeURIComponent(name)}`); } catch (e) { return toast(e.message); }
  const text = JSON.stringify(r.pack, null, 1);
  if (window.TarjimApp?.shareFile) {  // the phone app: Android's share sheet (WhatsApp, Files, ...)
    TarjimApp.shareFile(r.file, text);
    return;
  }
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  a.download = r.file;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  toast(`انحفظ «${r.file}» في التنزيلات ✓`);
});
$("#pack-import").addEventListener("click", () => { $("#pack-file").value = ""; $("#pack-file").click(); });
$("#pack-file").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 8_000_000) return toast("الملف كبير مرة، وملفات ترجِم ما توصل لهالحجم.");
  let pack;
  try { pack = JSON.parse(await file.text()); } catch { pack = null; }
  if (!pack || pack.tarjim !== "tarjim-game") return toast("هذا مو ملف لعبة من ترجِم.");
  openImport(pack);
});
// The phone app hands over a file opened from WhatsApp or Files ("Open with ترجِم").
window.onPackFile = async text => {
  await booted;  // the list of games must be there before asking where the file goes
  let pack;
  try { pack = JSON.parse(text); } catch { pack = null; }
  if (!pack || pack.tarjim !== "tarjim-game") return toast("هذا مو ملف لعبة من ترجِم.");
  openImport(pack);
};
function openImport(pack) {
  const names = (pack.names || []).length, fixes = (pack.fixes || []).length;
  $("#imp-title").textContent = pack.title || pack.id || "لعبة";
  $("#imp-sub").textContent = [`${names} اسم`, fixes ? `${fixes} سطر مترجم` : "", pack.made ? `من ${pack.made}` : ""]
    .filter(Boolean).join(" · ");
  const own = S.profiles.find(p => p.name === pack.id);
  const opts = own ? [] : [[pack.id || "", `لعبة جديدة: ${pack.title || pack.id}`]];
  for (const p of S.profiles) opts.push([p.name, p.title]);
  const sel = $("#imp-into");
  sel.replaceChildren(...opts.map(([v, t]) => { const o = el("option", "", t); o.value = v; return o; }));
  sel.value = own ? own.name : pack.id || "";
  const d = $("#dlg-import");
  d.returnValue = "cancel";
  d.addEventListener("close", async () => {
    if (d.returnValue !== "ok") return;
    try { afterPackIn(await api("/api/pack/import", { pack, into: sel.value })); } catch (err) { toast(err.message); }
  }, { once: true });
  d.showModal();
}
function nameRow(row, collect) {
  const r = el("div", "name");
  const en = el("input", "en"), ar = el("input", "ar");
  const fem = el("button", `fem ${row.f ? "on" : ""}`, "♀"), del = el("button", "del", "×");
  en.value = row.en; en.placeholder = "Name"; en.maxLength = 60; en.setAttribute("aria-label", "الاسم بالإنجليزي");
  ar.value = row.ar; ar.placeholder = "الاسم بالعربي"; ar.maxLength = 60; ar.setAttribute("aria-label", "الاسم بالعربي");
  fem.title = "اسم امرأة (الأفعال تطلع مؤنثة)";
  fem.setAttribute("aria-pressed", String(!!row.f));
  del.title = "حذف";
  en.addEventListener("change", collect);
  ar.addEventListener("change", collect);
  fem.addEventListener("click", () => {
    fem.setAttribute("aria-pressed", String(fem.classList.toggle("on")));
    collect();
  });
  del.addEventListener("click", () => {
    r.classList.add("out");
    setTimeout(() => { r.remove(); collect(); }, 220);
  });
  r.append(en, el("span", "arrow", "←"), ar, fem, del);
  return r;
}

$("#new-game").addEventListener("click", newGame);
$("#dlg-new").addEventListener("close", async () => {
  const title = $("#new-title").value.trim();
  if ($("#dlg-new").returnValue !== "ok" || !title) return;
  try {
    const p = await api("/api/profile/new", { title });
    S.profiles.push({ name: p.name, title: p.title });
    fillGames();
    selectGame(p.name);
    toast("انضافت اللعبة ✓");
  } catch (e) { toast(e.message); }
});

/* ---------------------------------- phone ---------------------------------- */

async function loadPhone() {
  let v;
  try { v = await api("/api/phone"); } catch (e) { return toast(e.message); }
  $("#phone-on").checked = v.enabled;
  $("#phone-body").hidden = !v.enabled;
  if (v.enabled) {
    const qr = $("#phone-qr");
    if (v.pair_url) {
      const img = el("img");
      img.alt = "رمز QR لربط الجوال";
      img.src = `/api/phone/qr.svg?t=${Date.now()}`;
      qr.replaceChildren(img);
    } else {
      qr.replaceChildren(el("span", "muted", "اللابتوب مو متصل بالشبكة"));
    }
    $("#phone-url").textContent = v.url ? v.url.replace("http://", "") : "اللابتوب مو متصل بالشبكة";
    $("#phone-pin").textContent = v.pin;
  }
  const list = $("#phone-devices");
  if (!v.devices.length) return list.replaceChildren(el("p", "muted", "ما فيه جوال مربوط."));
  list.replaceChildren(...v.devices.map(d => {
    const row = el("div", "device");
    const info = el("div");
    info.append(el("b", "", d.name), el("span", "muted", `انربط ${new Date(d.added * 1000).toLocaleDateString("ar-SA-u-nu-latn")}`));
    const rm = el("button", "btn soft", "افصل");
    rm.addEventListener("click", async () => {
      if (!await ask("تفصل هذا الجوال؟", `«${d.name}» ما راح يقدر يتحكم بترجِم إلا بعد ما ينربط من جديد.`, "افصله")) return;
      await api("/api/phone/forget", { id: d.id }).catch(e => toast(e.message));
      loadPhone();
    });
    row.append(info, rm);
    return row;
  }));
}
$("#phone-on").addEventListener("change", async e => {
  try { await api("/api/phone", { enabled: e.target.checked }); } catch (err) { toast(err.message); }
  loadPhone();
});

/* ------------------------------ inside the phone app ------------------------------ */

function rest(on) {
  $("#rest-screen").hidden = !on;
  window.TarjimApp?.dim?.(on);
}
let restTap = 0;
$("#rest").addEventListener("click", () => {
  if (S.live.state === "idle") return toast("شغّل الترجمة أول");
  $("#rest-line").textContent = "";
  rest(true);
});
$("#rest-screen").addEventListener("click", () => {
  const now = Date.now();
  if (now - restTap < 450) rest(false);
  restTap = now;
});
window.onAppBack = () => {
  if (!$("#rest-screen").hidden) { rest(false); return true; }
  if (!$("#view-home").classList.contains("on")) { go("home"); return true; }
  return false;
};
window.onAppResume = () => {
  if ($("#view-setup").classList.contains("on")) loadSetup();
  S.check = null;
  runCheck();
};

async function loadSetup() {
  let v;
  try { v = await api("/api/setup"); } catch (e) { return toast(e.message); }
  // PlayStation or Xbox: one card each; the Xbox one is the same linking steps as on the laptop.
  const xboxCard = $("#su-xbox"), ps5Card = $("#su-ps5card");
  xboxCard.hidden = S.kind !== "xbox";
  ps5Card.hidden = S.kind === "xbox";
  if (S.kind === "xbox") { if (!xboxCard.firstChild || !$("#x-link", xboxCard)) xboxCard.replaceChildren(xboxGuide()); }
  else { xboxCard.replaceChildren(); if (!ps5Card.dataset.wired) { ps5Card.dataset.wired = "1"; kindSwitch(ps5Card); } }
  const tv = $("#su-tv"), ps5 = $("#su-ps5");
  if (document.activeElement !== tv) tv.value = v.tv;
  if (document.activeElement !== ps5) ps5.value = v.console;
  const linked = $("#su-linked");
  linked.className = `linked ${v.consoles.length ? "" : "none"}`;
  linked.textContent = v.consoles.length
    ? `الجوال مربوط بـ${v.consoles.join("، ")} ✓${v.account && v.account !== "محفوظ" ? ` (الحساب: ${v.account})` : ""}`
    : "الجوال مو مربوط بالـPS5 للحين.";
  $("#su-relogin").hidden = !v.account;
  $("#su-link").textContent = v.consoles.length ? "اربط من جديد" : "اربط الـPS5";
  $("#su-link").className = v.consoles.length ? "btn soft" : "btn";
  const free = !!window.TarjimApp?.batteryExempt?.();
  $("#su-batt-state").textContent = free ? "مسموح ✓" : "";
  $("#su-batt").hidden = free;
}
const ADDRESS = /^\d{1,3}(\.\d{1,3}){3}$/;
for (const [id, key] of [["#su-tv", "tv"], ["#su-ps5", "console"]]) {
  $(id).addEventListener("change", async e => {
    const value = e.target.value.trim();
    if (value && !ADDRESS.test(value)) return toast("العنوان يكون بهذا الشكل: 192.168.1.50");
    try { await api("/api/setup", { [key]: value }); toast("انحفظ ✓"); S.check = null; runCheck(); } catch (err) { toast(err.message); }
  });
}
$("#su-link").addEventListener("click", () => {
  const host = $("#su-ps5").value.trim();
  if (!ADDRESS.test(host)) return toast("اكتب عنوان الـPS5 أول");
  window.TarjimApp?.register?.(host, false);
});
$("#su-relogin").addEventListener("click", () => {
  const host = $("#su-ps5").value.trim();
  if (!ADDRESS.test(host)) return toast("اكتب عنوان الـPS5 أول");
  window.TarjimApp?.register?.(host, true);
});
$("#su-batt").addEventListener("click", () => window.TarjimApp?.askBattery?.());

/* ---------------------------------- guide ---------------------------------- */

const GUIDE = [
  { title: "كيف يشتغل", done: () => true },
  { title: "التلفزيون", done: c => !!c?.tv_ip },
  { title: "الجهاز", done: c => !!c?.ps5_user },
  { title: "ابدأ", done: () => false },
];

function renderSteps() {
  $("#steps").replaceChildren(...GUIDE.map((g, i) => {
    const b = el("button", `stepdot ${i === S.guideStep ? "on" : ""} ${g.done(S.check) && i !== S.guideStep ? "done" : ""}`);
    b.append(el("span", "n", g.done(S.check) && i !== 0 && i !== S.guideStep ? "✓" : String(i + 1)), el("b", "", g.title));
    b.addEventListener("click", () => { S.guideStep = i; renderGuide(); });
    return b;
  }));
  $("#g-pos").textContent = `${S.guideStep + 1} من ${GUIDE.length}`;
  $("#g-prev").disabled = S.guideStep === 0;
  $("#g-next").textContent = S.guideStep === GUIDE.length - 1 ? "للرئيسية" : "التالي";
}
$("#g-prev").addEventListener("click", () => { S.guideStep = Math.max(0, S.guideStep - 1); renderGuide(); });
$("#g-next").addEventListener("click", () => {
  if (S.guideStep === GUIDE.length - 1) return go("home");
  S.guideStep += 1;
  renderGuide();
});

function html(strings, ...values) {
  const t = document.createElement("template");
  t.innerHTML = String.raw(strings, ...values);
  const wrap = el("div");
  wrap.append(t.content);
  return wrap;
}
function logBox(id) {
  const box = el("div", "log");
  box.id = id;
  return box;
}
function pushLog(box, item) {
  if (!box) return;
  box.append(el("div", item.kind, item.msg));
  box.scrollTop = box.scrollHeight;
}

function renderGuide() {
  renderSteps();
  const body = $("#guide-body");
  const c = S.check || {};
  let view;

  if (S.guideStep === 0) {
    view = html`
      <h2>ترجِم في ثلاث خطوات</h2>
      <p class="lead">الـPS5 يبقى موصّل بالتلفزيون مثل ما هو، ولعبتك ويدك ما يتغيّر فيهم شي. ترجِم يشتغل جنبهم.</p>
      <div class="how">
        <div><span class="num">1</span><b>اللابتوب يشوف الصورة</b><p>ياخذ نسخة من صورة الـPS5 عن طريق الشبكة، بحساب ثاني عشان يدك تبقى شغّالة.</p></div>
        <div><span class="num">2</span><b>يقرأ ويترجم</b><p>يلقط السطر الإنجليزي أول ما يطلع، ويترجمه على اللابتوب نفسه بدون إنترنت.</p></div>
        <div><span class="num">3</span><b>التلفزيون يرسم العربي</b><p>تطبيق صغير على التلفزيون يحط العربي فوق مكان الإنجليزي. كل هذا في أقل من ربع ثانية.</p></div>
      </div>`;
  }

  if (S.guideStep === 1) {
    view = html`
      <h2>جهّز التلفزيون</h2>
      <p class="lead">مرة وحدة بس. نثبّت تطبيق صغير على التلفزيون يرسم الترجمة، وتقدر تشيله بأي وقت.</p>
      <ol class="todo">
        <li><div>فعّل خيارات المطوّر: <span class="path">الإعدادات ‹ النظام ‹ حول ‹ رقم الإصدار</span> واضغط عليه 7 مرات.</div></li>
        <li><div>فعّل التصحيح: <span class="path">الإعدادات ‹ النظام ‹ خيارات المطوّر ‹ تصحيح أخطاء USB</span></div></li>
        <li><div>شوف عنوان التلفزيون: <span class="path">الإعدادات ‹ الشبكة والإنترنت ‹ اسم شبكتك ‹ عنوان IP</span> واكتبه هنا:</div></li>
      </ol>
      <div class="action">
        <input class="text" id="tv-ip" placeholder="192.168.1.50" inputmode="decimal" maxlength="15" aria-label="عنوان التلفزيون">
        <button class="btn" id="tv-setup">جهّز التلفزيون</button>
        <button class="btn soft" id="tv-test">اعرض مربعات الاختبار</button>
      </div>
      <div class="note">أول مرة بتطلع رسالة على التلفزيون: «السماح بتصحيح الأخطاء؟» اختر <b>السماح دائماً</b> ثم موافق.</div>
      <div id="tv-result"></div>`;
    view.append(logBox("tv-log"));
    $("#tv-ip", view).value = c.tv_ip || "";
    if (c.tv_ip) $("#tv-result", view).append(el("div", "note good", `التلفزيون مجهّز${c.tv_model ? ` (${c.tv_model})` : ""}. لو تحدّث ترجِم أو تغيّر عنوان التلفزيون، اضغط «جهّز التلفزيون» مرة ثانية.`));
    $("#tv-setup", view).addEventListener("click", async () => {
      $("#tv-log").replaceChildren();
      $("#tv-result").replaceChildren();
      try { await api("/api/tv/setup", { ip: $("#tv-ip").value }); setBusy(true); }
      catch (err) { $("#tv-result").append(el("div", "note bad", err.message)); }
    });
    $("#tv-test", view).addEventListener("click", async () => {
      $("#tv-log").replaceChildren();
      const r = $("#tv-result");
      r.replaceChildren();
      try {
        await api("/api/tv/test", {});
        setBusy(true);
        r.append(html`<div class="note">شوف التلفزيون الحين (25 ثانية): المفروض يطلع مربعين فوق اللعبة.</div>
          <div class="ab"><i class="a">A</i><i class="b">B</i></div>`);
      } catch (err) { r.append(el("div", "note bad", err.message)); }
    });
  }

  if (S.guideStep === 2 && S.kind === "xbox") {
    view = xboxGuide();
  } else if (S.guideStep === 2) {
    view = html`
      <div class="seg kind" id="kind"><button data-v="ps5" class="on">PlayStation 5</button><button data-v="xbox">Xbox</button></div>
      <h2>اربط الـPS5</h2>
      <p class="lead">اللابتوب يحتاج حساب PSN <b>ثاني</b> عشان ياخذ نسخة الصورة. لو استخدمت نفس الحساب اللي تلعب فيه، يدك بتفصل.</p>
      <ol class="todo">
        <li><div>على الـPS5 أضف مستخدم بحساب PSN ثاني (مجاني)، ثم فعّل: <span class="path">الإعدادات ‹ النظام ‹ اللعب عن بُعد ‹ تمكين اللعب عن بُعد</span></div></li>
        <li><div>خلّ الـPS5 شغّال، ثم:
          <div class="action"><button class="btn" id="p-find">دوّر على الـPS5</button><span class="muted" id="p-found"></span></div></div></li>
        <li><div>سجّل دخول <b>بالحساب الثاني</b> في صفحة Sony. بعد الدخول تطلع صفحة فاضية فيها كلمة redirect: انسخ عنوانها من فوق والصقه هنا.
          <div class="action"><button class="btn soft" id="p-login">افتح صفحة الدخول</button>
          <input class="text wide" id="p-url" placeholder="https://remoteplay.dl.playstation.net/remoteplay/redirect?code=…" aria-label="عنوان صفحة redirect">
          <button class="btn" id="p-url-go">تم</button></div></div></li>
        <li><div>على الـPS5 وأنت داخل بالحساب الثاني: <span class="path">الإعدادات ‹ النظام ‹ اللعب عن بُعد ‹ ربط جهاز</span> واكتب الرقم:
          <div class="action"><input class="text" id="p-pin" placeholder="8 أرقام" inputmode="numeric" maxlength="9" aria-label="رقم الربط">
          <button class="btn" id="p-pin-go">اربط</button></div></div></li>
      </ol>
      <div id="p-result"></div>`;
    const result = $("#p-result", view);
    const say = (cls, msg) => result.replaceChildren(el("div", `note ${cls}`, msg));
    if (c.ps5_user) say("good", `اللابتوب مربوط بالحساب «${c.ps5_user}»${c.ps5_name ? ` على ${c.ps5_name}` : ""}. ما تحتاج تعيد الخطوات.`);
    const busy = async (btn, fn) => {
      btn.disabled = true;
      try { await fn(); } catch (err) { say("bad", err.message); } finally { btn.disabled = false; }
    };
    kindSwitch(view);
    $("#p-find", view).addEventListener("click", e => busy(e.currentTarget, async () => {
      $("#p-found").textContent = "أدوّر…";
      const r = await api("/api/ps5/discover", {});
      $("#p-found").textContent = r.found ? `لقيته: ${r.name} (${r.on ? "شغّال" : "في وضع الراحة"})` : "ما لقيته. تأكد إنه شغّال وعلى نفس الشبكة.";
      if (r.found && r.linked.length) say("good", `مربوط من قبل بالحساب «${r.linked[0]}».`);
      S.check = null; runCheck();
    }));
    $("#p-login", view).addEventListener("click", e => busy(e.currentTarget, () => api("/api/ps5/open-login", {})));
    $("#p-url-go", view).addEventListener("click", e => busy(e.currentTarget, async () => {
      const r = await api("/api/ps5/redirect", { url: $("#p-url").value });
      say("good", `تمام، الحساب: «${r.user}». باقي رقم الربط من الـPS5.`);
    }));
    $("#p-pin-go", view).addEventListener("click", e => busy(e.currentTarget, async () => {
      const r = await api("/api/ps5/pin", { pin: $("#p-pin").value });
      say("good", `تم الربط بالحساب «${r.user}» ✓`);
      S.check = null; runCheck();
    }));
  }

  if (S.guideStep === 3) {
    const ready = c.setup_done;
    view = html`
      <h2>${ready ? "كل شي جاهز" : "باقي شوي"}</h2>
      <p class="lead">${ready ? "من اليوم ورايح: افتح ترجِم، اختر اللعبة، واضغط «ابدأ»." : "كمّل خطوة التلفزيون وخطوة الـPS5، ثم ارجع هنا."}</p>
      <div class="tips">
        <div><b>شبّك الشاحن</b><p>على البطارية ويندوز يبطّئ المعالج والترجمة تتأخر.</p></div>
        <div><b>العب بحسابك العادي</b><p>الحساب الثاني للنسخة بس. لا تدخل اللعبة فيه.</p></div>
        <div><b>أضف أسماء الشخصيات</b><p>من «الأسماء»، عشان تنكتب صح دائماً.</p></div>
        <div><b>ترجمة ما عجبتك؟</b><p>في «الرئيسية» اضغط القلم جنب السطر واكتبها بطريقتك. تنحفظ للمرة الجاية.</p></div>
        <div><b>الترجمة تطلع قبل اكتمال السطر؟</b><p>فعّل «اللعبة تكتب النص حرف حرف» في «المظهر» ‹ «التوقيت».</p></div>
        <div><b>تبي تشيل ترجِم من التلفزيون؟</b><p>يحذف التطبيق ويرجّع إعدادات التلفزيون مثل ما كانت.</p>
          <button class="textbtn" id="tv-revert">شيله من التلفزيون</button></div>
      </div>
      <button class="btn" data-goto="home">${ready ? "يلا نبدأ" : "للرئيسية"}</button>`;
    view.append(logBox("rv-log"));
    $("#tv-revert", view).addEventListener("click", async () => {
      if (!await ask("تشيل ترجِم من التلفزيون؟", "ينحذف التطبيق من التلفزيون وترجع إعداداته مثل ما كانت. تقدر تجهّزه من جديد من «الدليل».", "شيله")) return;
      $("#rv-log").replaceChildren();
      try { await api("/api/tv/revert", {}); } catch (err) { toast(err.message); }
    });
    api("/api/guide-seen", {}).catch(() => {});
  }

  body.replaceChildren(view);
  if (S.task?.running) setBusy(true);
}

/* ---- Xbox: choosing it, linking the Microsoft account, picking the console ---- */

function kindSwitch(view) {
  $$("#kind button", view).forEach(b => b.addEventListener("click", async () => {
    if (S.live.state !== "idle") return toast("وقّف الترجمة أول");
    try { await api("/api/console", { kind: b.dataset.v }); } catch (e) { return toast(e.message); }
    S.kind = b.dataset.v;
    S.check = null;
    await runCheck();
    if (S.app) loadSetup(); else renderGuide();
  }));
}

let xboxPoll;
function xboxGuide() {
  const view = html`
    <div class="seg kind" id="kind"><button data-v="ps5">PlayStation 5</button><button data-v="xbox" class="on">Xbox</button></div>
    <h2>اربط الإكس بوكس</h2>
    <p class="lead">ترجِم ياخذ نسخة من صورة الإكس بوكس عن طريق «اللعب عن بُعد» من مايكروسوفت، بحساب مايكروسوفت اللي على جهازك. ما يرسل أي ضغطة يد للجهاز.</p>
    <div class="note">تجريبي: دعم الإكس بوكس جديد وما انجرّب على جهاز حقيقي للحين. لو ما اشتغل معك قل لنا وش طلع.</div>
    <ol class="todo" style="margin-top:18px">
      <li><div>على الإكس بوكس: <span class="path">الإعدادات ‹ الأجهزة والاتصالات ‹ الميزات عن بُعد</span> وفعّل <b>«تمكين الميزات عن بُعد»</b>. وخلّ وضع الطاقة <b>«السكون»</b>.</div></li>
      <li><div>اربط حساب مايكروسوفت (نفس الحساب اللي على الإكس بوكس). كلمة السر تنكتب في صفحة مايكروسوفت، وترجِم ما يشوفها.
        <div class="action"><button class="btn" id="x-link">اربط حساب مايكروسوفت</button><button class="textbtn" id="x-forget" hidden>افصل الحساب</button></div>
        <div id="x-code"></div></div></li>
      <li><div>اختر جهازك:
        <div class="action"><button class="btn soft" id="x-find">دوّر على أجهزتي</button></div>
        <div class="consoles" id="x-consoles"></div></div></li>
    </ol>
    <div id="x-result"></div>`;
  kindSwitch(view);
  const say = (cls, msg) => $("#x-result", view).replaceChildren(el("div", `note ${cls}`, msg));
  const showConsoles = async () => {
    const box = $("#x-consoles", view);
    box.replaceChildren(el("span", "muted", "أدوّر…"));
    try {
      const r = await api("/api/xbox/consoles");
      const st = await api("/api/xbox");
      if (!r.consoles.length) return box.replaceChildren(el("span", "muted", "ما لقيت أجهزة على هذا الحساب. تأكد إن «الميزات عن بُعد» مفعّلة."));
      box.replaceChildren(...r.consoles.map(c => {
        const b = el("button", st.console?.id === c.id ? "on" : "");
        b.append(el("b", "", c.name), el("small", "", [c.type, c.power].filter(Boolean).join(" · ")));
        b.addEventListener("click", async () => {
          await api("/api/xbox/select", { id: c.id, name: c.name }).catch(e => toast(e.message));
          say("good", `تم: «${c.name}» هو جهازك ✓ ارجع للرئيسية واضغط «ابدأ الترجمة».`);
          S.check = null; runCheck(); showConsoles();
        });
        return b;
      }));
    } catch (e) { box.replaceChildren(el("span", "muted", e.message)); }
  };
  const refresh = async () => {
    let st;
    try { st = await api("/api/xbox"); } catch { return; }
    const code = $("#x-code", view);
    if (!code.isConnected) return clearInterval(xboxPoll);
    $("#x-forget", view).hidden = !st.linked;
    $("#x-link", view).textContent = st.linked ? "اربط حساب ثاني" : "اربط حساب مايكروسوفت";
    if (st.state === "waiting") {
      const box = html`<p class="muted" style="margin:10px 0 6px">افتح الصفحة واكتب هذا الرمز، ثم سجّل دخولك:</p>
        <span class="bigcode"></span>
        <div class="action"><button class="btn soft" id="x-open">افتح صفحة مايكروسوفت</button><span class="muted">microsoft.com/link · أنتظرك…</span></div>`;
      $(".bigcode", box).textContent = st.code;
      $("#x-open", box).addEventListener("click", () => api("/api/xbox/open", {}).catch(() => {}));
      if ($(".bigcode", code)?.textContent !== st.code) code.replaceChildren(box);
    } else {
      code.replaceChildren();
      clearInterval(xboxPoll);
      if (st.state === "linked") { say("good", "انربط الحساب ✓ الحين اختر جهازك."); showConsoles(); }
      if (st.state === "failed") say("bad", "ما اكتمل الربط (الرمز يصلح ربع ساعة). اضغط «اربط» وجرّب مرة ثانية.");
    }
  };
  $("#x-link", view).addEventListener("click", async e => {
    e.currentTarget.disabled = true;
    try { await api("/api/xbox/link", {}); clearInterval(xboxPoll); xboxPoll = setInterval(refresh, 2000); await refresh(); }
    catch (err) { say("bad", err.message); }
    e.currentTarget.disabled = false;
  });
  $("#x-forget", view).addEventListener("click", async () => {
    if (!await ask("تفصل حساب مايكروسوفت؟", "ترجِم ينسى الحساب والجهاز المختار. تقدر تربط من جديد بأي وقت.", "افصله")) return;
    await api("/api/xbox/forget", {}).catch(e => toast(e.message));
    S.check = null; runCheck();
    if (S.app) loadSetup(); else renderGuide();
  });
  $("#x-find", view).addEventListener("click", showConsoles);
  api("/api/xbox").then(st => {
    $("#x-forget", view).hidden = !st.linked;
    if (st.linked) { $("#x-link", view).textContent = "اربط حساب ثاني"; showConsoles(); }
    if (st.state === "waiting") { clearInterval(xboxPoll); xboxPoll = setInterval(refresh, 2000); refresh(); }
  }).catch(() => {});
  return view;
}

function setBusy(on) {
  $$("#tv-setup, #tv-test, #tv-revert").forEach(b => b.disabled = on);
}

/* ------------------------------ live connection ------------------------------ */

function connect() {
  const es = new EventSource(withKey("/api/events"));
  es.addEventListener("state", e => {
    const was = S.live.state;
    S.live = { ...S.live, ...JSON.parse(e.data) };
    renderLive();
    if (was !== "idle" && S.live.state === "idle") runCheck();
    if (was === "starting" && S.live.state === "running") { S.latencies = []; drawSpark(); toast("الترجمة شغّالة ✓"); }
  });
  es.addEventListener("line", e => addLine(JSON.parse(e.data)));
  es.addEventListener("stats", e => {
    const st = JSON.parse(e.data);
    renderStats(st);
    if (S.gemini && st.gemini) showGemini({ ...S.gemini, status: st.gemini });
    // every key ran out for the day: said once, so the change of Arabic is not a mystery
    if (st.gemini && st.gemini.spent && !S.spentTold) {
      S.spentTold = true;
      toast("خلص الحد اليومي لمفاتيح الذكاء. الترجمة تكمل بالمترجم المحلي، والحد يتجدد بكرة.");
    }
    if (st.gemini && !st.gemini.spent) S.spentTold = false;
  });
  es.addEventListener("better", e => betterLine(JSON.parse(e.data)));
  es.addEventListener("update", e => updateProgress(JSON.parse(e.data)));
  es.addEventListener("backfill", e => {
    const b = JSON.parse(e.data);
    showBackfill(b);
    if (!b.running && $("#view-look").classList.contains("on")) loadBackfill();  // the count of what is left
  });
  // The Xbox picture is received by the program's own window: start and stop it with the session.
  es.addEventListener("xbox", e => {
    if (S.remote) return;
    const d = JSON.parse(e.data);
    if (d.action === "start") Xbox.start(d.id, d.crop ? { crop: d.crop, every: 70 } : {});
    else Xbox.stop();
  });
  es.addEventListener("phone", () => { if ($("#view-phone").classList.contains("on")) loadPhone(); toast("انربط جوال جديد ✓"); });
  es.addEventListener("ask-close", async () => {
    if ($("#dlg-ask").open) return;
    if (await ask("الترجمة شغّالة", "تبي توقّفها وتقفل البرنامج؟", "وقّف واقفل")) api("/api/quit", {}).catch(() => {});
  });
  es.addEventListener("task", e => {
    const d = JSON.parse(e.data);
    S.task = d;
    if (d.item) pushLog(d.name === "tv-revert" ? $("#rv-log") : $("#tv-log"), d.item);
    if (!d.running) {
      setBusy(false);
      if (d.name === "tv-setup") {
        const r = $("#tv-result");
        if (r) r.replaceChildren(el("div", `note ${d.ok ? "good" : "bad"}`, d.ok ? "التلفزيون جاهز ✓ جرّب مربعات الاختبار." : "ما اكتمل التجهيز. شوف الرسائل تحت."));
      }
      if (d.name === "tv-setup" || d.name === "tv-revert") { S.check = null; runCheck(); }
    }
  });
  es.onerror = () => { /* the browser reconnects by itself */ };
}

/* ---------------------------------- pairing ---------------------------------- */

let pairingShown = false;
function needPairing() {
  if (pairingShown) return;
  pairingShown = true;
  TOKEN = null;
  store.set("tarjim-token", null);
  $("#pair").hidden = false;
  $("#pair-pin").focus();
}
function deviceName() {
  const m = navigator.userAgent.match(/Android [\d.]+; ([^;)]+?)(?: Build|\))/);
  return m ? m[1].trim() : /iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : "جوال";
}
async function pairWith(pin) {
  const res = await fetch("/api/pair", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin, name: deviceName() }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.token) throw new Error(data.error || "ما قدرت أربط. تأكد إن «التحكم من الجوال» مفعّل على اللابتوب.");
  TOKEN = data.token;
  store.set("tarjim-token", TOKEN);
}
$("#pair-go").addEventListener("click", async () => {
  const err = $("#pair-err");
  err.hidden = true;
  try {
    await pairWith($("#pair-pin").value.trim());
    location.replace(location.pathname);
  } catch (e) { err.textContent = e.message; err.hidden = false; }
});
$("#pair-pin").addEventListener("keydown", e => { if (e.key === "Enter") $("#pair-go").click(); });

/* ----------------------------------- boot ----------------------------------- */

async function boot() {
  const q = new URLSearchParams(location.search);
  if (q.get("k")) { TOKEN = q.get("k"); store.set("tarjim-token", TOKEN); }
  const helloRes = await fetch(withKey("/api/hello"));
  const hello = await helloRes.json();
  S.remote = !!hello.remote;
  if (hello.version) $("#ver").textContent = hello.version;
  S.app = hello.app === "phone";
  document.body.classList.toggle("remote", S.remote);
  document.body.classList.toggle("phoneapp", S.app);
  UI.menu(what => { if (what === "live") syncLive(); });
  if (S.app) {
    $$('[data-node-n="pc"]').forEach(n => { n.textContent = "الجوال"; });
    // The phone covers the English with a plate; it cannot repaint the scene under the letters.
    for (const preset of PRESETS) if (preset.set.background === "cover") preset.set.background = "plate";
    $("#try-tv").title = "يشتغل والترجمة شغّالة";
  }
  if (S.remote && !hello.paired) {
    if (q.get("pair")) {
      try { await pairWith(q.get("pair")); return location.replace(location.pathname); } catch { /* fall through to the code screen */ }
    }
    document.body.classList.remove("booting");
    return needPairing();
  }
  if (q.get("k") || q.get("pair")) history.replaceState(null, "", location.pathname);
  if (S.remote && hello.apk && /Android/.test(navigator.userAgent) && !/TarjimApp/.test(navigator.userAgent)) {
    $("#apk-banner").hidden = false;
  }

  const st = await api("/api/state");
  S.skew = st.now - Date.now() / 1000;
  S.profiles = st.profiles;
  S.live = st.live;
  S.task = st.task;
  S.still = q.has("still");
  document.body.classList.toggle("still", S.still);
  S.kind = st.console || "ps5";
  try { S.recent = S.still ? [] : JSON.parse(UI.store.get("tarjim-recent") || "[]"); } catch { S.recent = []; }
  if (!S.recent.length && st.last_profile) S.recent = [st.last_profile];
  const mine = UI.store.get("tarjim-game");
  S.selected = st.live.profile || (S.profiles.some(p => p.name === mine) ? mine : null) || st.last_profile
    || (st.profiles.find(p => p.name !== "default") || st.profiles[0])?.name;
  fillGames();
  selectGame(S.selected);
  renderLive();
  renderStats(st.live.stats || {});
  [...(st.live.lines || [])].forEach(l => addLine(l, false));
  requestAnimationFrame(() => document.body.classList.remove("booting"));

  // ?still&view=game&step=2&demo : a frozen page for screenshots (no live connection, no timers).
  // &demo also plays a running session; &ok (and &demo) show the three devices ready.
  if (S.still) {
    await runCheck();
    if (q.has("demo") || q.has("ok")) {
      setNode("ps5", "ok", "PS5 · شغّال", "192.168.1.60");
      setNode("pc", "ok", "على الشاحن · يقرأ ويترجم");
      setNode("tv", "ok", "Android TV · جاهز", "192.168.1.50");
    }
    if (q.get("step")) S.guideStep = Number(q.get("step"));
    if (q.get("game")) selectGame(q.get("game"));
    if (q.get("view")) go(q.get("view"));
    if (q.has("library")) openLibrary();
    if (q.has("games")) openGames();
    if (q.get("scroll")) requestAnimationFrame(() => $("main").scrollTo(0, Number(q.get("scroll"))));
    if (q.has("demo")) {
      S.live = { state: "running", step: "", error: "", started_at: Date.now() / 1000 + S.skew - 754 };
      renderLive();
      [162, 148, 171, 139, 150, 190, 144, 152, 136, 158, 149, 141, 165, 150].forEach(v => renderStats({ latency_ms: v, lines: 3, fps: 30 }));
      addLine({ en: "We should leave before the storm gets here.", ar: "يجب أن نغادر قبل وصول العاصفة." }, false);
      addLine({ en: "Keep your voice down and stay close.", ar: "اخفض صوتك وابقَ قريباً." }, false);
      addLine({ en: "Hold the lantern higher, I can't see a thing.", ar: "ارفع الفانوس عالياً، لا أستطيع رؤية شيء." }, false);
    }
    return;
  }
  connect();
  if (S.app && !st.setup_done) go("setup");
  if (!S.app && !S.remote && !st.guide_seen && !st.setup_done) go("guide");
  runCheck();
  setInterval(() => { if (S.live.state === "idle" && document.visibilityState === "visible") runCheck(); }, 20000);
}
const booted = boot().catch(e => {
  document.body.classList.remove("booting");
  $("#alert").hidden = false;
  $("#alert").textContent = e.message;
});
booted.then(() => setTimeout(checkUpdate, 2500));  // after the page is up: it never holds the start
