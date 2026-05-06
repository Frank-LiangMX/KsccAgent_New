@echo off
chcp 65001 >nul
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 goto no_git

set "MSG=%~1"
if not "%~2"=="" set "MSG=%~1 %~2"
if not "%~3"=="" set "MSG=%MSG% %~3"
if not "%~4"=="" set "MSG=%MSG% %~4"
if not "%~5"=="" set "MSG=%MSG% %~5"
if not "%~6"=="" set "MSG=%MSG% %~6"
if not "%~7"=="" set "MSG=%MSG% %~7"
if not "%~8"=="" set "MSG=%MSG% %~8"
if not "%~9"=="" set "MSG=%MSG% %~9"

if not defined MSG goto ask_msg
goto do_commit

:ask_msg
set /p MSG=Enter commit message: 
if not defined MSG goto empty_msg

:do_commit
echo.
echo Current changes:
git status --short
echo.
echo Staging all changes...
git add -A
if errorlevel 1 goto add_failed

echo Creating commit...
git commit -m "%MSG%"
if errorlevel 1 goto commit_failed

echo.
echo Commit complete.
git log -1 --oneline
pause
exit /b 0

:no_git
echo Git was not found in PATH.
pause
exit /b 1

:empty_msg
echo Commit cancelled: empty message.
pause
exit /b 1

:add_failed
echo git add failed.
pause
exit /b 1

:commit_failed
echo git commit failed. There may be nothing to commit.
pause
exit /b 1
