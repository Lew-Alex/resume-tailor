"""Prompt builders. Every prompt asks for a single JSON object back."""
from __future__ import annotations

import json

OPS_DOC = """\
Edit operations (reference things by their existing "id"):
  {"op":"add_bullet","parent":"<entry id>","bullet":{"text":"...","tags":["..."],"evidence":"implemented|experiment|planned","status":"draft","metrics_needed":true,"groups":["..."]}, "index":0}
  {"op":"update_bullet","id":"<bullet id>","fields":{"text":"...","tags":[...],"evidence":"...","status":"approved|draft","metrics_needed":false,"groups":[...]}}
  {"op":"remove_bullet","id":"<bullet id>"}
  {"op":"move_bullet","id":"<bullet id>","index":0}
  {"op":"add_entry","section":"experience|projects|education","entry":{...fields..., "bullets":[{...}]}}
      experience fields: org, role, location, start, end
      projects fields:   name, tech, link, start, end
      education fields:  school, degree, location, start, end, note
  {"op":"update_entry","id":"<entry id>","fields":{...same fields as above...}}
  {"op":"remove_entry","id":"<entry id>"}
  {"op":"add_skill","skill":{"name":"...","category":"<existing category if one fits>","bold":false}}
  {"op":"update_skill","id":"<skill id>","fields":{"name":"...","category":"...","bold":true}}
  {"op":"remove_skill","id":"<skill id>"}
  {"op":"update_basics","fields":{"name":"...","email":"...","phone":"...","location":"...","links":[{"label":"...","url":"..."}]}}
"index" is optional. Omit "id" on new items; the app assigns ids.
"""

RULES = """\
Rules:
- NEVER invent facts, numbers, tools, results or employers. Use only what the
  user said or what is already in the profile. If a bullet would be stronger
  with a number you don't have, write it without one, set "metrics_needed":true,
  and ask the user for the number.
- New bullets you write get "status":"draft". Only set "approved" if the user
  explicitly approves.
- "evidence" must be honest: "implemented" = built and working, "experiment" =
  tried/evaluated, "planned" = not done yet. Never upgrade evidence on your own.
- Markup: wrap a few key terms in **double asterisks** to bold them (the
  user's style bolds 1-3 standout tools/results per bullet). No other markup.
- "groups": bullets that describe the same achievement share a group name, so a
  resume never shows two of them. Reuse existing group names when you add an
  alternative wording of an existing bullet.
- Resume bullets: start with a strong past-tense verb, say what was built, how,
  and the result. One line (~110-140 characters) is ideal; two lines max.
- When the user wants help writing bullets, interview them: ask 1-3 focused
  questions (what did you build, with what, what was the result/number) before
  drafting. It's fine to return no ops and just ask questions.
- Only make the changes the user asked for. Don't reorganize unrelated items.
"""


def chat_prompt(profile: dict, history: list[dict], message: str) -> str:
    convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history[-12:])
    return f"""You are the assistant inside a local resume-builder app. The user's
master profile (a bank of ALL their experience, projects, skills and bullets) is
below as JSON. You change it by returning edit operations, which the app
validates and applies. The user sees the result live.

{OPS_DOC}
{RULES}
Reply with ONLY a JSON object, no prose outside it:
{{"reply": "<short message to the user: what you changed, or your questions>",
  "ops": [ ...zero or more edit operations... ]}}

PROFILE:
{json.dumps(profile, indent=1)}

CONVERSATION SO FAR:
{convo or "(none)"}

USER MESSAGE:
{message}
"""


def tailor_items(profile: dict) -> list[dict]:
    """Compact list of everything the AI should score."""
    items = []
    for sec in ("experience", "projects", "education"):
        for e in profile[sec]:
            head = e.get("role") or e.get("name") or e.get("degree") or ""
            org = e.get("org") or e.get("tech") or e.get("school") or ""
            items.append({"id": e["id"], "kind": sec, "title": f"{head} — {org}".strip(" —")})
            for b in e["bullets"]:
                if b.get("status") == "draft" or b.get("evidence") == "planned":
                    continue
                items.append({"id": b["id"], "kind": "bullet", "parent": e["id"],
                              "text": b["text"], "tags": b.get("tags", [])})
    for s in profile["skills"]:
        items.append({"id": s["id"], "kind": "skill", "text": s["name"], "category": s["category"]})
    return items


