#!/usr/bin/env bash
# Start the app at http://localhost:8765
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if ! command -v tectonic >/dev/null && [ ! -x /opt/homebrew/bin/tectonic ]; then
  echo "warning: tectonic not found, so resumes can't compile. Install it with: brew install tectonic"
fi

PORT="${PORT:-8765}"
echo "Resume Tailor → http://localhost:$PORT  (AI provider: ${LLM_PROVIDER:-claude})"
# Short graceful timeout: open browser tabs hold a live-update connection that
# would otherwise make Ctrl+C hang.
exec .venv/bin/uvicorn server.app:app --host 127.0.0.1 --port "$PORT" --timeout-graceful-shutdown 1 "$@"
