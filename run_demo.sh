#!/usr/bin/env bash
set -euo pipefail
DEMO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEMO_ROOT"
# Prefer an explicit override, then the user's activated environment.
if [[ -n "${DEMO_PYTHON:-}" ]]; then
  DEMO_PYTHON="$(command -v -- "$DEMO_PYTHON" || true)"
elif [[ -n "${CONDA_PREFIX:-}${VIRTUAL_ENV:-}" ]] && command -v python >/dev/null 2>&1; then
  DEMO_PYTHON="$(command -v python)"
elif [[ -x "$DEMO_ROOT/demo_env/bin/python" ]]; then
  DEMO_PYTHON="$DEMO_ROOT/demo_env/bin/python"
else
  DEMO_PYTHON="$(command -v python || command -v python3 || true)"
fi
if [[ -z "$DEMO_PYTHON" || ! -x "$DEMO_PYTHON" ]]; then
  echo '실행할 Python 환경이 없습니다. conda 환경을 활성화하거나 DEMO_PYTHON=/path/to/python으로 지정하세요.' >&2
  exit 1
fi
exec "$DEMO_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "${DEMO_PORT:-8001}" "$@"
