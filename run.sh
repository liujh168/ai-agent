#!/usr/bin/env bash
# AI Agent 学习演示 · 一键启动（macOS / Linux）
set -euo pipefail

echo ""
echo " 🤖 AI Agent 学习演示 · 一键启动"
echo " ================================="

# ---------- 1. 检查 LM Studio ----------
if command -v curl &>/dev/null; then
    LM_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:1234/v1/models 2>/dev/null || echo "000")
    if [ "$LM_CODE" != "200" ]; then
        echo ""
        echo " ❌ LM Studio 未启动或端口不对"
        echo "    请启动 LM Studio，Load 一个模型，然后重试"
        echo "    下载: https://lmstudio.ai/"
        exit 1
    fi
    echo " ✅ LM Studio 在线"
fi

# ---------- 2. 检查 / 创建 venv ----------
if [ ! -f ".venv/bin/python" ]; then
    echo ""
    echo " 📦 首次运行，创建虚拟环境..."
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    echo " 📥 安装依赖（首次较慢）..."
    python -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        || python -m pip install -r requirements.txt
else
    source .venv/bin/activate
fi
echo " ✅ 依赖就绪"

# ---------- 3. 启动 ----------
echo ""
case "${1:-web}" in
    --cli)
        echo " 🚀 启动 CLI 交互模式..."
        echo " ================================="
        python main.py
        ;;
    --eval)
        echo " 🚀 运行评测集..."
        echo " ================================="
        python -m ai_agent.eval
        ;;
    *)
        echo " 🚀 启动 Web UI: http://127.0.0.1:7860"
        echo "    Ctrl+C 停止"
        echo " ================================="
        python -m ai_agent.web_ui
        ;;
esac
