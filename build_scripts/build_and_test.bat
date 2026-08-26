@echo off
setlocal EnableExtensions

cd /d "%~dp0.."
if errorlevel 1 (
    echo [ERROR] Unable to enter the HunterX project root.
    exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11.9 is required and must be available as python.
    exit /b 1
)

for /f "usebackq delims=" %%V in (`python -c "import platform; print(platform.python_version())"`) do set "PYTHON_VERSION=%%V"
if not "%PYTHON_VERSION%"=="3.11.9" (
    echo [ERROR] Python 3.11.9 is required; found %PYTHON_VERSION%.
    exit /b 1
)

if not defined VERSION (
    for /f "usebackq delims=" %%V in (`python scripts\release_utils.py project-version --metadata src\hunter_metadata.py`) do set "VERSION=%%V"
)
if not defined VERSION (
    echo [ERROR] Unable to resolve APP_VERSION.
    exit /b 1
)

if not defined BASE_ARCHIVE set "BASE_ARCHIVE=dist\base\hunterX_windows_0.5.2_rc3.zip"
if not exist "%BASE_ARCHIVE%" (
    echo [ERROR] Verified v0.5.2 RC3 Windows base not found: %BASE_ARCHIVE%
    echo [ERROR] Supply hunterX_windows_0.5.2_rc3.zip with the approved SHA-256.
    exit /b 1
)

for /f "usebackq delims=" %%C in (`git rev-parse HEAD`) do set "RELEASE_COMMIT=%%C"
if not defined RELEASE_COMMIT (
    echo [ERROR] Unable to resolve the clean FINAL release commit.
    exit /b 1
)

python scripts\release_utils.py validate-project-version --version "%VERSION%" --metadata src\hunter_metadata.py
if errorlevel 1 exit /b 1

echo [1/8] Installing bounded project dependencies...
python -m pip install --require-hashes -r requirements-lock-windows-py311.txt
if errorlevel 1 exit /b 1
python -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo [2/8] Compiling source...
python -m compileall -q src scripts
if errorlevel 1 exit /b 1

echo [3/8] Running Ruff...
python -m ruff check src tests scripts
if errorlevel 1 exit /b 1

echo [4/8] Running mypy...
python -m mypy
if errorlevel 1 exit /b 1

echo [5/8] Running pytest...
python -m pytest
if errorlevel 1 exit /b 1

echo [6/8] Auditing dependencies and high-severity findings...
python -m pip_audit -r requirement.txt
if errorlevel 1 exit /b 1
python -m bandit -r src scripts -lll -c pyproject.toml
if errorlevel 1 exit /b 1

echo [7/8] Building through the canonical PowerShell workflow...
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1" -Version "%VERSION%" -BaseArchive "%BASE_ARCHIVE%" -Commit "%RELEASE_COMMIT%" -Qualifier final
if errorlevel 1 exit /b 1

set "ZIP_NAME=hunterX_windows_%VERSION%_final.zip"
if not exist "dist\release\%ZIP_NAME%" (
    echo [ERROR] Expected artifact dist\release\%ZIP_NAME% was not created.
    exit /b 1
)

echo [8/8] Re-verifying the release ZIP...
python scripts\verify_release_archive.py windows --archive "dist\release\%ZIP_NAME%" --version "%VERSION%" --qualifier final
if errorlevel 1 exit /b 1

set "REPORT_FILE=dist\release\test_report_%VERSION%.txt"
(
    echo HunterX build and test report
    echo Version: %VERSION%
    echo Python: %PYTHON_VERSION%
    echo Artifact: %ZIP_NAME%
    echo Status: PASS
) > "%REPORT_FILE%"

echo [SUCCESS] %ZIP_NAME%
echo [SUCCESS] %REPORT_FILE%
exit /b 0
