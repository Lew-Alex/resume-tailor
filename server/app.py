"""Local server: owns data/, talks to the AI, compiles LaTeX, pushes live updates.

Run with ./run.sh (or: uvicorn server.app:app --port 8765 from the repo root).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from watchfiles import awatch

from . import checks, llm, profile_ops as po, prompts, resumes as rs, texgen

WEB = po.ROOT / "web"
CHAT_LOG = po.DATA / "chat.json"

_clients: set[asyncio.Queue] = set()
_write_lock = asyncio.Lock()   # profile.yaml writes
_build_lock = asyncio.Lock()   # LaTeX compiles + resume doc writes
_rebuild_all_pending = False


# ---------- live updates (Server-Sent Events) ----------

def broadcast(event: dict) -> None:
    for q in list(_clients):
        q.put_nowait(event)


async def progress(token: str | None, slug: str | None, text: str) -> None:
    broadcast({"type": "progress", "token": token, "slug": slug, "text": text})


async def rebuild_all() -> None:
    """Profile or style changed: regenerate every resume's .tex/.pdf."""
    global _rebuild_all_pending
    if _rebuild_all_pending:
        return
    _rebuild_all_pending = True
    await asyncio.sleep(0.3)
    _rebuild_all_pending = False
    profile = po.load()
    for doc in rs.list_all():
        async with _build_lock:
            try:
                doc = rs.load(doc["slug"])
                doc["build"] = await rs.build(doc, profile)
                doc.pop("build_error", None)
            except (texgen.CompileError, rs.ResumeError) as e:
                doc["build_error"] = str(e)
            rs.save(doc)
        broadcast({"type": "resume", "slug": doc["slug"]})


