#!/usr/bin/env bash
# 构建课件中心并同步到 ai_agent/static/courses/
set -euo pipefail
cd "$(dirname "$0")"

npm run build
rm -rf ../ai_agent/static/courses
cp -r dist ../ai_agent/static/courses

echo "✅ 已同步到 ai_agent/static/courses，重启 Web UI 后生效"
