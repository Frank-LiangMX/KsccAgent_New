@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto no_python

echo.
echo [1/4] Checking PyInstaller...
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo PyInstaller is not installed. Installing...
  python -m pip install pyinstaller
  if errorlevel 1 goto install_failed
)

echo.
echo [2/4] Cleaning old build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/4] Building EXE...
python -m PyInstaller --noconfirm --clean KsccUI.spec
if errorlevel 1 goto build_failed

echo.
echo [4/4] Preparing runtime folders...
if not exist "dist\KsccUI\sessions" mkdir "dist\KsccUI\sessions"
if not exist "dist\KsccUI\memory" mkdir "dist\KsccUI\memory"
if not exist "dist\KsccUI\skills" mkdir "dist\KsccUI\skills"
if not exist "dist\KsccUI\skills\items" mkdir "dist\KsccUI\skills\items"
if not exist "dist\KsccUI\logs" mkdir "dist\KsccUI\logs"

echo.
echo Build complete:
echo %cd%\dist\KsccUI\KsccUI.exe
pause
exit /b 0

:no_python
echo Python was not found in PATH.
pause
exit /b 1

:install_failed
echo Failed to install PyInstaller.
pause
exit /b 1

:build_failed
echo EXE build failed.
pause
exit /b 1
