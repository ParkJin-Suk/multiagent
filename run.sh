#!/usr/bin/env bash
# 백엔드 + 프론트를 한 번에. Ctrl+C 로 둘 다 종료.
set -e
cd "$(dirname "$0")"

[ -f .env ] || { echo "⚠️  .env 가 없습니다. cp .env.example .env 후 LLM 키를 채워주세요."; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "⚠️  ffmpeg 가 필요합니다."; exit 1; }

trap 'kill 0' EXIT
( cd backend && uvicorn app.main:app --reload --port 8000 ) &

if [ -d frontend/node_modules ]; then
  ( cd frontend && npm run dev ) &
  echo ""
  echo "  ▶ 웹 GUI : http://localhost:5173"
  echo "  ▶ API    : http://localhost:8000/docs"
  echo ""
else
  echo "frontend/node_modules 가 없습니다. cd frontend && npm install 을 먼저 실행하세요."
fi
wait
