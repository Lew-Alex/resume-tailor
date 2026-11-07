// Resume Tailor UI. Vanilla JS, no build step.
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const state = {
  view: { kind: "profile" },   // or { kind: "resume", slug }
  profile: null, yaml: "", yamlDirty: false, profileTab: "bank",
  resumes: [], doc: null, resumeTab: "refine",
  busy: {},          // slug -> progress text while the server works on it
  chatBusy: false, editing: null, status: null, token: null,
};

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
const plain = (s) => String(s ?? "").replace(/\*\*(.+?)\*\*/g, "$1");
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* unavailable */ } },
};

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function toast(msg, bad = false) {
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3000);
}

const ICON = {
  send: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  undo: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M9 14 4 9l5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 9h10a6 6 0 0 1 0 12h-3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  dl: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  folder: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  wand: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="m4 20 11-11m2-5v3m0 6v3m-5-9h3m6 0h3m-2.5-3.5-2 2m-5 5-2 2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  fit: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M5 7h14M10 7V4h4v3m-7 0 1 13h8l1-13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  edit: "✎", check: "✓", x: "✕",
};

// ---------- profile lookups ----------
function index() {
  const p = state.profile, entries = {}, bullets = {}, skills = {};
  for (const sec of ["experience", "projects", "education"]) for (const e of p[sec]) {
    entries[e.id] = { sec, e };
    for (const b of e.bullets) bullets[b.id] = { entry: e.id, b };
  }
  for (const s of p.skills) skills[s.id] = s;
  return { entries, bullets, skills };
}
const usable = (b) => b.status !== "draft" && b.evidence !== "planned";
const entryTitle = (e) => e.role || e.name || [e.school, e.degree].filter(Boolean).join(" - ");
const entrySub = (e) => [e.org || (e.tech ? plain(e.tech) : "") || e.note, [e.start, e.end].filter(Boolean).join(" – ")].filter(Boolean).join(" · ");

// ---------- sidebar ----------
function renderSidebar() {
  const p = state.profile;
  if (p) {
    const n = ["experience", "projects", "education"].reduce((a, s) => a + p[s].reduce((x, e) => x + e.bullets.length, 0), 0);
    $("#bank-count").textContent = `${n} bullets`;
  }
  $("#nav-profile").classList.toggle("active", state.view.kind === "profile");
  $("#resume-list").innerHTML = state.resumes.map((r) => {
    const busy = state.busy[r.slug];
    const dot = busy ? "busy" : r.error ? "bad" : r.pages === 1 ? "ok" : r.pages > 1 ? "bad" : "";
    const sub = busy ? busy : r.kind === "base" ? "Base resume" : (r.company || "Tailored");
    const active = state.view.kind === "resume" && state.view.slug === r.slug;
    return `<button class="resume-item ${active ? "active" : ""}" data-slug="${esc(r.slug)}">
      <span class="dot ${dot}"></span>
      <span class="ri-body"><div class="ri-title">${esc(r.title)}</div><div class="ri-sub">${esc(sub)}</div></span></button>`;
  }).join("") || `<div class="muted small" style="padding:6px 10px">No resumes yet.</div>`;
  const st = state.status;
  if (st) $("#status").innerHTML = `
    <div class="st"><span class="dot ${st.ai_available ? "ok" : "bad"}"></span>Claude ${st.provider === "mock" ? "(mock)" : "CLI"} ${st.ai_available ? "connected" : "not found"}</div>
    <div class="st"><span class="dot ${st.latex_available ? "ok" : "bad"}"></span>LaTeX (Tectonic) ${st.latex_available ? "ready" : "not found"}</div>`;
}

$("#nav-profile").addEventListener("click", () => go({ kind: "profile" }));
$("#resume-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-slug]");
  if (b) go({ kind: "resume", slug: b.dataset.slug });
});

async function go(view) {
  state.view = view;
  store.set("view", view);
  if (view.kind === "resume") {
    try { state.doc = await api(`/api/resumes/${encodeURIComponent(view.slug)}`); }
    catch { state.view = { kind: "profile" }; state.doc = null; }
  }
  render();
}

function render() {
  renderSidebar();
  if (state.view.kind === "resume" && state.doc) renderResume();
  else renderProfile();
}

// ---------- master profile view ----------
function badgeTags(b) {
  return [
    b.status === "draft" ? `<span class="tag draft">draft · needs approval</span>` : "",
    b.evidence === "experiment" ? `<span class="tag experiment">experiment</span>` : "",
    b.evidence === "planned" ? `<span class="tag planned">planned</span>` : "",
    b.metrics_needed ? `<span class="tag metric">needs a number</span>` : "",
    ...(b.groups || []).map((g) => `<span class="tag group" title="Bullets in the same group say the same thing; a resume uses at most one">${esc(g)}</span>`),
  ].join("");
}

