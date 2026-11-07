"""Formatting checks run on every compiled resume.

They read the real PDF (word positions from `pdftotext -bbox-layout`) and the
LaTeX log, so they see what a recruiter sees, not an estimate. Each issue:
  {"level": "error"|"warn"|"info", "kind": str, "msg": str, "ids": [bullet ids], "fix": str}

"fix" is an instruction the refine step hands to Claude for automatic fixing.
"""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess
from pathlib import Path

PDFTOTEXT = shutil.which("pdftotext") or "/opt/homebrew/bin/pdftotext"
SECTIONS = ("Skills", "Experience", "Projects", "Education")
PAGE_W = 612.0          # letter, pt
MARGIN = 0.4 * 72       # from resume_style.tex
TEXT_RIGHT = PAGE_W - MARGIN
RUNT_WORDS = 2          # a last line with this many words or fewer is a runt...
RUNT_FRAC = 0.22        # ...as is one shorter than this fraction of the line width
SECTION_GAP_TOL = 1.2   # pt difference from the typical gap above headings
UNDERFILL_PT = 40


def _lines(pdf: Path) -> list[dict]:
    out = subprocess.run([PDFTOTEXT, "-bbox-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    pages = out.split("<page ")[1:] or [out]
    lines = []
    for pi, page in enumerate(pages):
        for m in re.finditer(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', page, re.S):
            words = [w.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                     for w in re.findall(r">([^<]*)</word>", m.group(5))]
            lines.append({"page": pi, "x0": float(m.group(1)), "y0": float(m.group(2)),
                          "x1": float(m.group(3)), "y1": float(m.group(4)), "words": words,
                          "text": " ".join(words)})
    lines.sort(key=lambda l: (l["page"], round(l["y0"], 1), l["x0"]))
    return lines


def run(pdf: Path, log: Path, resolved: dict, groups: dict[str, list[str]], build: dict, kind: str) -> list[dict]:
    issues: list[dict] = []
    add = lambda level, k, msg, ids=(), fix="": issues.append(
        {"level": level, "kind": k, "msg": msg, "ids": list(ids), "fix": fix})

    if not pdf.exists():
        return issues
    lines = _lines(pdf)

    # ---- page fit ----
    if build.get("pages", 1) > 1:
        add("error", "overflow", f"Runs onto a second page (about {build.get('overflow_pt', 0):.0f}pt over).",
            fix="Cut or shorten bullets until everything fits on one page.")
    elif build.get("slack_pt", 0) > UNDERFILL_PT and kind != "base":
        add("warn", "underfill", f"{build['slack_pt']:.0f}pt of empty space at the bottom of the page.",
            fix="Use the empty space: add the next most relevant available bullets or a project.")

    # ---- section heading spacing ----
    heads = [i for i, l in enumerate(lines) if l["text"] in SECTIONS and l["page"] == 0]
    gaps = {}
    for i in heads:
        above = [l["y1"] for l in lines[:i] if l["page"] == lines[i]["page"] and l["y1"] <= lines[i]["y0"] + 0.5]
        if above:
            gaps[lines[i]["text"]] = lines[i]["y0"] - max(above)
    body_gaps = [g for name, g in gaps.items() if name != "Skills"]
    if len(body_gaps) >= 2:
        typical = statistics.median(body_gaps)
        for name, g in gaps.items():
            if name != "Skills" and abs(g - typical) > SECTION_GAP_TOL:
                add("warn", "spacing", f"Space above “{name}” is {g:.1f}pt; other headings get {typical:.1f}pt.")

    # ---- margins / overfull boxes ----
    for l in lines:
        if l["x1"] > TEXT_RIGHT + 1.5:
            add("error", "margin", f"Text runs into the right margin: “{l['text'][-50:]}”.",
                fix="Shorten the line that runs into the margin.")
    if log.exists():
        for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt too wide\)[^\n]*\n(.*)", log.read_text(errors="replace")):
            if float(m.group(1)) > 1.0:
                add("warn", "overfull", f"A line is {float(m.group(1)):.1f}pt too wide near “{m.group(2).strip()[:60]}”.")

    # ---- bullets: map PDF bullets to ids in order ----
    order = [b for sec in ("experience", "projects", "education") for e in resolved[sec] for b in e["bullets"]]
    groups_pdf: list[list[dict]] = []
    cur = None
    for l in lines:
        if l["words"] and l["words"][0] == "•":
            cur = [l]
            groups_pdf.append(cur)
        elif (cur is not None and cur[0]["x0"] + 8 < l["x0"] < cur[0]["x0"] + 25
              and 0 < l["y0"] - cur[-1]["y1"] < 6):
            cur.append(l)   # wrapped line, indented to the bullet text (not e.g. a right-aligned date)
        else:
            cur = None
    if len(groups_pdf) == len(order):
        for b, bl in zip(order, groups_pdf):
            width = TEXT_RIGHT - bl[0]["x0"]
            last = bl[-1]
            cut = len(last["text"]) + 4   # characters to remove to lose the last line
            per_line = max(len(l["text"]) for l in bl)
            if len(bl) > 2:
                add("warn", "long", f"Bullet runs {len(bl)} lines: “{_short(b['text'])}”.", [b["id"]],
                    fix=f"Shorten bullet {b['id']} to at most two lines: its last line holds {len(last['text'])} "
                        f"characters, so remove at least {cut} characters (a full line is ~{per_line}).")
            if len(bl) > 1:
                frac = (last["x1"] - last["x0"]) / width
                if len(last["words"]) <= RUNT_WORDS or frac < RUNT_FRAC:
                    add("warn", "runt", f"Bullet ends with a nearly empty line (“{last['text']}”): “{_short(b['text'])}”.",
                        [b["id"]], fix=f"Bullet {b['id']} wraps to {len(bl)} lines, leaving only “{last['text']}” on the last one. "
                                       f"Either remove at least {cut} characters so it ends a line sooner, or add "
                                       f"~{max(25, per_line // 3)} characters of real detail from the original so the last line is fuller. "
                                       f"Count characters; a full line holds about {per_line}.")
            for a, nxt in zip(bl, bl[1:]):
                if a["words"] and a["words"][-1].endswith("-") and nxt["words"] and nxt["words"][0][:1].islower():
                    add("info", "hyphen", f"A word is hyphenated across lines in “{_short(b['text'])}”.", [b["id"]],
                        fix=f"Reword bullet {b['id']} slightly so no word is split across lines.")

    # ---- skills block: column balance + runts ----
    if "Skills" in gaps and heads:
        top = next(l["y1"] for l in lines if l["text"] == "Skills")
        nxt = [lines[i]["y0"] for i in heads if lines[i]["text"] != "Skills" and lines[i]["y0"] > top]
        bottom = min(nxt) if nxt else 1e9
        block = [l for l in lines if top < l["y0"] < bottom and l["page"] == 0]
        left = [l for l in block if l["x0"] < PAGE_W / 2 - 10]
        right = [l for l in block if l["x0"] >= PAGE_W / 2 - 10]
        if left and right and abs(len(left) - len(right)) >= 2:
            add("warn", "skills-balance", f"Skills columns are uneven ({len(left)} vs {len(right)} lines).",
                fix="Rebalance skill lines (move or trim items) so both columns are about the same height.")
        colw = PAGE_W / 2 - MARGIN - 6
        for col in (left, right):
            for a, b2 in zip(col, col[1:]):
                if (b2["x1"] - b2["x0"]) < 0.22 * colw and len(b2["words"]) <= 2 and (a["x1"] - a["x0"]) > 0.7 * colw:
                    add("info", "skills-runt", f"A skill line wraps with only “{b2['text']}” on its second line.",
                        fix=f"Trim or regroup skill items so “{b2['text']}” doesn't wrap onto its own line.")

    # ---- content-level ----
    seen: dict[str, str] = {}
    for b in order:
        for g in groups.get(b["id"], []):
            if g in seen and seen[g] != b["id"]:
                add("warn", "duplicate", f"Two bullets describe the same thing ({seen[g]} and {b['id']}).",
                    [seen[g], b["id"]], fix=f"Keep only one of {seen[g]} / {b['id']}; they share group '{g}'.")
            seen.setdefault(g, b["id"])
        if len(re.findall(r"\*\*(.+?)\*\*", b["text"])) > 3:
            add("info", "bold", f"Lots of bold in “{_short(b['text'])}”.", [b["id"]],
                fix=f"Bold at most 3 terms in bullet {b['id']}.")
    for sec in ("experience", "projects"):
        for e in resolved[sec]:
            if not e["bullets"]:
                add("warn", "empty", f"“{e.get('role') or e.get('name')}” has no bullets.",
                    fix=f"Give entry {e['id']} at least one bullet or remove it.")

    order_lv = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: order_lv[i["level"]])
    return issues


def _short(text: str, n: int = 60) -> str:
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def fix_instructions(issues: list[dict]) -> str:
    todo = [i for i in issues if i.get("fix")]
    if not todo:
        return ""
    return "Fix these formatting problems found in the compiled PDF:\n" + "\n".join(f"- {i['fix']}" for i in todo)
