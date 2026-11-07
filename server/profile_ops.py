"""The profile file, its ID-based edit operations, validation and undo history.

The AI never rewrites the whole file. It returns a list of small ops that
reference items by ID; we validate each one and apply them here.
"""
from __future__ import annotations

import copy
import re
import shutil
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROFILE = DATA / "profile.yaml"
EXAMPLE = ROOT / "profile.example.yaml"
HISTORY = DATA / "history"
RESUMES = DATA / "resumes"      # one JSON per resume (layout, job, chat)
BUILD = DATA / "build"          # generated .tex / .pdf / previews per resume
STYLE = DATA / "resume_style.tex"
EXAMPLE_STYLE = ROOT / "latex" / "resume_style.example.tex"

SECTIONS = ("education", "experience", "projects")
EVIDENCE = ("implemented", "experiment", "planned")
STATUS = ("approved", "draft")
ENTRY_FIELDS = {
    "education": {"school", "degree", "location", "start", "end", "note"},
    "experience": {"org", "role", "location", "start", "end"},
    "projects": {"name", "tech", "link", "start", "end"},
}
BULLET_FIELDS = {"text", "tags", "evidence", "status", "metrics_needed", "groups"}
SKILL_FIELDS = {"name", "category", "bold"}
BASICS_FIELDS = {"name", "email", "phone", "location", "links"}
MAX_HISTORY = 50


class OpError(ValueError):
    pass


# ---------- file IO ----------

def ensure_data() -> None:
    DATA.mkdir(exist_ok=True)
    HISTORY.mkdir(exist_ok=True)
    RESUMES.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    if not PROFILE.exists():
        shutil.copy(EXAMPLE, PROFILE)
    if not STYLE.exists():
        shutil.copy(EXAMPLE_STYLE, STYLE)


def load() -> dict:
    ensure_data()
    data = yaml.safe_load(PROFILE.read_text()) or {}
    return normalize(data)


def _id_first(x):
    if isinstance(x, dict):
        out = {"id": x["id"]} if "id" in x else {}
        out.update({k: _id_first(v) for k, v in x.items() if k != "id"})
        return out
    if isinstance(x, list):
        return [_id_first(v) for v in x]
    return x


def dump_yaml(profile: dict) -> str:
    return yaml.safe_dump(_id_first(profile), sort_keys=False, allow_unicode=True, width=1000)


# Last profile content this process knows about. Used so edits made outside the
# app (your editor, Claude Code in the terminal) also get an undo snapshot.
_known: str | None = None


def _snapshot(text: str) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
    (HISTORY / f"{stamp}.yaml").write_text(text)
    for old in sorted(HISTORY.glob("*.yaml"))[:-MAX_HISTORY]:
        old.unlink()


def _write(text: str) -> None:
    global _known
    _known = text
    tmp = PROFILE.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(PROFILE)


def remember_current() -> None:
    global _known
    ensure_data()
    _known = PROFILE.read_text()


def note_external_change() -> None:
    """Called by the file watcher. If the file changed behind our back, keep the
    previous version in history so Undo can get back to it."""
    global _known
    cur = PROFILE.read_text() if PROFILE.exists() else ""
    if _known is not None and cur != _known:
        _snapshot(_known)
    _known = cur


def save(profile: dict) -> None:
    """Snapshot the current file into history, then write atomically."""
    ensure_data()
    if PROFILE.exists():
        _snapshot(PROFILE.read_text())
    _write(dump_yaml(normalize(profile)))


def undo() -> bool:
    snaps = sorted(HISTORY.glob("*.yaml"))
    if not snaps:
        return False
    last = snaps[-1]
    _write(last.read_text())
    last.unlink()
    return True


def parse_yaml_text(text: str) -> dict:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise OpError(f"YAML error: {e}") from e
    if not isinstance(data, dict):
        raise OpError("Top level of the profile must be a mapping.")
    return normalize(data)


# ---------- normalization ----------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:24] or "item"


def all_ids(profile: dict) -> set[str]:
    ids = set()
    for sec in SECTIONS:
        for e in profile.get(sec, []):
            ids.add(e["id"])
            ids.update(b["id"] for b in e.get("bullets", []))
    ids.update(s["id"] for s in profile.get("skills", []))
    return ids


