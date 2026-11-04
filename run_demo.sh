#!/usr/bin/env bash
set -euo pipefail
DEMO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEMO_ROOT"
DEMO_PYTHON="${DEMO_PYTHON:-$DEMO_ROOT/demo_env/bin/python}"
if [[ ! -x "$DEMO_PYTHON" ]]; then
  echo "실행할 Python 환경이 없습니다. README대로 환경을 준비하거나 DEMO_PYTHON을 지정하세요." >&2
  exit 1
fi
exec "$DEMO_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "${DEMO_PORT:-8001}" "$@"
