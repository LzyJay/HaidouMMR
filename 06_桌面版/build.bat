@echo off
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Haidou MMR - one-click build wrapper
rem
rem  This file ONLY locates a Python interpreter and hands off
rem  to build.py. All real work lives there, because bat code
rem  page / delayed-expansion issues are painful to debug.
rem
rem  If this file ever misbehaves, you can skip it entirely and
rem  run:   python build.py
rem
rem  Set HAIDOU_NOPAUSE=1 to skip the pause (for automation).
rem
rem  Pure ASCII on purpose: identical bytes under code page 936
rem  and 65001, so it never garbles regardless of console setup.
rem ============================================================

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo [X] Python not found on PATH.
    echo     Install Python 3.13+ from python.org, then reopen this window.
    set "RC=1"
    goto :done
)

echo [*] Interpreter: %PY%
echo.
%PY% "%~dp0build.py" %*
set "RC=%ERRORLEVEL%"

:done
if not defined RC set "RC=0"
if not "%RC%"=="0" (
    echo.
    echo [X] Build failed with exit code %RC%.
)
if not defined HAIDOU_NOPAUSE pause
exit /b %RC%
