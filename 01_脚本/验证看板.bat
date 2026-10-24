@echo off
chcp 65001 >nul 2>nul
setlocal
title Haidou dashboard verify
cd /d "%~dp0"

rem ============================================================
rem  Haidou MMR dashboard assertion runner (offline, no game needed)
rem  Runs _verify_dash.js + _verify_toolbar.js + _verify_player.js with jsdom,
rem  then checks theme.css <-> board style drift (apply_theme.py --check) and data-owner
rem  consistency (test_owner_consistency.py, step 5/5).
rem  2026-10-03: _verify_dash_iso.js 已并入 _verify_dash.js 的 ⑥/⑦ 段（隔离性），
rem  原第 2 步随之取消，步数 6 -> 5。
rem  Edit NODE_EXE / NODE_PATH / PY_EXE if you move to another machine.
rem  Set LOL_NOPAUSE=1 to skip the pause (for automation).
rem ============================================================

rem 留空 = 自动从 PATH 找 node / python（见下面的 where 兜底）
set "NODE_EXE="
rem jsdom 装在仓库根或本目录的 node_modules 都行；装了全局 jsdom 就留空
set "NODE_PATH=%~dp0..\node_modules"
set "PY_EXE="
if not exist "%NODE_EXE%" (
    for /f "delims=" %%i in ('where node 2^>nul') do set "NODE_EXE=%%i"
)
if not exist "%PY_EXE%" (
    for /f "delims=" %%i in ('where python 2^>nul') do set "PY_EXE=%%i"
)
set "THEME_PY=..\06_桌面版\apply_theme.py"

if not exist "%NODE_EXE%" (
    echo [X] node.exe not found: %NODE_EXE%  -- also tried PATH
    echo     Edit NODE_EXE in this .bat and retry.
    call :pause_it
    exit /b 1
)
if not exist "%NODE_PATH%\jsdom" (
    echo [X] jsdom not found under: %NODE_PATH%
    echo     Edit NODE_PATH in this .bat and retry.
    call :pause_it
    exit /b 1
)

set FAILED=0
set SKIPPED=0

echo === [1/5] _verify_dash.js ===
"%NODE_EXE%" _verify_dash.js
if errorlevel 1 set FAILED=1

echo.
echo === [2/5] _verify_toolbar.js ===
"%NODE_EXE%" _verify_toolbar.js
if errorlevel 1 set FAILED=1

echo.
echo === [3/5] _verify_player.js ===
"%NODE_EXE%" _verify_player.js
if errorlevel 1 set FAILED=1

echo.
echo === [4/5] theme.css drift check (apply_theme.py --check) ===
if not exist "%PY_EXE%" (
    rem 审查 H1：跳过 != 通过。防线没跑完就报绿，等于把回归放行。
    echo [X] python.exe not found: %PY_EXE%  -- also tried PATH
    echo     SKIPPED - but SKIPPED COUNTS AS FAILURE, verification incomplete.
    set /a SKIPPED+=1
) else (
    "%PY_EXE%" "%THEME_PY%" --check
    if errorlevel 1 (
        echo [X] theme.css is BEHIND the board style block - see listed lines above.
        set FAILED=1
    )
)

echo.
echo === [5/5] test_owner_consistency.py  (崩溃回归：my_game_ids 须每局含数据主人) ===
if not exist "%PY_EXE%" (
    echo [X] python.exe not found: %PY_EXE%  -- also tried PATH
    echo     SKIPPED - but SKIPPED COUNTS AS FAILURE, verification incomplete.
    set /a SKIPPED+=1
) else (
    "%PY_EXE%" "..\07_测试\test_owner_consistency.py"
    if errorlevel 1 (
        echo [X] data-owner consistency FAILED - my_game_ids 可能混入了别的号的对局（看板重建会 StopIteration 崩）。
        set FAILED=1
    )
)

echo.
if not "%SKIPPED%"=="0" set FAILED=1
if "%FAILED%"=="1" (
    echo [X] NOT ALL CHECKS PASSED - see output above: assertion failures and/or skipped steps.
) else (
    echo [OK] ALL 5/5 CHECKS RAN AND PASSED - jsdom assertions + theme drift check + data-owner consistency.
)

call :pause_it
exit /b %FAILED%

:pause_it
if not defined LOL_NOPAUSE pause
exit /b
