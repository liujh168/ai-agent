@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo  🤖 AI Agent 学习演示 · 一键启动
echo  =================================

:: ---------- 1. 检查 LM Studio ----------
where curl >nul 2>nul
if errorlevel 1 (
    echo  ⚠️  未找到 curl，跳过 LM Studio 检查
    goto :skip_lm_check
)

for /f "tokens=*" %%i in ('curl -s -o NUL -w "%%{http_code}" http://127.0.0.1:1234/v1/models 2^>nul') do set LM_CODE=%%i
if not "%LM_CODE%"=="200" (
    echo.
    echo  ❌ LM Studio 未启动或端口不对
    echo     请启动 LM Studio，Load 一个模型，然后重试
    echo     下载: https://lmstudio.ai/
    pause
    exit /b 1
)
echo  ✅ LM Studio 在线

:skip_lm_check

:: ---------- 2. 检查 / 创建 venv ----------
if not exist .venv\Scripts\python.exe (
    echo.
    echo  📦 首次运行，创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo  ❌ 创建 venv 失败，请确认已安装 Python 3.10+
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    echo  📥 安装依赖（首次较慢，约 1-3 分钟）...
    python -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo     清华镜像失败，重试官方源...
        python -m pip install -r requirements.txt
    )
) else (
    call .venv\Scripts\activate.bat
)
echo  ✅ 依赖就绪

:: ---------- 3. 启动 ----------
echo.
if "%1"=="--cli" (
    echo  🚀 启动 CLI 交互模式...
    echo  =================================
    python main.py
) else if "%1"=="--eval" (
    echo  🚀 运行评测集...
    echo  =================================
    python -m ai_agent.eval
) else (
    echo  🚀 启动 Web UI: http://127.0.0.1:7860
    echo     Ctrl+C 停止
    echo  =================================
    python -m ai_agent.web_ui
)

pause
