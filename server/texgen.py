"""Turn a resume layout into LaTeX using the user's own resume_style.tex macros,
compile it with Tectonic (XeLaTeX), and measure how full the page is.

Bullet/skill text uses a tiny markup: **bold** -> \\textbf{bold}. Everything
else is escaped, so the AI can never inject raw LaTeX.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
from pathlib import Path

TECTONIC = os.environ.get("TECTONIC_BIN") or shutil.which("tectonic") or "/opt/homebrew/bin/tectonic"
PDFTOPPM = shutil.which("pdftoppm") or "/opt/homebrew/bin/pdftoppm"

# Rough geometry of the user's template, used to estimate bullet heights
# before compiling. Real fit is always confirmed by an actual compile.
BULLET_LINE_PT = 13.15
BULLET_SEP_PT = 1.05
CHARS_PER_LINE = 118

_ESC = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_SPECIAL = {"±": r"$\pm$", " ": "~", "–": "--", "—": "---", "×": r"$\times$", "°": r"$^\circ$",
            "≈": r"$\approx$", "→": r"$\rightarrow$"}


def esc(text: str) -> str:
    out = []
    for ch in str(text):
        out.append(_ESC.get(ch) or _SPECIAL.get(ch) or ch)
    return "".join(out)


def md(text: str) -> str:
    """Escape, then turn **bold** into \\textbf{bold}."""
    parts = re.split(r"\*\*(.+?)\*\*", str(text))
    return "".join(r"\textbf{" + esc(p) + "}" if i % 2 else esc(p) for i, p in enumerate(parts))


def plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(text))


def est_bullet_pt(text: str) -> float:
    lines = max(1, math.ceil(len(plain(text)) / CHARS_PER_LINE))
    return lines * BULLET_LINE_PT + BULLET_SEP_PT


SKILLS_GAP_PT = 0.44         # with the \strut on each skill line, gives the same 4.46pt gap as other headings
SKILL_CHARS_PER_LINE = 54    # half-width column


def skill_line_count(ln: dict) -> int:
    chars = len(ln["label"]) + 1 + len(plain(", ".join(ln["items"])))
    return max(1, math.ceil(chars / SKILL_CHARS_PER_LINE))


def skill_split(lines: list[dict]) -> int:
    """Where to split the (ordered) skill lines into left/right columns so the
    two columns end up about the same height. Order is preserved."""
    n = len(lines)
    if n < 2:
        return n
    counts = [skill_line_count(l) for l in lines]
    mid = math.ceil(n / 2)
    best = None
    for k in range(max(1, mid - 1), min(n - 1, mid + 1) + 1):
        diff = abs(sum(counts[:k]) - sum(counts[k:]))
        key = (diff, abs(k - mid))
        if best is None or key < best[0]:
            best = (key, k)
    return best[1]


def dates(e: dict) -> str:
    # str(): YAML reads a bare year like `start: 2025` as an int
    return esc(" - ".join(str(x) for x in (e.get("start"), e.get("end")) if x))


# ---------- build the .tex ----------

def build_tex(resolved: dict) -> str:
    """`resolved` is a layout with the actual entry/bullet/skill content filled in
    (see resumes.resolve)."""
    L = [r"\documentclass[11pt]{article}", r"\input{resume_style.tex}", "", r"\begin{document}", r"\resumeHeader", ""]

    lines = resolved["skill_lines"]
    if lines:
        half = skill_split(lines)
        cols = [lines[:half], lines[half:]]
        L.append(r"\section*{Skills}\resumeRule")
        for ci, col in enumerate(cols):
            if not col:
                continue
            L.append(r"\begin{minipage}[t]{0.49\textwidth}\raggedright")
            for ln in col:
                items = ", ".join(md(s) for s in ln["items"])
                L.append(f"  \\skillline{{{esc(ln['label'])}}}{{{items}\\strut}}")
            L.append(r"\end{minipage}" + (r"\hfill" if ci == 0 and cols[1] else ""))
        # Top-aligned minipages make LaTeX lose the normal baseline spacing, so
        # the next heading would sit ~1-3pt from the skills. Set the gap
        # explicitly to match the space above every other section heading.
        L.append(r"\par\nointerlineskip\vspace{SKILLS_GAP}".replace("SKILLS_GAP", f"{SKILLS_GAP_PT}pt"))
        L.append("")

    def bullets(bs):
        if not bs:
            return []
        out = [r"\begin{resumeBullets}"]
        out += [f"  \\item {md(b['text'])}" for b in bs]
        out.append(r"\end{resumeBullets}")
        return out

    if resolved["experience"]:
        L.append(r"\section*{Experience}\resumeRule")
        for i, e in enumerate(resolved["experience"]):
            if i:
                L += ["", r"\vspace{3pt}"]
            L.append(f"\\roleheading{{{md(e.get('role', ''))}}}{{{dates(e)}}}")
            L.append(f"\\organization{{{md(e.get('org', ''))}}}")
            L += bullets(e["bullets"])
        L.append("")

    if resolved["projects"]:
        L.append(r"\section*{Projects}\resumeRule")
        for i, e in enumerate(resolved["projects"]):
            if i:
                L.append("")
            L.append(f"\\projectheading{{{md(e.get('name', ''))}}}{{{md(e.get('tech', ''))}}}{{{dates(e)}}}")
            L += bullets(e["bullets"])
        L.append("")

    if resolved["education"]:
        L.append(r"\section*{Education}\resumeRule")
        for i, e in enumerate(resolved["education"]):
            if i:
                L += ["", r"\vspace{3pt}"]
            head = " - ".join(x for x in (e.get("school"), e.get("degree")) if x)
            L.append(f"\\educationheading{{{md(head)}}}{{{dates(e)}}}")
            if e.get("note"):
                L.append(f"\\organization{{{md(e['note'])}}}")
            L += bullets(e["bullets"])
        L.append("")

    # Probe: lets the app measure page fill without parsing the PDF.
    L.append(r"\par\typeout{RTFIT pages=\thepage\space used=\the\pagetotal\space goal=\the\pagegoal}")
    L.append(r"\end{document}")
    return "\n".join(L) + "\n"


# ---------- compile ----------

class CompileError(RuntimeError):
    pass


async def compile_tex(workdir: Path, stem: str, tex: str, style: Path, render_png: bool) -> dict:
    """Write + compile. Returns {pages, used_pt, goal_pt, slack_pt}."""
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(style, workdir / "resume_style.tex")
    (workdir / f"{stem}.tex").write_text(tex)
    proc = await asyncio.create_subprocess_exec(
        TECTONIC, "--keep-logs", "--chatter", "minimal", f"{stem}.tex",
        cwd=workdir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 240)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise CompileError("LaTeX compile timed out") from e
    if proc.returncode != 0:
        msg = out.decode(errors="replace")
        errs = [l for l in msg.splitlines() if "error" in l.lower()]
        raise CompileError("\n".join(errs[-6:]) or msg[-1200:])

    log = (workdir / f"{stem}.log").read_text(errors="replace")
    m = re.search(r"RTFIT pages=(\d+) used=([\d.]+)pt goal=([\d.]+)pt", log)
    pages, used, goal = (int(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (1, 0.0, 0.0)
    info = {"pages": pages, "used_pt": used, "goal_pt": goal,
            "slack_pt": round(goal - used, 1) if pages == 1 else 0.0,
            "overflow_pt": round(used, 1) if pages > 1 else 0.0}

    if render_png:
        for old in workdir.glob("page-*.png"):
            old.unlink()
        p = await asyncio.create_subprocess_exec(
            PDFTOPPM, "-r", "144", "-png", f"{stem}.pdf", "page", cwd=workdir,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.wait()
        info["png"] = sorted(x.name for x in workdir.glob("page-*.png"))
    return info
