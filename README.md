# Resume Tailor

A local web app for keeping one master list of everything you've done and turning
it into one-page LaTeX resumes tailored to specific job postings.

- **Bank**: experiences, projects, skills and bullets in one YAML file, edited in the UI or by hand.
- **Tailor**: paste a job description and it picks the most relevant bullets and skills,
  then fits them to exactly one page by compiling and measuring the real PDF.
- **Refine**: chat with each resume ("put the backend bullets first"). Rewording can't add
  facts or numbers that aren't in your bank.
- **Checks**: every build is checked for formatting issues (spacing, margins, runt lines,
  uneven skill columns, duplicate bullets).

The AI parts run through the [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI
(`claude -p`) using your own login, so there's no API key to set up.

## Setup

Needs Python 3.10+, the Claude Code CLI (logged in), and [Tectonic](https://tectonic-typesetting.github.io).

```bash
brew install tectonic
./run.sh          # http://localhost:8765
```

On first run, `profile.example.yaml` and `latex/resume_style.example.tex` are copied into
`data/`. Replace them with your own info and format. `data/` is git-ignored, so your
personal info stays local.

To try the UI without the AI: `LLM_PROVIDER=mock ./run.sh`

| env var | default | |
|---|---|---|
| `CLAUDE_MODEL` | `sonnet` | model for `claude -p` |
| `LLM_PROVIDER` | `claude` | `claude` or `mock` |
| `PORT` | `8765` | web port |

## License

MIT