async def watch_data() -> None:
    """Changes from this app, your editor, or Claude Code in the terminal all
    get pushed to every open browser tab."""
    async for changes in awatch(po.DATA, debounce=150):
        kinds = set()
        for _, path in changes:
            p = Path(path)
            if p.name == "profile.yaml":
                kinds.add("profile")
                po.note_external_change()
            elif p.name == "resume_style.tex" and p.parent == po.DATA:
                kinds.add("style")
            elif p.parent == po.RESUMES and p.suffix == ".json":
                kinds.add("resumes")
        if "profile" in kinds:
            broadcast({"type": "profile"})
        if kinds & {"profile", "style"}:
            asyncio.create_task(rebuild_all())
        if "resumes" in kinds:
            broadcast({"type": "resumes"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    po.remember_current()
    task = asyncio.create_task(watch_data())
    asyncio.create_task(rebuild_all())  # make sure every resume has fresh output
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


class NoCache:
    """Local tool: always serve fresh files after edits/rebuilds. (A plain ASGI
    middleware, so streaming responses like /api/events shut down cleanly.)"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            # Ctrl+C with a browser tab open cancels the live-update stream;
            # that's expected, so don't print a traceback for it.
            if scope.get("path") != "/api/events":
                raise


app.add_middleware(NoCache)


@app.get("/api/events")
async def events():
    q: asyncio.Queue = asyncio.Queue()
    _clients.add(q)

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _clients.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------- status ----------

@app.get("/api/status")
def status():
    provider = os.environ.get("LLM_PROVIDER", "claude")
    ai = provider == "mock" or bool(llm.claude_binary())
    tex = bool(shutil.which(texgen.TECTONIC) or Path(texgen.TECTONIC).exists())
    return {"provider": provider, "ai_available": ai, "latex_available": tex,
            "hint": None if ai else "claude CLI not found. Install Claude Code or set CLAUDE_BIN."}


# ---------- master profile ----------

class YamlBody(BaseModel):
    yaml: str


@app.get("/api/profile")
def get_profile():
    p = po.load()
    return {"profile": p, "yaml": po.dump_yaml(p)}


@app.put("/api/profile")
async def put_profile(body: YamlBody):
    try:
        p = po.parse_yaml_text(body.yaml)
    except po.OpError as e:
        raise HTTPException(400, str(e))
    async with _write_lock:
        po.save(p)
    return {"ok": True}


class OpsBody(BaseModel):
    ops: list[dict]


async def apply_profile_ops(ops: list[dict], context: str = "") -> tuple[list, list, list]:
    if not ops:
        return [], [], []
    async with _write_lock:
        before = po.load()
        after, applied, errors = po.apply_ops(before, ops)
        warnings = po.honesty_warnings(before, after, context) if applied and context else []
        if applied:
            po.save(after)
    return applied, errors, warnings


@app.post("/api/ops")
async def post_ops(body: OpsBody):
    """Direct edits from the UI (approve, delete, edit...). Same validation as AI edits."""
    applied, errors, _ = await apply_profile_ops(body.ops)
    if errors and not applied:
        raise HTTPException(400, "; ".join(errors))
    return {"applied": applied, "errors": errors}


@app.post("/api/undo")
async def undo():
    async with _write_lock:
        ok = po.undo()
    if not ok:
        raise HTTPException(400, "Nothing to undo.")
    return {"ok": True}


# ---------- master-profile chat ----------

class ChatBody(BaseModel):
    message: str


def _load_chat() -> list[dict]:
    try:
        return json.loads(CHAT_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_chat(log: list[dict]) -> None:
    CHAT_LOG.write_text(json.dumps(log[-200:], indent=1))


@app.get("/api/chat")
def chat_history():
    return _load_chat()


@app.delete("/api/chat")
def clear_chat():
    _save_chat([])
    return {"ok": True}


@app.post("/api/chat")
async def chat(body: ChatBody):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Empty message.")
    log = _load_chat()
    try:
        result = await llm.complete_json(prompts.chat_prompt(po.load(), log, message))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    reply = str(result.get("reply", ""))
    context = message + "\n" + "\n".join(m["content"] for m in log if m["role"] == "user")
    applied, errors, warnings = await apply_profile_ops(result.get("ops") or [], context)
    log += [{"role": "user", "content": message, "at": time.time()},
            {"role": "assistant", "content": reply, "applied": applied,
             "errors": errors, "warnings": warnings, "at": time.time()}]
    _save_chat(log)
    return {"reply": reply, "applied": applied, "errors": errors, "warnings": warnings}


# ---------- resumes ----------

def _summary(d: dict) -> dict:
    b = d.get("build") or {}
    return {"slug": d["slug"], "title": d["title"], "company": d.get("company", ""), "kind": d.get("kind"),
            "created": d.get("created"), "updated": d.get("updated"),
            "pages": b.get("pages"), "slack_pt": b.get("slack_pt"), "error": d.get("build_error")}


@app.get("/api/resumes")
def list_resumes():
    return [_summary(d) for d in rs.list_all()]


@app.get("/api/resumes/{slug}")
def get_resume(slug: str):
    try:
        return rs.load(slug)
    except rs.ResumeError as e:
        raise HTTPException(404, str(e))


def _push_history(doc: dict) -> None:
    snap = {"layout": copy.deepcopy(doc["layout"]), "rewrites": copy.deepcopy(doc.get("rewrites", {}))}
    doc.setdefault("history", []).append(snap)
    doc["history"] = doc["history"][-25:]


async def _rebuild(doc: dict, fit: str | None = None, token: str | None = None) -> dict:
    """fit: None = just build; "cut" = cut to one page if over; "full" = cut + fill."""
    profile = po.load()

    async def prog(text):
        await progress(token, doc["slug"], text)

    try:
        if fit and doc.get("scores"):
            doc["build"] = await rs.fit(doc, profile, progress=prog, fill=(fit == "full"))
        else:
            doc["build"] = await rs.build(doc, profile)
        doc.pop("build_error", None)
    except texgen.CompileError as e:
        doc["build_error"] = str(e)
    rs.save(doc)
    broadcast({"type": "resume", "slug": doc["slug"]})
    return doc


class TailorBody(BaseModel):
    job: str
    token: str | None = None


@app.post("/api/resumes")
async def create_resume(body: TailorBody):
    job = body.job.strip()
    if len(job) < 40:
        raise HTTPException(400, "Paste the full job description.")
    tok = body.token
    profile = po.load()
    await progress(tok, None, "Reading the job and scoring your bank…")
    try:
        result = await llm.complete_json(prompts.tailor_prompt(profile, job))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))

    known = po.all_ids(profile)
    scores = {}
    for k, v in (result.get("scores") or {}).items():
        if k not in known:
            continue  # drop hallucinated ids
        s = v.get("s", 0) if isinstance(v, dict) else v
        try:
            s = max(0.0, min(10.0, float(s)))
        except (TypeError, ValueError):
            s = 0.0
        scores[k] = {"s": s, "why": v.get("why", "") if isinstance(v, dict) else ""}

    title = str(result.get("title") or "Job").strip()
    company = str(result.get("company") or "").strip()
    doc = rs.new_doc(rs.unique_slug(f"{company} {title}" if company else title), title, company)
    doc.update({"job": job, "scores": scores, "summary": str(result.get("summary") or ""),
                "keywords": [str(k) for k in result.get("keywords") or []]})

    await progress(tok, doc["slug"], "Picking bullets and fitting one page…")
    async with _build_lock:
        rs.auto_layout(doc, profile)
        await _rebuild(doc, fit="full", token=tok)

    await progress(tok, doc["slug"], "Claude is refining wording and choices…")
    try:
        await _refine(doc["slug"], message=None, token=tok)
        # Then one pass over anything the PDF checks still flag (runt lines, etc.)
        if _fix_message(rs.load(doc["slug"])):
            await progress(tok, doc["slug"], "Fixing formatting issues found in the PDF…")
            await _fix_format(doc["slug"], tok)
    except HTTPException:
        pass  # the fitted version is still good
    await progress(tok, doc["slug"], "Done")
    return rs.load(doc["slug"])


def _fix_message(doc: dict) -> str:
    return checks.fix_instructions((doc.get("build") or {}).get("checks") or [])


async def _fix_format(slug: str, token: str | None = None) -> dict:
    msg = _fix_message(rs.load(slug))
    if not msg:
        raise HTTPException(400, "No fixable formatting issues on this resume.")
    msg += ("\nOnly make changes needed for these problems (trim wording via rewrite ops, "
            "swap/remove a bullet only if it's a duplicate or the page overflows).")
    msg += "\nDo NOT use profile_ops here: formatting fixes change this resume only, never the bank."
    doc = await _refine(slug, msg, token, shown="Fix the formatting issues found in the PDF.", allow_profile_ops=False)
    # Line breaks are hard to predict from text alone, so take one more pass
    # with the freshly measured PDF if anything fixable is left.
    again = _fix_message(doc)
    if again:
        await progress(token, slug, "Second pass on what's left…")
        again += "\nDo NOT use profile_ops here: formatting fixes change this resume only, never the bank."
        doc = await _refine(slug, again, token, shown="(second pass) Fix the remaining formatting issues.",
                            allow_profile_ops=False)
    return doc


@app.post("/api/resumes/{slug}/fix-format")
async def fix_format(slug: str, body: dict | None = None):
    return await _fix_format(slug, (body or {}).get("token"))


async def _refine(slug: str, message: str | None, token: str | None = None, shown: str | None = None,
                  allow_profile_ops: bool = True) -> dict:
    """message=None -> automatic refinement pass. `shown` is what the chat log
    displays for the user's side (defaults to the message itself)."""
    doc = rs.load(slug)
    profile = po.load()
    prompt = prompts.refine_prompt(doc, profile, message or "", rs.resolve, rs.index, auto=message is None)
    try:
        result = await llm.complete_json(prompt)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))

    p_applied, p_errors, warnings = [], [], []
    if message and allow_profile_ops and result.get("profile_ops"):
        p_applied, p_errors, warnings = await apply_profile_ops(result["profile_ops"], message)

    async with _build_lock:
        doc = rs.load(slug)  # fresh copy
        _push_history(doc)
        applied, errors = rs.apply_ops(doc, po.load(), result.get("resume_ops") or [])
        await progress(token, slug, "Rebuilding the PDF…")
        # Automatic passes may also top up empty space; a chat request only
        # gets cut back if it overflowed, so what you asked to remove stays removed.
        await _rebuild(doc, fit="full" if message is None else "cut", token=token)
        doc = rs.load(slug)
        entry = {"role": "assistant", "content": str(result.get("reply", "")),
                 "applied": p_applied + applied, "errors": p_errors + errors, "warnings": warnings,
                 "at": time.time()}
        if message:
            doc["chat"].append({"role": "user", "content": shown or message, "at": time.time()})
        else:
            entry["content"] = "Automatic refinement: " + entry["content"]
        doc["chat"].append(entry)
        rs.save(doc)
    broadcast({"type": "resume", "slug": slug})
    return doc


