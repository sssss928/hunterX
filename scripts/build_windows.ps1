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

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$ArtifactName = (& python scripts/release_utils.py artifact-name --version $Version).Trim()
$PackageDir = Join-Path $ProjectRoot "dist\hunterX"
$ReleaseDir = Join-Path $ProjectRoot "dist\release"
$ArtifactPath = Join-Path $ReleaseDir $ArtifactName

Write-Host "Building HunterX $Version"
Write-Host "Artifact: $ArtifactPath"

python -m PyInstaller build_scripts/nodriver_tixcraft.spec --clean --noconfirm
python -m PyInstaller build_scripts/settings.spec --clean --noconfirm

if (Test-Path -LiteralPath $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $PackageDir | Out-Null
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

Copy-Item -LiteralPath "dist\nodriver_tixcraft\nodriver_tixcraft.exe" -Destination $PackageDir -Force
Copy-Item -LiteralPath "dist\settings\settings.exe" -Destination $PackageDir -Force

if (Test-Path -LiteralPath "dist\nodriver_tixcraft\_internal") {
    Copy-Item -LiteralPath "dist\nodriver_tixcraft\_internal" -Destination (Join-Path $PackageDir "_internal") -Recurse -Force
}

if (Test-Path -LiteralPath "dist\settings\_internal") {
    $InternalDir = Join-Path $PackageDir "_internal"
    New-Item -ItemType Directory -Path $InternalDir -Force | Out-Null
    Get-ChildItem -LiteralPath "dist\settings\_internal" -Force |
        Copy-Item -Destination $InternalDir -Recurse -Force
}

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
if (Test-Path -LiteralPath "guide") {
    Copy-Item -LiteralPath "guide" -Destination (Join-Path $PackageDir "guide") -Recurse -Force
}

if (Test-Path -LiteralPath $ArtifactPath) {
    Remove-Item -LiteralPath $ArtifactPath -Force
}

Compress-WithRetry -SourcePath (Join-Path $PackageDir "*") -DestinationPath $ArtifactPath

Get-ChildItem -LiteralPath $ArtifactPath | Select-Object FullName, Length