def _unique(base: str, taken: set[str]) -> str:
    cand, n = base, 2
    while cand in taken:
        cand, n = f"{base}-{n}", n + 1
    taken.add(cand)
    return cand


def normalize(p: dict) -> dict:
    """Fill defaults and give every entry, bullet and skill a unique id.

    Existing ids are kept; only missing (or duplicate) ids are generated, and
    generated ids never take an id that is already in use.
    """
    p.setdefault("basics", {})
    for key in (*SECTIONS, "skills"):
        p[key] = p.get(key) or []
    reserved: set[str] = set()
    for sec in SECTIONS:
        for e in p[sec]:
            if e.get("id"):
                reserved.add(str(e["id"]))
            for b in e.get("bullets") or []:
                if isinstance(b, dict) and b.get("id"):
                    reserved.add(str(b["id"]))
    for s in p["skills"]:
        if isinstance(s, dict) and s.get("id"):
            reserved.add(str(s["id"]))

    taken: set[str] = set()

    def assign(explicit, fallback: str) -> str:
        if explicit and str(explicit) not in taken:
            taken.add(str(explicit))
            return str(explicit)
        return _unique(fallback, taken | reserved)

    for sec in SECTIONS:
        for e in p[sec]:
            label = e.get("org") or e.get("name") or e.get("school") or sec
            e["id"] = assign(e.get("id"), _slug(label))
            taken.add(e["id"])
            bullets = e.get("bullets") or []
            fixed = []
            for i, b in enumerate(bullets, 1):
                if isinstance(b, str):
                    b = {"text": b}
                b["id"] = assign(b.get("id"), f"{e['id']}-{i}")
                taken.add(b["id"])
                b.setdefault("tags", [])
                b.setdefault("evidence", "implemented")
                b.setdefault("status", "approved")
                fixed.append(b)
            e["bullets"] = fixed
    skills = []
    for s in p["skills"]:
        if isinstance(s, str):
            s = {"name": s}
        s.setdefault("category", "Other")
        s["id"] = assign(s.get("id"), "skill-" + _slug(s["name"]))
        taken.add(s["id"])
        skills.append(s)
    p["skills"] = skills
    return p


# ---------- lookup ----------

def _find_entry(p: dict, eid: str):
    for sec in SECTIONS:
        for i, e in enumerate(p[sec]):
            if e["id"] == eid:
                return sec, i, e
    raise OpError(f"No entry with id '{eid}'")


def _find_bullet(p: dict, bid: str):
    for sec in SECTIONS:
        for e in p[sec]:
            for i, b in enumerate(e["bullets"]):
                if b["id"] == bid:
                    return e, i, b
    raise OpError(f"No bullet with id '{bid}'")


def _find_skill(p: dict, sid: str):
    for i, s in enumerate(p["skills"]):
        if s["id"] == sid:
            return i, s
    raise OpError(f"No skill with id '{sid}'")


def _check_fields(fields: dict, allowed: set[str], what: str) -> dict:
    if not isinstance(fields, dict):
        raise OpError(f"{what}: fields must be an object")
    bad = set(fields) - allowed
    if bad:
        raise OpError(f"{what}: fields not allowed: {sorted(bad)}")
    if "evidence" in fields and fields["evidence"] not in EVIDENCE:
        raise OpError(f"{what}: evidence must be one of {EVIDENCE}")
    if "status" in fields and fields["status"] not in STATUS:
        raise OpError(f"{what}: status must be one of {STATUS}")
    return fields


# ---------- ops ----------

def apply_ops(profile: dict, ops: list[dict]) -> tuple[dict, list[str], list[str]]:
    """Apply ops to a copy. Returns (new_profile, applied_descriptions, errors).

    Each op is validated on its own; a bad op is skipped and reported instead of
    sinking the whole batch.
    """
    p = copy.deepcopy(profile)
    applied, errors = [], []
    for op in ops or []:
        try:
            applied.append(_apply_one(p, op))
            normalize(p)  # assign ids to anything just added
        except (OpError, KeyError, TypeError) as e:
            errors.append(f"{op.get('op', '?')}: {e}")
    return p, applied, errors


