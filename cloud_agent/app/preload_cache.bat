@echo off
REM ============================================================
REM 预热 L1 语义缓存（Windows）
REM
REM 注意：milvus-lite 是 SQLite 文件锁独占，运行此脚本前必须先停掉
REM FastAPI 后端，否则 preload 会报 DataDirLockedError。
REM
REM 标准流程（按顺序）：
REM   1. 停后端
REM   2. 运行 preload_cache.bat
REM   3. 重新启动后端 (run_dev.bat)
REM ============================================================

setlocal

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"

set "SCRIPT_DIR=%~dp0"

echo [preload_cache.bat] WARNING: backend must be stopped first (milvus-lite file lock).
echo [preload_cache.bat] HF_ENDPOINT=%HF_ENDPOINT%
echo [preload_cache.bat] Running preload_cache.py ...

cd /d "%SCRIPT_DIR%"
python -X utf8 preload_cache.py

endlocal