class RefineBody(BaseModel):
    message: str | None = None
    token: str | None = None


@app.post("/api/resumes/{slug}/chat")
async def refine(slug: str, body: RefineBody):
    try:
        rs.load(slug)
    except rs.ResumeError as e:
        raise HTTPException(404, str(e))
    msg = (body.message or "").strip() or None
    return await _refine(slug, msg, body.token)


@app.post("/api/resumes/{slug}/ops")
async def resume_ops(slug: str, body: OpsBody):
    async with _build_lock:
        doc = rs.load(slug)
        _push_history(doc)
        applied, errors = rs.apply_ops(doc, po.load(), body.ops)
        if errors and not applied:
            raise HTTPException(400, "; ".join(errors))
        await _rebuild(doc)
    return {"applied": applied, "errors": errors}


class SettingsBody(BaseModel):
    settings: dict | None = None
    title: str | None = None
    company: str | None = None


@app.patch("/api/resumes/{slug}")
async def patch_resume(slug: str, body: SettingsBody):
    async with _build_lock:
        doc = rs.load(slug)
        if body.title is not None:
            doc["title"] = body.title.strip() or doc["title"]
        if body.company is not None:
            doc["company"] = body.company.strip()
        if body.settings:
            doc["settings"] = {**rs.DEFAULT_SETTINGS, **doc.get("settings", {}),
                               **{k: int(v) for k, v in body.settings.items() if k in rs.DEFAULT_SETTINGS}}
        rs.save(doc)
    return {"ok": True}