function bulletRow(b) {
  if (state.editing === b.id) {
    return `<div class="bl" data-bid="${esc(b.id)}"><div class="tx">
      <textarea rows="3" id="edit-text">${esc(b.text)}</textarea>
      <div class="muted small" style="margin-top:4px">Wrap words in **double asterisks** to bold them.</div>
      <div class="edit-row"><button class="btn ghost" data-act="cancel">Cancel</button><button class="btn primary" data-act="save">Save</button></div>
    </div></div>`;
  }
  return `<div class="bl ${b.status === "draft" ? "draft" : ""}" data-bid="${esc(b.id)}">
    <span class="mark"></span>
    <div class="tx">${md(b.text)}<div class="meta">${badgeTags(b)}</div></div>
    <div class="hover-acts">
      ${b.status === "draft" ? `<button class="icon-btn" data-act="approve" title="Approve" style="color:var(--good)">${ICON.check}</button>` : ""}
      <button class="icon-btn" data-act="edit" title="Edit">${ICON.edit}</button>
      <button class="icon-btn" data-act="delete" title="Delete">${ICON.x}</button>
    </div></div>`;
}

function renderBank() {
  const p = state.profile;
  const section = (key, label) => `
    <div class="sec-title"><h2>${label}</h2><span class="n">${p[key].length}</span></div>
    ${p[key].map((e) => `
      <div class="card">
        <div class="card-head">
          <div><div class="t">${md(entryTitle(e))}</div><div class="s">${md(entrySub(e))}</div></div>
          <div class="acts"><span class="id-chip">${esc(e.id)}</span>
            <button class="btn ghost" data-help="${esc(e.id)}" title="Claude interviews you and drafts bullets">${ICON.wand} Draft bullets</button></div>
        </div>
        ${e.bullets.length ? `<div class="bullets">${e.bullets.map(bulletRow).join("")}</div>` : ""}
      </div>`).join("") || `<div class="muted">Nothing yet.</div>`}`;
  const cats = new Map();
  for (const s of p.skills) { if (!cats.has(s.category)) cats.set(s.category, []); cats.get(s.category).push(s); }
  return `<div class="bank"><div class="bank-inner">
    ${section("experience", "Experience")}${section("projects", "Projects")}${section("education", "Education")}
    <div class="sec-title"><h2>Skills</h2><span class="n">${p.skills.length}</span></div>
    <div class="card"><div class="skills-card">${[...cats].map(([c, list]) => `
      <div class="skill-row"><div class="cat">${esc(c)}</div><div class="items">${list.map((s) => `<span class="skill ${s.bold ? "b" : ""}" title="${esc(s.id)}">${esc(s.name)}</span>`).join("")}</div></div>`).join("")}
    </div></div></div></div>`;
}

