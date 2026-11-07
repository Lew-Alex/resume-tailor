"""Resumes: each one is a *layout* that points into the master profile by id.

data/resumes/<slug>.json
{
  "slug", "title", "company", "created", "updated",
  "job": "...", "summary": "...", "keywords": [...],       # empty for base resumes
  "scores": {id: {"s": 0-10, "why": "..."}},               # AI relevance scores (tailored only)
  "settings": {minScore, maxExpBullets, maxProjects, maxProjBullets},
  "layout": {
     "skill_lines": [{"label": "Firmware:", "items": [skill ids]}],
     "experience": [{"id": entry id, "bullets": [bullet ids]}],
     "projects":   [...], "education": [...]
  },
  "rewrites": {bullet id: {"text": "...", "base": "<bank text when rewritten>"}},
  "chat": [...], "build": {pages, slack_pt, ...}
}

The bank is the single source of truth for facts. A resume only chooses,
orders, groups and (lightly) rewords. Any profile change rebuilds every resume.
"""
from __future__ import annotations

import copy
import json
import re
import time

from . import checks
from . import profile_ops as po
from . import texgen

SECTIONS = ("experience", "projects", "education")
DEFAULT_SETTINGS = {"minScore": 4, "maxExpBullets": 5, "maxProjects": 3, "maxProjBullets": 4}
MAX_SKILL_LINES = 6
BULLET_MIN_PT = 14.5  # one-line bullet


class ResumeError(ValueError):
    pass


# ---------- storage ----------

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48] or "resume"


def path(slug: str):
    return po.RESUMES / f"{slugify(slug)}.json"


def load(slug: str) -> dict:
    f = path(slug)
    if not f.exists():
        raise ResumeError(f"No resume '{slug}'")
    return json.loads(f.read_text())