@app.post("/api/resumes/{slug}/autofit")
async def autofit(slug: str):
    """Re-pick everything from the AI's scores with the current settings."""
    async with _build_lock:
        doc = rs.load(slug)
        if not doc.get("scores"):
            raise HTTPException(400, "Auto-fit needs a job description (this is a base resume).")
        _push_history(doc)
        rs.auto_layout(doc, po.load())
        await _rebuild(doc, fit="full")
    return {"ok": True}


@app.post("/api/resumes/{slug}/undo")
async def resume_undo(slug: str):
    async with _build_lock:
        doc = rs.load(slug)
        if not doc.get("history"):
            raise HTTPException(400, "Nothing to undo for this resume.")
        snap = doc["history"].pop()
        doc["layout"], doc["rewrites"] = snap["layout"], snap["rewrites"]
        await _rebuild(doc)
    return {"ok": True}


@app.post("/api/resumes/{slug}/reveal")
async def reveal(slug: str):
    doc = rs.load(slug)
    stem = (doc.get("build") or {}).get("stem")
    target = po.BUILD / doc["slug"] / f"{stem}.tex" if stem else po.BUILD / doc["slug"]
    proc = await asyncio.create_subprocess_exec("open", "-R", str(target))
    await proc.wait()
    return {"ok": True}


@app.delete("/api/resumes/{slug}")
async def delete_resume(slug: str):
    async with _build_lock:
        rs.path(slug).unlink(missing_ok=True)
        shutil.rmtree(po.BUILD / rs.slugify(slug), ignore_errors=True)
    broadcast({"type": "resumes"})
    return {"ok": True}


# Generated .tex / .pdf / page previews
po.ensure_data()
app.mount("/build", StaticFiles(directory=po.BUILD), name="build")
# Frontend last so /api/* wins.
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