def tailor_prompt(profile: dict, job: str) -> str:
    items = tailor_items(profile)
    return f"""TASK: TAILOR
You are helping pick which items from a candidate's master resume go on a
one-page resume for a specific job. You do NOT write or rewrite anything here:
you only read the job description and score each item for relevance.

Score every item 0-10:
  10 = directly matches a core requirement of the job
   6 = clearly relevant / transferable
   3 = weakly relevant
   0 = irrelevant
Score experience/project entries on how relevant the entry as a whole is.
Give a short reason (under 12 words) per item. Be discriminating: not everything
deserves 6+.

Reply with ONLY this JSON object:
{{"title": "<job title>", "company": "<company or empty>",
  "keywords": ["<8-20 important skills/keywords from the job description>"],
  "scores": {{"<item id>": {{"s": <0-10>, "why": "<reason>"}}, ...every item id...}},
  "summary": "<2 sentences: what this job wants most and which of the candidate's experience fits best>"}}

JOB DESCRIPTION:
{job}

ITEMS:
{json.dumps(items, indent=1)}
"""


RESUME_OPS_DOC = """\
Resume operations (change ONLY this resume; the master bank stays as is):
  {"op":"set_bullets","entry":"<entry id>","bullets":["<bullet id>", ...]}   # choose + order bullets; adds the entry if missing
  {"op":"remove_entry","entry":"<entry id>"}
  {"op":"order_entries","section":"experience|projects|education","ids":[...]}
  {"op":"rewrite","bullet":"<bullet id>","text":"..."}       # this-resume-only wording of a bank bullet
  {"op":"clear_rewrite","bullet":"<bullet id>"}
  {"op":"set_skill_lines","lines":[{"label":"Firmware:","items":["<skill id>", ...]}, ...]}   # max 6 lines, shown in 2 columns
"""

REFINE_RULES = """\
Rules:
- NEVER add facts. A "rewrite" may only re-express what the original bullet
  already says: tighten it, reorder it, or use the job's terminology for the
  same thing. Never add numbers, tools, results or scope. The app rejects
  rewrites that contain numbers the original doesn't have.
- Only bullet ids listed below can be used. Drafts/planned items are not
  available. If the user tells you a NEW fact, add it to the bank with a
  profile op (status "draft") and tell them to approve it in the Bank first.
- Hard limit: ONE page. Bullets are ~118 characters per line, ~13.2pt per
  line. Prefer one-line bullets; never more than two lines.
- Use the page: a finished one-pager should have under ~25pt free. If there
  is much more room, add the next most relevant available bullets (moderately
  relevant is fine) or bring back a project. Don't leave half the page blank.
- At most one bullet per "groups" value (they say the same thing).
- Choose what is most relevant to the job. Keep the strongest evidence of
  real, working results near the top of each entry.
- Bold (**...**) 1-3 standout terms per bullet, as in the existing bullets.
- Skill lines: 4-6 lines, short labels ending in ":", relevant skills first,
  only skill ids that exist (add new skills to the bank with a profile op).
- Typography: no bullet may end with a nearly empty last line (1-2 words,
  e.g. "notebook." alone). Fix those by trimming the bullet so it ends a line
  sooner: drop filler words or a redundant clause, never a key fact or number.
  Keep normal, readable grammar: don't drop articles ("the", "a") or
  conjunctions to save space, and don't produce telegraphic fragments. If a
  bullet can't lose a line gracefully, make it slightly longer so the last line
  is fuller, or restructure the sentence.
  BAD (telegraphic): "Won: regional hackathon; only team with live hardware demo, custom PCB."
  GOOD (restructured): "Won a regional hackathon as the only team with a live hardware demo on a custom PCB."
  Every rewrite must read as a normal resume sentence starting with a past-tense verb.
  Keep skill lines from wrapping onto a line with one leftover item.
- The FORMATTING CHECKS section lists problems measured in the real compiled
  PDF. Don't introduce new ones; fix them when asked.
- Make only the changes the user asked for (or, for an automatic refinement
  pass, the few changes that clearly improve fit to the job).
"""


