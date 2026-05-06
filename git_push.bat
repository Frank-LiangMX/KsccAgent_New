@echo off
chcp 65001 >nul
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo Git was not found in PATH.
  pause
  exit /b 1
)

echo.
echo Current branch status:
git status -sb
echo.
echo Pushing to remote...
git push
if errorlevel 1 (
  echo git push failed. Check network, remote, or SSH / HTTPS auth.
  pause
  exit /b 1
)

echo.
echo Push complete.
git log -1 --oneline
pause
