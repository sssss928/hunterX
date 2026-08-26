param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string] $Version,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $Commit
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PythonVersion = (& python -c "import platform; print(platform.python_version())" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $PythonVersion -cne "3.11.9") {
    throw "Source-native Windows build requires Python 3.11.9; found '$PythonVersion'."
}

$PyInstallerVersion = (& python -c "import importlib.metadata; print(importlib.metadata.version('pyinstaller'))" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $PyInstallerVersion -cne "6.21.0") {
    throw "Source-native Windows build requires PyInstaller 6.21.0; found '$PyInstallerVersion'."
}

$DeclaredVersion = (& python scripts/release_utils.py validate-project-version `
    --version $Version `
    --metadata src/hunter_metadata.py | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $DeclaredVersion -cne $Version) {
    throw "Release version validation failed for '$Version'."
}

$ResolvedCommit = (& git rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $ResolvedCommit -cne $Commit) {
    throw "Release commit '$Commit' is not the checked-out HEAD '$ResolvedCommit'."
}

$ArtifactName = (& python scripts/release_utils.py artifact-name `
    --version $Version `
    --qualifier final | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ArtifactName)) {
    throw "Unable to resolve the FINAL Windows artifact name."
}

$PackageDir = Join-Path $ProjectRoot "dist\hunterX"
$ReleaseDir = Join-Path $ProjectRoot "dist\release"
$ArtifactPath = Join-Path $ReleaseDir $ArtifactName

Write-Host "Building HunterX $Version FINAL directly from clean commit $Commit"
Write-Host "Artifact: $ArtifactPath"

& python scripts/build_windows_final.py `
    --version $Version `
    --project-root $ProjectRoot `
    --package-dir $PackageDir `
    --commit $Commit `
    --output $ArtifactPath
if ($LASTEXITCODE -ne 0) {
    throw "Source-native Windows FINAL build failed (python exit code $LASTEXITCODE)."
}

& cscript.exe //nologo scripts/verify_windows_shell_zip.js $ArtifactPath
if ($LASTEXITCODE -ne 0) {
    throw "Windows Shell ZIP verification failed (cscript exit code $LASTEXITCODE)."
}

& python scripts/verify_release_archive.py windows `
    --archive $ArtifactPath `
    --version $Version `
    --qualifier final
if ($LASTEXITCODE -ne 0) {
    throw "FINAL archive verification failed (python exit code $LASTEXITCODE)."
}

$Artifact = Get-Item -LiteralPath $ArtifactPath
$Sha256 = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Verified: $($Artifact.FullName)"
Write-Host "Size: $($Artifact.Length) bytes"
Write-Host "SHA-256: $Sha256"
