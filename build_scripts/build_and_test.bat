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

for /f "usebackq delims=" %%C in (`git rev-parse HEAD`) do set "RELEASE_COMMIT=%%C"
if not defined RELEASE_COMMIT (
    echo [ERROR] Unable to resolve the clean FINAL release commit.
    exit /b 1
)

python scripts\release_utils.py validate-project-version --version "%VERSION%" --metadata src\hunter_metadata.py
if errorlevel 1 exit /b 1

echo [1/9] Installing bounded project dependencies...
python -m pip install --require-hashes -r requirements-lock-windows-py311.txt
if errorlevel 1 exit /b 1
python -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo [2/9] Compiling source...
python -m compileall -q src scripts
if errorlevel 1 exit /b 1

echo [3/9] Running Ruff...
python -m ruff check src tests scripts
if errorlevel 1 exit /b 1

echo [4/9] Running mypy...
python -m mypy
if errorlevel 1 exit /b 1

echo [5/9] Running pytest with the release coverage threshold...
python -m pytest --cov-fail-under=30
if errorlevel 1 exit /b 1

echo [6/9] Auditing dependencies and high-severity findings...
python -m pip_audit -r requirement.txt
if errorlevel 1 exit /b 1
python -m bandit -r src scripts -lll -c pyproject.toml
if errorlevel 1 exit /b 1

echo [7/9] Building directly from the exact committed source...
set "POWERSHELL_EXE="
where pwsh.exe >nul 2>&1
if not errorlevel 1 set "POWERSHELL_EXE=pwsh.exe"
if not defined POWERSHELL_EXE set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" if /i not "%POWERSHELL_EXE%"=="pwsh.exe" (
    echo [ERROR] No supported PowerShell executable was found.
    exit /b 1
)
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows_final.ps1" -Version "%VERSION%" -Commit "%RELEASE_COMMIT%"
if errorlevel 1 exit /b 1

set "ZIP_NAME=hunterX_windows_%VERSION%_final.zip"
set "SOURCE_ZIP_NAME=hunterX_source_%VERSION%_final.zip"
set "CHECKSUM_NAME=SHA256SUMS_v%VERSION%_FINAL.txt"
if not exist "dist\release\%ZIP_NAME%" (
    echo [ERROR] Expected artifact dist\release\%ZIP_NAME% was not created.
    exit /b 1
)

echo [8/9] Building the matching source archive and checksum manifest...
python scripts\build_source_archive.py --version "%VERSION%" --output "dist\release\%SOURCE_ZIP_NAME%" --repo-root . --commit "%RELEASE_COMMIT%" --qualifier final
if errorlevel 1 exit /b 1
python scripts\write_release_checksums.py --version "%VERSION%" --output "dist\release\%CHECKSUM_NAME%" --qualifier final "dist\release\%ZIP_NAME%" "dist\release\%SOURCE_ZIP_NAME%"
if errorlevel 1 exit /b 1

echo [9/9] Re-verifying both archives, checksums, and exact source parity...
python scripts\verify_release_archive.py windows --archive "dist\release\%ZIP_NAME%" --version "%VERSION%" --qualifier final
if errorlevel 1 exit /b 1
python scripts\verify_release_archive.py source --archive "dist\release\%SOURCE_ZIP_NAME%" --version "%VERSION%" --repo-root . --commit "%RELEASE_COMMIT%" --qualifier final
if errorlevel 1 exit /b 1
python scripts\verify_release_archive.py pair --windows-archive "dist\release\%ZIP_NAME%" --source-archive "dist\release\%SOURCE_ZIP_NAME%" --version "%VERSION%" --repo-root . --commit "%RELEASE_COMMIT%" --qualifier final
if errorlevel 1 exit /b 1
python scripts\write_release_checksums.py --version "%VERSION%" --verify-manifest "dist\release\%CHECKSUM_NAME%" --asset-dir dist\release --qualifier final
if errorlevel 1 exit /b 1

set "REPORT_FILE=dist\release\test_report_%VERSION%.txt"
(
    echo HunterX build and test report
    echo Version: %VERSION%
    echo Python: %PYTHON_VERSION%
    echo Artifact: %ZIP_NAME%
    echo Source: %SOURCE_ZIP_NAME%
    echo Checksums: %CHECKSUM_NAME%
    echo Build mode: source_native
    echo Commit: %RELEASE_COMMIT%
    echo Status: PASS
) > "%REPORT_FILE%"

echo [SUCCESS] %ZIP_NAME%
echo [SUCCESS] %SOURCE_ZIP_NAME%
echo [SUCCESS] %CHECKSUM_NAME%
echo [SUCCESS] %REPORT_FILE%
exit /b 0