def save(doc: dict) -> None:
    doc["updated"] = time.time()
    tmp = path(doc["slug"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(path(doc["slug"]))


def list_all() -> list[dict]:
    out = []
    for f in po.RESUMES.glob("*.json"):
        try:
            out.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    out.sort(key=lambda d: (d.get("kind") != "base", -(d.get("created") or 0)))
    return out


def unique_slug(base: str) -> str:
    slug, n = slugify(base), 2
    while path(slug).exists():
        slug, n = f"{slugify(base)}-{n}", n + 1
    return slug


def new_doc(slug: str, title: str, company: str = "", kind: str = "tailored") -> dict:
    return {"slug": slug, "title": title, "company": company, "kind": kind,
            "created": time.time(), "updated": time.time(),
            "job": "", "summary": "", "keywords": [], "scores": {},
            "settings": dict(DEFAULT_SETTINGS),
            "layout": {"skill_lines": [], "experience": [], "projects": [], "education": []},
            "rewrites": {}, "chat": [], "build": {}}


# ---------- profile lookups ----------

def index(profile: dict) -> dict:
    entries, bullets, skills = {}, {}, {}
    for sec in SECTIONS:
        for e in profile[sec]:
            entries[e["id"]] = (sec, e)
            for b in e["bullets"]:
                bullets[b["id"]] = (e["id"], b)
    for s in profile["skills"]:
        skills[s["id"]] = s
    return {"entries": entries, "bullets": bullets, "skills": skills}


def usable(b: dict) -> bool:
    return b.get("status") != "draft" and b.get("evidence") != "planned"


def score(doc: dict, item_id: str) -> float:
    return float((doc.get("scores") or {}).get(item_id, {}).get("s", 0))


# ---------- layout <-> content ----------

def sanitize(doc: dict, profile: dict) -> list[str]:
    """Drop ids that no longer exist (or became drafts). Returns notes."""
    ix, notes = index(profile), []
    lay = doc["layout"]
    for sec in SECTIONS:
        kept = []
        for item in lay.get(sec, []):
            if item["id"] not in ix["entries"]:
                notes.append(f"entry {item['id']} no longer exists")
                continue
            item["bullets"] = [b for b in item.get("bullets", [])
                               if b in ix["bullets"] and ix["bullets"][b][0] == item["id"] and usable(ix["bullets"][b][1])]
            kept.append(item)
        lay[sec] = kept
    for ln in lay.get("skill_lines", []):
        ln["items"] = [s for s in ln.get("items", []) if s in ix["skills"]]
    lay["skill_lines"] = [ln for ln in lay.get("skill_lines", []) if ln["items"]]
    return notes


def bullet_text(doc: dict, b: dict) -> str:
    rw = (doc.get("rewrites") or {}).get(b["id"])
    if rw and rw.get("base") == b["text"]:
        return rw["text"]
    return b["text"]  # bank text changed since the rewrite -> bank wins


def resolve(doc: dict, profile: dict) -> dict:
    ix = index(profile)
    out = {"skill_lines": [], "experience": [], "projects": [], "education": []}
    for ln in doc["layout"]["skill_lines"]:
        items = []
        for sid in ln["items"]:
            s = ix["skills"][sid]
            items.append(f"**{s['name']}**" if s.get("bold") else s["name"])
        out["skill_lines"].append({"label": ln["label"], "items": items})
    for sec in SECTIONS:
        for item in doc["layout"][sec]:
            _, e = ix["entries"][item["id"]]
            bl = []
            for bid in item["bullets"]:
                _, b = ix["bullets"][bid]
                bl.append({"id": bid, "text": bullet_text(doc, b)})
            out[sec].append({**{k: v for k, v in e.items() if k != "bullets"}, "bullets": bl})
    return out


def plain_text(resolved: dict) -> str:
    parts = []
    for ln in resolved["skill_lines"]:
        parts.append(ln["label"] + " " + ", ".join(ln["items"]))
    for sec in SECTIONS:
        for e in resolved[sec]:
            parts += [e.get(k, "") for k in ("role", "org", "name", "tech", "school", "degree", "note")]
            parts += [b["text"] for b in e["bullets"]]
    return texgen.plain("\n".join(p for p in parts if p))


# ---------- automatic selection from scores ----------

def _groups(b: dict) -> list[str]:
    return list(b.get("groups") or [])


def auto_layout(doc: dict, profile: dict) -> None:
    st = {**DEFAULT_SETTINGS, **(doc.get("settings") or {})}
    sc = lambda i: score(doc, i)
    used_groups: set[str] = set()

    def pick(e, cap):
        cands = sorted([b for b in e["bullets"] if usable(b)], key=lambda b: -sc(b["id"]))
        chosen = []
        for b in cands:
            if len(chosen) >= cap:
                break
            if sc(b["id"]) < st["minScore"] and chosen:
                break
            if any(g in used_groups for g in _groups(b)):
                continue
            chosen.append(b)
            used_groups.update(_groups(b))
        return [b["id"] for b in chosen]

    lay = {"skill_lines": [], "experience": [], "projects": [], "education": []}
    for e in profile["experience"]:
        if sc(e["id"]) >= 2 or not doc.get("scores"):
            lay["experience"].append({"id": e["id"], "bullets": pick(e, st["maxExpBullets"])})
    projs = sorted([e for e in profile["projects"] if sc(e["id"]) >= st["minScore"]], key=lambda e: -sc(e["id"]))
    keep = {e["id"] for e in projs[: st["maxProjects"]]}
    for e in profile["projects"]:
        if e["id"] in keep:
            lay["projects"].append({"id": e["id"], "bullets": pick(e, st["maxProjBullets"])})
    for e in profile["education"]:
        bl = [b["id"] for b in e["bullets"] if usable(b) and sc(b["id"]) >= st["minScore"] + 2]
        lay["education"].append({"id": e["id"], "bullets": bl[:2]})

    # skills: group by the bank's default category, strongest categories first
    cats: dict[str, list] = {}
    for s in profile["skills"]:
        if sc(s["id"]) >= st["minScore"]:
            cats.setdefault(s.get("category") or "Other", []).append(s)
    ranked = sorted(cats.items(), key=lambda kv: -sum(sc(s["id"]) for s in kv[1]))[:MAX_SKILL_LINES]
    for cat, items in ranked:
        items.sort(key=lambda s: -sc(s["id"]))
        lay["skill_lines"].append({"label": cat.rstrip(":") + ":", "items": [s["id"] for s in items[:7]]})
    doc["layout"] = lay


# ---------- resume-level ops (from the AI or the UI) ----------

def apply_ops(doc: dict, profile: dict, ops: list[dict]) -> tuple[list[str], list[str]]:
    ix = index(profile)
    lay = doc["layout"]
    applied, errors = [], []

    def find(entry_id):
        for sec in SECTIONS:
            for i, item in enumerate(lay[sec]):
                if item["id"] == entry_id:
                    return sec, i, item
        return None

    for op in ops or []:
        try:
            kind = op.get("op")
            if kind in ("set_bullets", "include_entry"):
                eid = op.get("entry") or op.get("id")
                if eid not in ix["entries"]:
                    raise ResumeError(f"no entry '{eid}'")
                sec, e = ix["entries"][eid]
                bl = op.get("bullets", [])
                for bid in bl:
                    if bid not in ix["bullets"] or ix["bullets"][bid][0] != eid:
                        raise ResumeError(f"bullet '{bid}' is not under '{eid}'")
                    if not usable(ix["bullets"][bid][1]):
                        raise ResumeError(f"bullet '{bid}' is a draft/planned and can't go on a resume")
                found = find(eid)
                if found:
                    found[2]["bullets"] = list(dict.fromkeys(bl))
                else:
                    pos = op.get("index", len(lay[sec]))
                    lay[sec].insert(int(pos), {"id": eid, "bullets": list(dict.fromkeys(bl))})
                applied.append(f"{'Updated' if found else 'Added'} {eid} ({len(bl)} bullets)")
            elif kind == "remove_entry":
                found = find(op.get("entry") or op.get("id"))
                if not found:
                    raise ResumeError("entry not on this resume")
                lay[found[0]].pop(found[1])
                applied.append(f"Removed {found[2]['id']}")
            elif kind == "order_entries":
                sec = op["section"]
                cur = {i["id"]: i for i in lay[sec]}
                ids = [i for i in op["ids"] if i in cur]
                lay[sec] = [cur[i] for i in ids] + [v for k, v in cur.items() if k not in ids]
                applied.append(f"Reordered {sec}")
            elif kind == "rewrite":
                bid, text = op["bullet"], str(op["text"]).strip()
                if bid not in ix["bullets"]:
                    raise ResumeError(f"no bullet '{bid}'")
                base = ix["bullets"][bid][1]["text"]
                new_nums = set(re.findall(r"\d+(?:[.,]\d+)?", text)) - set(re.findall(r"\d+(?:[.,]\d+)?", base))
                if new_nums:
                    raise ResumeError(f"rewrite of {bid} adds numbers not in the original: {sorted(new_nums)}")
                if not text:
                    raise ResumeError("empty rewrite")
                doc.setdefault("rewrites", {})[bid] = {"text": text, "base": base}
                applied.append(f"Reworded {bid} for this resume")
            elif kind == "clear_rewrite":
                doc.get("rewrites", {}).pop(op["bullet"], None)
                applied.append(f"Restored original wording of {op['bullet']}")
            elif kind == "set_skill_lines":
                lines = []
                for ln in op["lines"][:MAX_SKILL_LINES]:
                    items = [s for s in ln.get("items", []) if s in ix["skills"]]
                    bad = [s for s in ln.get("items", []) if s not in ix["skills"]]
                    if bad:
                        raise ResumeError(f"unknown skill ids {bad} (add them to the bank first)")
                    if items:
                        lines.append({"label": str(ln["label"]).rstrip(":") + ":", "items": items})
                lay["skill_lines"] = lines
                applied.append(f"Set {len(lines)} skill lines")
            else:
                raise ResumeError(f"unknown op '{kind}'")
        except (ResumeError, KeyError, TypeError, ValueError) as e:
            errors.append(f"{op.get('op', '?')}: {e}")
    return applied, errors


# ---------- build + fit ----------

def stem(doc: dict, profile: dict) -> str:
    name = (profile.get("basics") or {}).get("name") or "Resume"
    title = re.sub(r"[^A-Za-z0-9]+", " ", doc["title"]).title().replace(" ", "")
    return f"{re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')}_Resume_{title or doc['slug']}"


async def build(doc: dict, profile: dict, render_png: bool = True) -> dict:
    sanitize(doc, profile)
    resolved = resolve(doc, profile)
    tex = texgen.build_tex(resolved)
    s = stem(doc, profile)
    out = po.BUILD / doc["slug"]
    info = await texgen.compile_tex(out, s, tex, po.STYLE, render_png)
    info.update({"stem": s, "text": plain_text(resolved), "at": time.time()})
    if render_png:  # final builds only; the fit loop's trial compiles skip this
        ix = index(profile)
        groups = {bid: _groups(b) for bid, (_, b) in ix["bullets"].items()}
        info["checks"] = checks.run(out / f"{s}.pdf", out / f"{s}.log", resolved, groups, info, doc.get("kind"))
    return info


def _est(doc, profile, bid):
    ix = index(profile)
    return texgen.est_bullet_pt(bullet_text(doc, ix["bullets"][bid][1]))


def _drop_candidates(doc):
    """(score, entry_item, bullet_id) for bullets that can go (entry keeps >=1)."""
    out = []
    for sec in SECTIONS:
        for item in doc["layout"][sec]:
            if len(item["bullets"]) > 1:
                out += [(score(doc, b), item, b) for b in item["bullets"]]
    return sorted(out, key=lambda t: (t[0], -t[1]["bullets"].index(t[2])))


def _add_candidates(doc, profile, relaxed=False):
    """Bullets that could be added to entries already on the page, best first.
    The per-entry caps only shape the first pick; leftover space goes to the
    most relevant remaining bullets wherever they are. In relaxed mode (lots of
    room left) the relevance bar drops a bit."""
    st = {**DEFAULT_SETTINGS, **doc.get("settings", {})}
    ix = index(profile)
    on = {b for sec in SECTIONS for i in doc["layout"][sec] for b in i["bullets"]}
    used = {g for b in on for g in _groups(ix["bullets"][b][1])}
    bar = max(2, st["minScore"] - 2) if relaxed else st["minScore"]
    out = []
    for sec in ("experience", "projects"):
        for item in doc["layout"][sec]:
            _, e = ix["entries"][item["id"]]
            for b in e["bullets"]:
                if b["id"] in on or not usable(b) or score(doc, b["id"]) < bar:
                    continue
                if any(g in used for g in _groups(b)):
                    continue
                out.append((score(doc, b["id"]), item, b["id"]))
    return sorted(out, key=lambda t: -t[0])


def _project_candidate(doc, profile, room):
    """A whole project not on the page yet (heading + its best 2 bullets)."""
    on = {i["id"] for i in doc["layout"]["projects"]}
    best = None
    for e in profile["projects"]:
        if e["id"] in on or score(doc, e["id"]) < 2:
            continue
        bl = sorted([b for b in e["bullets"] if usable(b)], key=lambda b: -score(doc, b["id"]))[:2]
        if not bl:
            continue
        h = 16 + sum(texgen.est_bullet_pt(b["text"]) for b in bl)
        if h <= room and (best is None or score(doc, e["id"]) > best[0]):
            best = (score(doc, e["id"]), e["id"], [b["id"] for b in bl])
    return best


async def fit(doc: dict, profile: dict, progress=None, fill: bool = True) -> dict:
    """Cut the lowest-scoring bullets until it's one page, then fill leftover
    space with the next-best bullets. Only uses scores, so it needs a tailored
    resume (base resumes are left as you wrote them)."""
    if not doc.get("scores"):
        return await build(doc, profile)
    info = await build(doc, profile, render_png=False)
    for _ in range(10):  # cut
        if info["pages"] <= 1:
            break
        need, cands = info["overflow_pt"] + 2, _drop_candidates(doc)
        if not cands:
            break
        while need > 0 and cands:
            _, item, bid = cands.pop(0)
            if len(item["bullets"]) <= 1:
                continue
            need -= _est(doc, profile, bid)
            item["bullets"].remove(bid)
        if progress:
            await progress("Cutting to one page…")
        info = await build(doc, profile, render_png=False)
    for _ in range(10 if fill else 0):  # fill
        if info["pages"] > 1:
            break
        room = info["slack_pt"] - 3
        if room < BULLET_MIN_PT:
            break
        snapshot = copy.deepcopy(doc["layout"])
        cands = [c for c in _add_candidates(doc, profile) if _est(doc, profile, c[2]) <= room]
        if not cands and room > 40:
            cands = [c for c in _add_candidates(doc, profile, relaxed=True) if _est(doc, profile, c[2]) <= room]
        if cands:
            _, item, bid = cands[0]
            item["bullets"].append(bid)
            item["bullets"].sort(key=lambda b: -score(doc, b))
        else:
            proj = _project_candidate(doc, profile, room)
            if not proj:
                break
            order = [e["id"] for e in profile["projects"]]
            doc["layout"]["projects"].append({"id": proj[1], "bullets": proj[2]})
            doc["layout"]["projects"].sort(key=lambda i: order.index(i["id"]) if i["id"] in order else 99)
        trial = await build(doc, profile, render_png=False)
        if trial["pages"] > 1:
            doc["layout"] = snapshot
            break
        info = trial
        if progress:
            await progress("Filling leftover space…")
    return await build(doc, profile)