def _resume_view(doc: dict, profile: dict, resolve, index) -> dict:
    ix = index(profile)
    sc = doc.get("scores") or {}
    on = set()
    current = {"skill_lines": [
        {"label": ln["label"], "items": [{"id": s, "name": ix["skills"][s]["name"]} for s in ln["items"]]}
        for ln in doc["layout"]["skill_lines"]]}
    for sec in ("experience", "projects", "education"):
        current[sec] = []
        for item in doc["layout"][sec]:
            _, e = ix["entries"][item["id"]]
            bl = []
            for bid in item["bullets"]:
                on.add(bid)
                b = ix["bullets"][bid][1]
                rw = (doc.get("rewrites") or {}).get(bid)
                row = {"id": bid, "text": b["text"], "score": sc.get(bid, {}).get("s"), "groups": b.get("groups", [])}
                if rw and rw.get("base") == b["text"]:
                    row["rewritten_as"] = rw["text"]
                bl.append(row)
            head = e.get("role") or e.get("name") or e.get("school")
            current[sec].append({"id": e["id"], "title": head, "score": sc.get(e["id"], {}).get("s"), "bullets": bl})
    pool = []
    for sec in ("experience", "projects", "education"):
        for e in profile[sec]:
            avail = [{"id": b["id"], "text": b["text"], "score": sc.get(b["id"], {}).get("s"), "groups": b.get("groups", [])}
                     for b in e["bullets"] if b["id"] not in on and b.get("status") != "draft" and b.get("evidence") != "planned"]
            if avail:
                pool.append({"entry": e["id"], "section": sec,
                             "title": e.get("role") or e.get("name") or e.get("school"), "available_bullets": avail})
    skills = [{"id": s["id"], "name": s["name"], "category": s.get("category"), "score": sc.get(s["id"], {}).get("s")}
              for s in profile["skills"]]
    return {"current": current, "pool": pool, "skills": skills}


def refine_prompt(doc: dict, profile: dict, message: str, resolve, index, auto: bool = False) -> str:
    view = _resume_view(doc, profile, resolve, index)
    b = doc.get("build") or {}
    if b.get("pages", 1) > 1:
        fitline = f"OVER ONE PAGE by about {b.get('overflow_pt', 0):.0f}pt. You must cut or shorten."
    else:
        fitline = f"Fits on one page with {b.get('slack_pt', 0):.0f}pt of free space at the bottom."
    checks_txt = "\n".join(f"- [{i['level']}] {i['msg']} (ids: {', '.join(i['ids']) or '-'})"
                           for i in b.get("checks") or []) or "- none"
    convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in (doc.get("chat") or [])[-10:])
    job = doc.get("job") or "(none: this is a general-purpose base resume)"
    task = ("Do one automatic refinement pass: make the few changes that most improve this "
            "resume for the job. Use the free space well, but stay on one page."
            if auto else message)
    return f"""You are refining ONE resume inside a local resume app.
The resume is built from the user's master bank and rendered with their
exact LaTeX template, so you never write LaTeX: you return operations.

{RESUME_OPS_DOC}
Profile (master bank) operations are also available for NEW facts the user gives you:
{OPS_DOC}
{REFINE_RULES}
Reply with ONLY a JSON object:
{{"reply": "<short note to the user: what you changed and why, or a question>",
  "resume_ops": [...], "profile_ops": [...]}}

RESUME: {doc['title']}{(' @ ' + doc['company']) if doc.get('company') else ''}
PAGE FIT: {fitline}
FORMATTING CHECKS (measured in the compiled PDF):
{checks_txt}
JOB DESCRIPTION:
{job}

JOB KEYWORDS: {', '.join(doc.get('keywords') or []) or '(none)'}

CURRENT RESUME (ids, bank text, relevance score 0-10):
{json.dumps(view['current'], indent=1)}

BULLETS AVAILABLE BUT NOT ON THIS RESUME:
{json.dumps(view['pool'], indent=1)}

ALL SKILLS IN THE BANK:
{json.dumps(view['skills'])}

CONVERSATION ABOUT THIS RESUME SO FAR:
{convo or '(none)'}

USER MESSAGE:
{task}
"""
