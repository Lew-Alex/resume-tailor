# Resume Tailor: notes for Claude

This repo is a local resume builder. The user's data lives in `data/profile.yaml`
(git-ignored). A local web app at http://localhost:8765 watches that file and
re-renders live, so **editing `data/profile.yaml` directly is a supported way to
change the user's resume**.

## When the user asks you to edit their profile from the terminal
- Edit `data/profile.yaml` only. Keep existing `id`s unchanged; omit `id` on new
  items (the app assigns them).
- Never invent facts, numbers, tools or results. If a number would help, write the
  bullet without it, set `metrics_needed: true`, and ask.
- New bullets you write: `status: draft`. Only the user approves.
- `evidence` must be honest: `implemented` | `experiment` | `planned`.
- Schema: see the header comment in `profile.example.yaml`.

- Use `**bold**` for emphasis (1-3 terms per bullet). Bullets that say the
  same thing share a `groups` value.
- Resumes in `data/resumes/*.json` only reference bank ids. Edit facts in the
  bank, never in a resume. Every resume rebuilds automatically when the bank changes.

## Code layout
- `server/app.py`: FastAPI. REST + Server-Sent Events (`/api/events`) + file watcher + build orchestration.
- `server/resumes.py`: resume layouts, auto-selection from scores, resume ops, compile-driven one-page fit.
- `server/checks.py`: formatting checks on the compiled PDF (pdftotext word boxes + LaTeX log).
- `server/texgen.py`: layout → LaTeX using the macros in `data/resume_style.tex`; Tectonic compile; `RTFIT` page-fill probe.
- `server/llm.py`: shells out to `claude -p --model sonnet --output-format json`; `LLM_PROVIDER=mock` for offline testing.
- `server/prompts.py`: bank chat (`{reply, ops}`), tailor scoring, per-resume refine (`{reply, resume_ops, profile_ops}`).
- `server/profile_ops.py`: ID-based bank ops, validation, honesty check, undo history.
- `web/app.js`: UI.
