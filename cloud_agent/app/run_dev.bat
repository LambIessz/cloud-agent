@echo off
REM ============================================================
REM cloud_agent FastAPI 后端本地开发启动脚本 (Windows)
REM
REM 固化以下环境约束（详见 后续改进计划.md 第 4.3 节）：
REM   PYTHONIOENCODING / PYTHONUTF8 - Windows GBK 控制台 emoji 兼容
REM   HF_ENDPOINT                   - HuggingFace 国内镜像
REM   HF_HUB_DISABLE_SYMLINKS_WARNING- 关闭 Windows 不支持 symlink 的提示
REM   CLOUD_AGENT_LLM_PRICING_CONFIG- LLM 估价配置 (相对仓库根)
REM ============================================================

setlocal

REM Python >=3.10 version check
python -c "import sys; v=sys.version_info; exit(0 if v.major>3 or (v.major==3 and v.minor>=10) else 1)" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [run_dev.bat] ERROR: Python ^>=3.10 required for langchain^>=1.2.0. 1>&2
    echo [run_dev.bat]        Install python3.10+ and retry. 1>&2
    python --version 2>&1
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"

REM 解析仓库根（脚本位于 cloud_agent/app/ 下，向上两级即仓库根）
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\.."

REM LLM 价格配置；可通过外部环境变量覆盖
if "%CLOUD_AGENT_LLM_PRICING_CONFIG%"=="" (
    set "CLOUD_AGENT_LLM_PRICING_CONFIG=%REPO_ROOT%\ops\prometheus\llm_pricing.example.yml"
)

echo [run_dev.bat] PYTHONUTF8=%PYTHONUTF8%
echo [run_dev.bat] HF_ENDPOINT=%HF_ENDPOINT%
echo [run_dev.bat] CLOUD_AGENT_LLM_PRICING_CONFIG=%CLOUD_AGENT_LLM_PRICING_CONFIG%
echo [run_dev.bat] Starting FastAPI on :5000 ...

cd /d "%SCRIPT_DIR%"
python -X utf8 app_main.py %*

endlocal