def _apply_one(p: dict, op: dict) -> str:
    kind = op.get("op")
    if kind == "add_bullet":
        _, _, e = _find_entry(p, op["parent"])
        b = _check_fields(dict(op["bullet"]), BULLET_FIELDS | {"id"}, "add_bullet")
        if not b.get("text"):
            raise OpError("bullet needs text")
        b.setdefault("status", "draft")
        e["bullets"].insert(op.get("index", len(e["bullets"])), b)
        return f"Added bullet to {e['id']}: {b['text'][:70]}"
    if kind == "update_bullet":
        _, _, b = _find_bullet(p, op["id"])
        b.update(_check_fields(op["fields"], BULLET_FIELDS, "update_bullet"))
        return f"Updated bullet {b['id']}"
    if kind == "remove_bullet":
        e, i, b = _find_bullet(p, op["id"])
        e["bullets"].pop(i)
        return f"Removed bullet {b['id']}"
    if kind == "move_bullet":
        e, i, b = _find_bullet(p, op["id"])
        e["bullets"].pop(i)
        e["bullets"].insert(int(op["index"]), b)
        return f"Moved bullet {b['id']} to position {op['index']}"
    if kind == "add_entry":
        sec = op["section"]
        if sec not in SECTIONS:
            raise OpError(f"section must be one of {SECTIONS}")
        entry = dict(op["entry"])
        bullets = entry.pop("bullets", [])
        _check_fields(entry, ENTRY_FIELDS[sec] | {"id"}, "add_entry")
        entry["bullets"] = [
            _check_fields(dict(b) if isinstance(b, dict) else {"text": b}, BULLET_FIELDS, "add_entry bullet")
            for b in bullets
        ]
        for b in entry["bullets"]:
            b.setdefault("status", "draft")
        p[sec].insert(op.get("index", len(p[sec])), entry)
        return f"Added {sec} entry"
    if kind == "update_entry":
        sec, _, e = _find_entry(p, op["id"])
        e.update(_check_fields(op["fields"], ENTRY_FIELDS[sec], "update_entry"))
        return f"Updated entry {e['id']}"
    if kind == "remove_entry":
        sec, i, e = _find_entry(p, op["id"])
        p[sec].pop(i)
        return f"Removed {sec} entry {e['id']}"
    if kind == "add_skill":
        s = _check_fields(dict(op["skill"]), SKILL_FIELDS, "add_skill")
        if not s.get("name"):
            raise OpError("skill needs a name")
        if any(x["name"].lower() == s["name"].lower() for x in p["skills"]):
            raise OpError(f"skill '{s['name']}' already exists")
        p["skills"].append(s)
        return f"Added skill {s['name']}"
    if kind == "update_skill":
        _, s = _find_skill(p, op["id"])
        s.update(_check_fields(op["fields"], SKILL_FIELDS, "update_skill"))
        return f"Updated skill {s['id']}"
    if kind == "remove_skill":
        i, s = _find_skill(p, op["id"])
        p["skills"].pop(i)
        return f"Removed skill {s['name']}"
    if kind == "update_basics":
        p["basics"].update(_check_fields(op["fields"], BASICS_FIELDS, "update_basics"))
        return "Updated contact info"
    raise OpError(f"unknown op '{kind}'")


# ---------- honesty check ----------

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _all_text(p: dict) -> str:
    return yaml.safe_dump(p)


def honesty_warnings(before: dict, after: dict, context: str) -> list[str]:
    """Flag numbers in new/changed bullets that appear nowhere in the old profile
    or in what the user said. Numbers are where invented claims usually hide."""
    known = set(_NUM.findall(_all_text(before) + "\n" + context))
    old_text = {}
    for sec in SECTIONS:
        for e in before[sec]:
            for b in e["bullets"]:
                old_text[b["id"]] = b["text"]
    warnings = []
    for sec in SECTIONS:
        for e in after[sec]:
            for b in e["bullets"]:
                if old_text.get(b["id"]) == b["text"]:
                    continue
                new_nums = [n for n in _NUM.findall(b["text"]) if n not in known]
                if new_nums:
                    warnings.append(
                        f"Bullet {b['id']} has numbers you never gave: {', '.join(new_nums)}. Check them."
                    )
    return warnings
