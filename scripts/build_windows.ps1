param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string] $Version
)

$ErrorActionPreference = "Stop"

function Compress-WithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourcePath,
        [Parameter(Mandatory = $true)]
        [string] $DestinationPath,
        [int] $Attempts = 5
    )

    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        try {
            Compress-Archive -Path $SourcePath -DestinationPath $DestinationPath -Force
            return
        } catch {
            if ($Attempt -ge $Attempts) {
                throw
            }
            Write-Warning "Compress-Archive failed on attempt $Attempt/${Attempts}: $($_.Exception.Message)"
            Start-Sleep -Seconds ([Math]::Min(10, $Attempt * 2))
        }
    }
}

function Copy-DirectoryFailClosed {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourceRoot,
        [Parameter(Mandatory = $true)]
        [string] $DestinationRoot
    )

    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw "Expected PyInstaller runtime directory is missing: '$SourceRoot'."
    }
    if (Test-Path -LiteralPath $DestinationRoot) {
        throw "Refusing to overwrite packaged PyInstaller runtime directory: '$DestinationRoot'."
    }

    Copy-Item -LiteralPath $SourceRoot -Destination $DestinationRoot -Recurse
    if (-not (Test-Path -LiteralPath $DestinationRoot -PathType Container)) {
        throw "Failed to package PyInstaller runtime directory: '$DestinationRoot'."
    }
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$DeclaredVersionOutput = & python scripts/release_utils.py validate-project-version `
    --version $Version `
    --metadata src/hunter_metadata.py
if ($LASTEXITCODE -ne 0) {
    throw "Release version validation failed (python exit code $LASTEXITCODE)."
}
$DeclaredVersion = ($DeclaredVersionOutput | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($DeclaredVersion)) {
    throw "Release version validation returned an empty project version."
}
if ($Version -cne $DeclaredVersion) {
    throw "Release version mismatch: requested '$Version', project declares '$DeclaredVersion'."
}

$ArtifactNameOutput = & python scripts/release_utils.py artifact-name --version $Version
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the release artifact name (python exit code $LASTEXITCODE)."
}
$ArtifactName = ($ArtifactNameOutput | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($ArtifactName)) {
    throw "Release artifact-name resolution returned an empty value."
}
$PackageDir = Join-Path $ProjectRoot "dist\hunterX"
$ReleaseDir = Join-Path $ProjectRoot "dist\release"
$ArtifactPath = Join-Path $ReleaseDir $ArtifactName

Write-Host "Building HunterX $Version"
Write-Host "Artifact: $ArtifactPath"

python -m PyInstaller build_scripts/nodriver_tixcraft.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    throw "nodriver_tixcraft PyInstaller build failed (exit code $LASTEXITCODE)."
}
python -m PyInstaller build_scripts/settings.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    throw "settings PyInstaller build failed (exit code $LASTEXITCODE)."
}

if (Test-Path -LiteralPath $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $PackageDir | Out-Null
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

Copy-Item -LiteralPath "dist\nodriver_tixcraft\nodriver_tixcraft.exe" -Destination $PackageDir -Force
Copy-Item -LiteralPath "dist\settings\settings.exe" -Destination $PackageDir -Force

Copy-DirectoryFailClosed `
    -SourceRoot "dist\nodriver_tixcraft\_nodriver_internal" `
    -DestinationRoot (Join-Path $PackageDir "_nodriver_internal")
Copy-DirectoryFailClosed `
    -SourceRoot "dist\settings\_settings_internal" `
    -DestinationRoot (Join-Path $PackageDir "_settings_internal")

Copy-Item -LiteralPath "src\assets" -Destination (Join-Path $PackageDir "assets") -Recurse -Force
Copy-Item -LiteralPath "src\www" -Destination (Join-Path $PackageDir "www") -Recurse -Force

if (Test-Path -LiteralPath "build_scripts\README_Release.txt") {
    Copy-Item -LiteralPath "build_scripts\README_Release.txt" -Destination $PackageDir -Force
}
if (Test-Path -LiteralPath "README.md") {
    Copy-Item -LiteralPath "README.md" -Destination (Join-Path $PackageDir "README.md") -Force
}
if (Test-Path -LiteralPath "CHANGELOG.md") {
    Copy-Item -LiteralPath "CHANGELOG.md" -Destination (Join-Path $PackageDir "CHANGELOG.md") -Force
}
if (Test-Path -LiteralPath "LEGAL_NOTICE.md") {
    Copy-Item -LiteralPath "LEGAL_NOTICE.md" -Destination $PackageDir -Force
}
if (Test-Path -LiteralPath "LICENSE") {
    Copy-Item -LiteralPath "LICENSE" -Destination $PackageDir -Force
}
if (Test-Path -LiteralPath "guide") {
    Copy-Item -LiteralPath "guide" -Destination (Join-Path $PackageDir "guide") -Recurse -Force
}

if (Test-Path -LiteralPath $ArtifactPath) {
    Remove-Item -LiteralPath $ArtifactPath -Force
}

Compress-WithRetry -SourcePath (Join-Path $PackageDir "*") -DestinationPath $ArtifactPath

python scripts/verify_release_archive.py windows `
    --archive $ArtifactPath `
    --version $Version
if ($LASTEXITCODE -ne 0) {
    throw "Windows release archive verification failed (python exit code $LASTEXITCODE)."
}

Get-ChildItem -LiteralPath $ArtifactPath | Select-Object FullName, Length