function chatHtml(log, empty, busyText) {
  const list = (a, c) => (a?.length ? `<ul class="${c}">${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
  const msgs = log.map((m) => `<div class="msg ${m.role}">${m.role === "assistant" ? md(m.content) : esc(m.content)}${list(m.applied, "applied")}${list(m.warnings, "warn")}${list(m.errors, "err")}</div>`).join("");
  const thinking = busyText ? `<div class="msg assistant thinking"><span class="spinner"></span>${esc(busyText)}</div>` : "";
  return msgs || thinking ? msgs + thinking : empty;
}

function composer(placeholder, extraHint = "") {
  return `<form class="composer" id="composer"><div class="composer-box">
      <textarea id="chat-input" rows="1" placeholder="${esc(placeholder)}"></textarea>
      <button class="send" id="chat-send" title="Send (⌘↵)" ${state.chatBusy ? "disabled" : ""}>${ICON.send}</button>
    </div><div class="composer-hint"><span>⌘↵ to send</span>${extraHint}</div></form>`;
}

let profileChat = [];
async function renderProfile() {
  const p = state.profile;
  if (!p) return;
  const drafts = ["experience", "projects", "education"].reduce((a, s) => a + p[s].reduce((x, e) => x + e.bullets.filter((b) => b.status === "draft").length, 0), 0);
  const nb = ["experience", "projects", "education"].reduce((a, s) => a + p[s].reduce((x, e) => x + e.bullets.length, 0), 0);
  $("#main").innerHTML = `
    <header class="page-head">
      <div><h1>Master profile</h1><div class="sub">${p.experience.length + p.projects.length + p.education.length} entries · ${nb} bullets · ${p.skills.length} skills${drafts ? ` · <span style="color:var(--warn)">${drafts} draft${drafts > 1 ? "s" : ""} to review</span>` : ""}</div></div>
      <div class="actions">
        <div class="seg" id="ptabs"><button data-t="bank" class="${state.profileTab === "bank" ? "active" : ""}">Bank</button><button data-t="yaml" class="${state.profileTab === "yaml" ? "active" : ""}">YAML</button></div>
        <button class="btn" id="p-undo" title="Undo the last change to your profile">${ICON.undo} Undo</button>
      </div>
    </header>
    <div class="body profile">
      ${state.profileTab === "bank" ? renderBank() : `
        <div class="yaml-wrap">
          <div class="banner ${state.yamlBanner ? "" : "hidden"}" id="yaml-banner">The file changed on disk. <button class="btn" id="yaml-reload">Load latest</button></div>
          <textarea id="yaml" spellcheck="false"></textarea>
          <div style="display:flex;gap:8px;align-items:center"><span class="muted small">data/profile.yaml: changes here, in your editor, or from Claude all show up live and rebuild every resume.</span><span class="spacer"></span><button class="btn primary" id="yaml-save">Save</button></div>
        </div>`}
      <section class="panel">
        <div class="panel-tabs"><strong style="font-size:13.5px">Profile assistant</strong><span class="spacer"></span><button class="link-btn" id="chat-clear">Clear</button></div>
        <div class="panel-body flush">
          <div class="messages" id="messages">${chatHtml(profileChat, `<div class="empty-chat"><h3>Add to your bank</h3>Paste anything you've done, as messy as you like: notes, an old resume, a project README. Claude turns it into entries and draft bullets. Facts only come from you.</div>`, state.chatBusy ? "Claude is thinking…" : "")}</div>
          ${composer("Tell Claude what to add or change…")}
        </div>
      </section>
    </div>`;
  if (state.profileTab === "yaml") {
    $("#yaml").value = state.yaml;
    $("#yaml").addEventListener("input", () => { state.yamlDirty = true; });
  }
  scrollChat();
}

function scrollChat() { const m = $("#messages"); if (m) m.scrollTop = m.scrollHeight; }

// ---------- resume view ----------
function fitChip(d) {
  if (state.busy[d.slug]) return `<span class="chip warn"><span class="spinner" style="width:11px;height:11px"></span>${esc(state.busy[d.slug])}</span>`;
  if (d.build_error) return `<span class="chip bad">LaTeX error</span>`;
  const b = d.build || {};
  if (!b.pages) return `<span class="chip">Not built yet</span>`;
  return b.pages > 1 ? `<span class="chip bad">Over 1 page by ~${Math.round(b.overflow_pt)}pt</span>`
    : `<span class="chip good">1 page · ${Math.round(b.slack_pt)}pt free</span>`;
}

function checksChip(d) {
  const c = (d.build || {}).checks;
  if (!c || state.busy[d.slug] || d.build_error) return "";
  const n = c.filter((i) => i.level !== "info").length;
  return n ? `<button class="chip warn chip-btn" id="r-checks" title="Open formatting checks">⚠ ${n} formatting issue${n > 1 ? "s" : ""}</button>`
    : `<span class="chip good" title="All formatting checks pass">✓ Formatting</span>`;
}

function renderResume() {
  const d = state.doc;
  const b = d.build || {};
  const files = b.stem ? `/build/${encodeURIComponent(d.slug)}/${encodeURIComponent(b.stem)}` : null;
  const nIssues = (b.checks || []).filter((i) => i.level !== "info").length;
  const tabs = [["refine", "Refine"], ["contents", "Contents"], ["checks", `Checks${nIssues ? ` <span class="badge">${nIssues}</span>` : ""}`], ["job", d.kind === "base" ? "About" : "Job"], ["latex", "LaTeX"]];
  $("#main").innerHTML = `
    <header class="page-head">
      <div style="min-width:0"><h1>${esc(d.title)}${d.company ? `<span class="muted" style="font-weight:500"> · ${esc(d.company)}</span>` : ""}</h1>
        <div class="sub">${d.kind === "base" ? "Base resume" : "Tailored resume"} · ${esc(b.stem || "")}.tex</div></div>
      <div class="actions">
        ${fitChip(d)}${checksChip(d)}
        <button class="btn ghost" id="r-undo" title="Undo the last change to this resume">${ICON.undo}</button>
        ${d.scores && Object.keys(d.scores).length ? `<button class="btn" id="r-autofit" title="Re-pick everything from the AI scores and fit one page">${ICON.fit} Auto-fit</button>` : ""}
        <button class="btn" id="r-reveal" title="Show the .tex and .pdf in Finder">${ICON.folder} Finder</button>
        ${files ? `<a class="btn" href="${files}.tex" download>${ICON.dl} .tex</a><a class="btn primary" href="${files}.pdf" download>${ICON.dl} PDF</a>` : ""}
        <button class="btn ghost danger" id="r-delete" title="Delete this resume">${ICON.trash}</button>
      </div>
    </header>
    <div class="body resume">
      <section class="panel">
        <div class="panel-tabs"><div class="seg" id="rtabs">${tabs.map(([k, l]) => `<button data-t="${k}" class="${state.resumeTab === k ? "active" : ""}">${l}</button>`).join("")}</div></div>
        <div class="panel-body ${state.resumeTab === "refine" ? "flush" : ""}" id="rtab-body"></div>
      </section>
      <section class="preview" id="preview">${previewHtml(d)}</section>
    </div>`;
  renderResumeTab();
}

function previewHtml(d) {
  const b = d.build || {};
  const busy = state.busy[d.slug] ? `<div class="preview-busy"><span class="spinner"></span>${esc(state.busy[d.slug])}</div>` : "";
  if (d.build_error) return busy + `<div class="build-error">${esc(d.build_error)}</div>`;
  if (!b.png?.length) return busy + `<div class="preview-inner"><div class="muted">Building…</div></div>`;
  const v = Math.round((b.at || 0) * 1000);
  return busy + `<div class="preview-inner">${b.png.map((f, i) => `<div class="paper ${i > 0 ? "over" : ""}"><img alt="Resume page ${i + 1}" src="/build/${encodeURIComponent(d.slug)}/${f}?v=${v}"></div>`).join("")}</div>`;
}

function renderResumeTab() {
  const box = $("#rtab-body");
  if (!box) return;
  const d = state.doc;
  if (state.resumeTab === "refine") {
    const sugg = d.kind === "base"
      ? ["Tighten any two-line bullets to one line", "Which bullets are weakest here?", "Use the free space well"]
      : ["Refine for this job automatically", "Tighten any two-line bullets", "Use the free space well", "What's missing for this job?"];
    if ((d.build?.checks || []).some((i) => i.fix)) sugg.unshift("Fix the formatting issues");
    box.innerHTML = `<div class="messages" id="messages">${chatHtml(d.chat || [], `<div class="empty-chat"><h3>Refine this resume</h3>Ask for changes in plain words. Claude can pick different bullets, reorder them, regroup skills, or reword a bullet <em>for this resume only</em> without adding facts. Every change recompiles your LaTeX.</div>`, state.busy[d.slug])}</div>
      <div class="suggestions">${sugg.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join("")}</div>
      ${composer("e.g. Put the backend bullets first and drop the oldest project", `<button class="link-btn" type="button" id="auto-refine">Run automatic refinement</button>`)}`;
    scrollChat();
  } else if (state.resumeTab === "contents") {
    box.innerHTML = contentsHtml();
  } else if (state.resumeTab === "checks") {
    box.innerHTML = checksHtml();
  } else if (state.resumeTab === "job") {
    box.innerHTML = jobHtml();
  } else {
    box.innerHTML = `<div class="muted small" style="margin-bottom:10px">Generated from your bank with your <code>resume_style.tex</code>. It recompiles automatically after every change.</div><pre class="code" id="tex">Loading…</pre>`;
    const stem = d.build?.stem;
    if (stem) fetch(`/build/${encodeURIComponent(d.slug)}/${encodeURIComponent(stem)}.tex`).then((r) => r.text()).then((t) => {
      const el = $("#tex"); if (el) el.innerHTML = esc(t).replace(/(\\[a-zA-Z]+)/g, '<span class="c1">$1</span>');
    });
  }
}

function scoreHtml(id) {
  const s = state.doc.scores?.[id];
  if (!s) return "";
  const cls = s.s >= 7 ? "hi" : s.s >= 4 ? "mid" : "";
  return `<span class="score ${cls}" title="${esc(s.why || "")}">${Math.round(s.s)}</span>`;
}

function contentsHtml() {
  const d = state.doc, ix = index(), lay = d.layout;
  const st = d.settings || {};
  const knobs = d.scores && Object.keys(d.scores).length ? `
    <div class="knobs">
      <label>Min relevance <input type="number" data-k="minScore" min="0" max="10" value="${st.minScore ?? 4}"></label>
      <label>Max bullets / job <input type="number" data-k="maxExpBullets" min="1" max="10" value="${st.maxExpBullets ?? 5}"></label>
      <label>Max projects <input type="number" data-k="maxProjects" min="0" max="6" value="${st.maxProjects ?? 3}"></label>
      <label>Max bullets / project <input type="number" data-k="maxProjBullets" min="1" max="10" value="${st.maxProjBullets ?? 4}"></label>
      <div class="row"><span class="muted small">Scores are Claude's relevance to the job. Hover a score for the reason.</span><button class="btn" id="apply-knobs">${ICON.fit} Re-fit</button></div>
    </div>` : `<div class="callout" style="margin-bottom:14px">Toggle entries and bullets to change this resume. Your bank stays as it is.</div>`;

  const skillLines = `<div class="c-sec">Skills</div>${lay.skill_lines.map((ln, li) => `
    <div class="skill-line" data-line="${li}"><div class="lab">${esc(ln.label)}</div><div class="items">
      ${ln.items.map((sid) => `<span class="skill ${ix.skills[sid]?.bold ? "b" : ""}">${esc(ix.skills[sid]?.name || sid)} <button data-rm-skill="${esc(sid)}" title="Remove">✕</button></span>`).join("")}
      <select data-add-skill="${li}"><option value="">+ add</option>${state.profile.skills.filter((s) => !ln.items.includes(s.id)).map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select>
    </div></div>`).join("")}`;

  const sections = ["experience", "projects", "education"].map((sec) => {
    const onItems = new Map(lay[sec].map((i) => [i.id, i]));
    const order = [...lay[sec].map((i) => i.id), ...state.profile[sec].map((e) => e.id).filter((id) => !onItems.has(id))];
    return `<div class="c-sec">${sec}</div>` + order.map((eid) => {
      const e = ix.entries[eid]?.e;
      if (!e) return "";
      const item = onItems.get(eid);
      const onB = item ? item.bullets : [];
      const rest = e.bullets.filter((b) => !onB.includes(b.id)).sort((a, b) => (d.scores?.[b.id]?.s ?? 0) - (d.scores?.[a.id]?.s ?? 0) || 0);
      const rows = [...onB.map((id) => ix.bullets[id]?.b).filter(Boolean), ...rest].map((b) => {
        const on = onB.includes(b.id);
        const rw = d.rewrites?.[b.id];
        const live = rw && rw.base === b.text;
        return `<div class="c-bl ${on ? "" : "off"}">
          <input type="checkbox" data-entry="${esc(eid)}" data-bullet="${esc(b.id)}" ${on ? "checked" : ""} ${usable(b) ? "" : "disabled"}>
          ${scoreHtml(b.id)}
          <div class="tx">${md(live ? rw.text : b.text)}${!usable(b) ? ` <span class="tag ${b.status === "draft" ? "draft" : "planned"}">${b.status === "draft" ? "draft" : "planned"}</span>` : ""}
            ${live ? `<span class="rw">Reworded for this resume · <button class="link-btn" data-clear-rw="${esc(b.id)}">restore original</button></span>` : ""}</div>
          ${on ? `<div class="ord"><button data-up="${esc(b.id)}" data-entry="${esc(eid)}" title="Move up">▲</button><button data-down="${esc(b.id)}" data-entry="${esc(eid)}" title="Move down">▼</button></div>` : ""}
        </div>`;
      }).join("");
      return `<div class="c-entry ${item ? "" : "off"}">
        <div class="c-head"><label class="switch" title="${item ? "Remove from resume" : "Add to resume"}"><input type="checkbox" data-toggle-entry="${esc(eid)}" ${item ? "checked" : ""}><span></span></label>
          ${scoreHtml(eid)}<span class="t">${md(entryTitle(e))}</span></div>${rows}</div>`;
    }).join("");
  }).join("");
  return knobs + skillLines + sections;
}

const LEVEL = { error: ["bad", "Must fix"], warn: ["warn", "Should fix"], info: ["", "Minor"] };
function checksHtml() {
  const d = state.doc, c = (d.build || {}).checks || [];
  const fixable = c.some((i) => i.fix);
  const head = `<div class="callout" style="margin-bottom:14px">These checks read the compiled PDF after every build: one page, even spacing, nothing in the margins, no bullet ending on a one- or two-word line, no 3-line bullets, balanced skill columns, and no repeated achievements.</div>`;
  if (!c.length) return head + `<div class="all-good">✓ No formatting issues found.</div>`;
  return head + `<div class="row-between"><strong>${c.length} finding${c.length > 1 ? "s" : ""}</strong>
      ${fixable ? `<button class="btn primary" id="fix-format" ${state.chatBusy ? "disabled" : ""}>${ICON.wand} Fix with Claude</button>` : ""}</div>
    <div class="issues">${c.map((i) => `<div class="issue">
      <span class="chip ${LEVEL[i.level][0]}">${LEVEL[i.level][1]}</span>
      <div class="tx">${esc(i.msg)}${i.ids.length ? `<div class="muted small">${i.ids.map(esc).join(", ")}</div>` : ""}</div></div>`).join("")}</div>
    <p class="muted small">Claude fixes these by trimming wording <em>for this resume only</em>. Your bank stays as it is, and every change can be undone (↶) or restored per bullet in Contents.</p>`;
}

function hasKeyword(text, kw) {
  const pat = kw.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  return new RegExp(`(^|[^A-Za-z0-9+#])${pat}(?![A-Za-z0-9+#])`, "i").test(text);
}

function jobHtml() {
  const d = state.doc;
  if (d.kind === "base") {
    return `<div class="callout">${esc(d.summary || "A base resume.")}</div>
      <p class="muted small" style="margin-top:14px">Base resumes keep exactly the bullets you chose. They still rebuild whenever you edit the bank, and Claude can refine them in the Refine tab. To make a version for a specific posting, use <strong>Tailor to a job</strong> in the sidebar.</p>`;
  }
  const text = d.build?.text || "";
  const kws = d.keywords || [];
  const hits = kws.filter((k) => hasKeyword(text, k));
  return `<div class="callout">${esc(d.summary)}</div>
    <h3 class="sub">Keywords on the page · ${hits.length}/${kws.length}</h3>
    <div class="kw">${kws.map((k) => `<span class="chip ${hits.includes(k) ? "good" : "miss"}">${esc(k)}</span>`).join("")}</div>
    <p class="muted small">Dashed keywords aren't on the page. If you have real experience with one, add it to your bank. Don't fake it.</p>
    <h3 class="sub">Job description</h3><div class="job-text">${esc(d.job)}</div>`;
}

// ---------- events: main area ----------
$("#main").addEventListener("click", async (ev) => {
  const t = ev.target.closest("button, [data-act]");
  if (!t) return;
  try {
    // profile tabs / actions
    if (t.closest("#ptabs")) { state.profileTab = t.dataset.t; return renderProfile(); }
    if (t.id === "p-undo") { await api("/api/undo", { method: "POST" }); return toast("Undid the last profile change"); }
    if (t.id === "yaml-save") { await api("/api/profile", { method: "PUT", body: { yaml: $("#yaml").value } }); state.yamlDirty = false; state.yamlBanner = false; return toast("Saved"); }
    if (t.id === "yaml-reload") { state.yamlDirty = false; state.yamlBanner = false; return renderProfile(); }
    if (t.id === "chat-clear") { if (confirm("Clear the profile chat? Your profile is not affected.")) { await api("/api/chat", { method: "DELETE" }); profileChat = []; renderProfile(); } return; }
    if (t.dataset.help) {
      const e = index().entries[t.dataset.help]?.e;
      const input = $("#chat-input");
      input.value = `Help me write strong bullets for "${plain(entryTitle(e))}" (id: ${t.dataset.help}). Interview me first.`;
      input.focus(); autosize(input); return;
    }
    const bl = t.closest("[data-bid]");
    if (bl && t.dataset.act) {
      const id = bl.dataset.bid;
      if (t.dataset.act === "approve") await api("/api/ops", { method: "POST", body: { ops: [{ op: "update_bullet", id, fields: { status: "approved" } }] } });
      if (t.dataset.act === "delete" && confirm("Delete this bullet from your bank? (Undo can bring it back.)")) await api("/api/ops", { method: "POST", body: { ops: [{ op: "remove_bullet", id }] } });
      if (t.dataset.act === "edit") { state.editing = id; renderProfile(); $("#edit-text")?.focus(); }
      if (t.dataset.act === "cancel") { state.editing = null; renderProfile(); }
      if (t.dataset.act === "save") {
        const text = $("#edit-text").value.trim();
        state.editing = null;
        await api("/api/ops", { method: "POST", body: { ops: [{ op: "update_bullet", id, fields: { text } }] } });
      }
      return;
    }
    // resume actions
    const d = state.doc;
    if (t.closest("#rtabs")) { state.resumeTab = t.closest("button").dataset.t; store.set("rtab", state.resumeTab); $$("#rtabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === state.resumeTab)); $("#rtab-body").className = "panel-body" + (state.resumeTab === "refine" ? " flush" : ""); return renderResumeTab(); }
    if (t.id === "r-undo") { await api(`/api/resumes/${d.slug}/undo`, { method: "POST" }); return; }
    if (t.id === "r-autofit") return busyCall(d.slug, "Re-picking and fitting…", () => api(`/api/resumes/${d.slug}/autofit`, { method: "POST" }));
    if (t.id === "r-reveal") return api(`/api/resumes/${d.slug}/reveal`, { method: "POST" });
    if (t.id === "r-delete") {
      if (!confirm(`Delete "${d.title}"? Its .tex/.pdf in the app's data folder are removed too.`)) return;
      await api(`/api/resumes/${d.slug}`, { method: "DELETE" });
      return go({ kind: "profile" });
    }
    if (t.id === "apply-knobs") {
      const settings = Object.fromEntries($$(".knobs input").map((i) => [i.dataset.k, +i.value]));
      await api(`/api/resumes/${d.slug}`, { method: "PATCH", body: { settings } });
      return busyCall(d.slug, "Re-picking and fitting…", () => api(`/api/resumes/${d.slug}/autofit`, { method: "POST" }));
    }
    if (t.classList.contains("suggestion")) {
      if (/automatically/.test(t.textContent)) return sendRefine(null);
      if (/formatting issues/.test(t.textContent)) return sendRefine(null, "fix");
      return sendRefine(t.textContent);
    }
    if (t.id === "auto-refine") return sendRefine(null);
    if (t.id === "r-checks") { state.resumeTab = "checks"; return renderResume(); }
    if (t.id === "fix-format") return sendRefine(null, "fix");
    if (t.dataset.clearRw) return resumeOps([{ op: "clear_rewrite", bullet: t.dataset.clearRw }]);
    if (t.dataset.rmSkill) {
      const li = +t.closest("[data-line]").dataset.line;
      const lines = structuredClone(d.layout.skill_lines);
      lines[li].items = lines[li].items.filter((s) => s !== t.dataset.rmSkill);
      return resumeOps([{ op: "set_skill_lines", lines }]);
    }
    if (t.dataset.up || t.dataset.down) {
      const id = t.dataset.up || t.dataset.down, eid = t.dataset.entry;
      const item = ["experience", "projects", "education"].flatMap((s) => d.layout[s]).find((i) => i.id === eid);
      const list = [...item.bullets], i = list.indexOf(id), j = t.dataset.up ? i - 1 : i + 1;
      if (j < 0 || j >= list.length) return;
      [list[i], list[j]] = [list[j], list[i]];
      return resumeOps([{ op: "set_bullets", entry: eid, bullets: list }]);
    }
  } catch (e) { toast(e.message, true); }
});

$("#main").addEventListener("change", async (ev) => {
  const t = ev.target, d = state.doc;
  try {
    if (t.dataset.bullet) {
      const item = ["experience", "projects", "education"].flatMap((s) => d.layout[s]).find((i) => i.id === t.dataset.entry);
      let list = item ? [...item.bullets] : [];
      list = t.checked ? [...list, t.dataset.bullet] : list.filter((x) => x !== t.dataset.bullet);
      return resumeOps([{ op: "set_bullets", entry: t.dataset.entry, bullets: list }]);
    }
    if (t.dataset.toggleEntry) {
      const eid = t.dataset.toggleEntry;
      if (!t.checked) return resumeOps([{ op: "remove_entry", entry: eid }]);
      const e = index().entries[eid].e;
      const best = e.bullets.filter(usable).sort((a, b) => (d.scores?.[b.id]?.s ?? 0) - (d.scores?.[a.id]?.s ?? 0)).slice(0, 2).map((b) => b.id);
      return resumeOps([{ op: "set_bullets", entry: eid, bullets: best }]);
    }
    if (t.dataset.addSkill !== undefined && t.value) {
      const lines = structuredClone(d.layout.skill_lines);
      lines[+t.dataset.addSkill].items.push(t.value);
      return resumeOps([{ op: "set_skill_lines", lines }]);
    }
  } catch (e) { toast(e.message, true); }
});

async function resumeOps(ops) {
  const d = state.doc;
  return busyCall(d.slug, "Recompiling…", () => api(`/api/resumes/${d.slug}/ops`, { method: "POST", body: { ops } }));
}

async function busyCall(slug, text, fn) {
  state.busy[slug] = text;
  refreshBusyUI();
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { delete state.busy[slug]; await reloadDoc(slug); refreshResumeList(); }
}

function refreshBusyUI() {
  renderSidebar();
  const d = state.doc;
  if (state.view.kind === "resume" && d) {
    const pv = $("#preview"); if (pv) pv.innerHTML = previewHtml(d);
    const chip = $(".page-head .actions .chip"); if (chip) chip.outerHTML = fitChip(d);
    if (state.resumeTab === "refine" && state.chatBusy) { const m = $("#messages .thinking"); if (m) m.lastChild.textContent = state.busy[d.slug] || "Working…"; }
  }
}

// ---------- chat (both views) ----------
function autosize(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 180) + "px"; }
$("#main").addEventListener("input", (e) => { if (e.target.id === "chat-input") autosize(e.target); });
$("#main").addEventListener("keydown", (e) => {
  if (e.target.id === "chat-input" && e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#main").addEventListener("submit", async (e) => {
  if (e.target.id !== "composer") return;
  e.preventDefault();
  const msg = $("#chat-input").value.trim();
  if (!msg || state.chatBusy) return;
  if (state.view.kind === "resume") return sendRefine(msg);
  state.chatBusy = true;
  profileChat.push({ role: "user", content: msg });
  renderProfile();
  try {
    await api("/api/chat", { method: "POST", body: { message: msg } });
  } catch (err) { toast(err.message, true); profileChat.pop(); }
  state.chatBusy = false;
  profileChat = await api("/api/chat");
  if (state.view.kind === "profile") renderProfile();
});

async function sendRefine(msg, mode) {
  const d = state.doc;
  if (state.chatBusy) return;
  state.chatBusy = true;
  state.resumeTab = "refine";
  const shown = mode === "fix" ? "Fix the formatting issues found in the PDF." : msg;
  d.chat = [...(d.chat || []), ...(shown ? [{ role: "user", content: shown }] : [])];
  state.busy[d.slug] = mode === "fix" ? "Claude is fixing formatting…" : msg ? "Claude is thinking…" : "Claude is reviewing this resume against the job…";
  renderResume();
  try {
    if (mode === "fix") await api(`/api/resumes/${d.slug}/fix-format`, { method: "POST", body: { token: state.token } });
    else await api(`/api/resumes/${d.slug}/chat`, { method: "POST", body: { message: msg, token: state.token } });
  } catch (err) { toast(err.message, true); }
  state.chatBusy = false;
  delete state.busy[d.slug];
  await reloadDoc(d.slug);
  refreshResumeList();
}

// ---------- new resume modal ----------
const modal = $("#modal");
$("#new-resume").addEventListener("click", () => { modal.classList.remove("hidden"); $("#job-input").focus(); });
$("#modal-close").addEventListener("click", () => { if (!state.tailoring) modal.classList.add("hidden"); });
modal.addEventListener("click", (e) => { if (e.target === modal && !state.tailoring) modal.classList.add("hidden"); });
const STEPS = ["Reading the job and scoring your bank…", "Picking bullets and fitting one page…", "Claude is refining wording and choices…", "Fixing formatting issues found in the PDF…"];
function renderSteps(cur) {
  const i = STEPS.findIndex((s) => cur && cur.startsWith(s.slice(0, 12)));
  $("#job-progress").innerHTML = STEPS.map((s, k) => `<div class="step ${k === i ? "cur" : ""}">${k < i ? '<span class="ok">✓</span>' : k === i ? '<span class="spinner"></span>' : '<span class="ok"> </span>'}${esc(s)}</div>`).join("")
    + (i === -1 && cur ? `<div class="step cur"><span class="spinner"></span>${esc(cur)}</div>` : "");
}
$("#job-go").addEventListener("click", async () => {
  const job = $("#job-input").value.trim();
  if (job.length < 40) return toast("Paste the full job description.", true);
  state.tailoring = true;
  state.token = Math.random().toString(36).slice(2);
  $("#job-go").disabled = true;
  $("#job-progress").classList.remove("hidden");
  renderSteps(STEPS[0]);
  try {
    const doc = await api("/api/resumes", { method: "POST", body: { job, token: state.token } });
    modal.classList.add("hidden");
    $("#job-input").value = "";
    await refreshResumeList();
    state.resumeTab = "contents";
    await go({ kind: "resume", slug: doc.slug });
    toast(`Tailored resume ready: ${doc.title}`);
  } catch (e) { toast(e.message, true); }
  state.tailoring = false;
  $("#job-go").disabled = false;
  $("#job-progress").classList.add("hidden");
});

// ---------- data loading + live updates ----------
async function loadProfile() {
  const { profile, yaml } = await api("/api/profile");
  state.profile = profile;
  if (state.yamlDirty && yaml !== state.yaml) state.yamlBanner = true;
  state.yaml = yaml;
}
async function refreshResumeList() { state.resumes = await api("/api/resumes"); renderSidebar(); }
async function reloadDoc(slug) {
  if (state.view.kind !== "resume" || state.view.slug !== slug) return;
  try { state.doc = await api(`/api/resumes/${encodeURIComponent(slug)}`); } catch { return go({ kind: "profile" }); }
  if (state.chatBusy) { refreshBusyUI(); return; }
  const scroll = $("#rtab-body")?.scrollTop;
  renderResume();
  if (scroll && state.resumeTab !== "refine") $("#rtab-body").scrollTop = scroll;
}

function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = async (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "profile") {
      await loadProfile();
      if (state.view.kind === "profile" && !state.editing) {
        if (state.profileTab === "yaml" && state.yamlDirty) { const b = $("#yaml-banner"); if (b) b.classList.remove("hidden"); }
        else if (!state.chatBusy) renderProfile();
      }
      renderSidebar();
    }
    if (e.type === "resumes") refreshResumeList();
    if (e.type === "resume") { refreshResumeList(); if (!state.busy[e.slug]) reloadDoc(e.slug); }
    if (e.type === "progress") {
      if (e.token && e.token === state.token && state.tailoring) renderSteps(e.text);
      if (e.slug && e.text !== "Done" && (state.busy[e.slug] || state.chatBusy)) { state.busy[e.slug] = e.text; refreshBusyUI(); }
    }
  };
  es.onerror = () => { $("#status").innerHTML = `<div class="st"><span class="dot bad"></span>Server offline: run ./run.sh</div>`; };
  es.onopen = async () => { state.status = await api("/api/status").catch(() => null); renderSidebar(); };
}

(async function init() {
  state.resumeTab = store.get("rtab", "refine");
  await Promise.all([loadProfile(), refreshResumeList(), api("/api/chat").then((c) => { profileChat = c; })]);
  state.status = await api("/api/status").catch(() => null);
  const v = store.get("view", { kind: "profile" });
  await go(v.kind === "resume" && state.resumes.some((r) => r.slug === v.slug) ? v : { kind: "profile" });
  connect();
})().catch((e) => toast(e.message, true));
