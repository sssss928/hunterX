param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string] $Version,

    [Parameter(Mandatory = $true)]
    [string] $BaseArchive
)

$ErrorActionPreference = "Stop"

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

$ResolvedBaseArchive = [System.IO.Path]::GetFullPath(
    (Join-Path $ProjectRoot $BaseArchive)
)
if (-not (Test-Path -LiteralPath $ResolvedBaseArchive -PathType Leaf)) {
    throw "Verified v0.4.9 Windows baseline archive is missing: '$ResolvedBaseArchive'."
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

Write-Host "Building HunterX $Version from verified HunterX v0.4.9 Windows baseline"
Write-Host "Baseline: $ResolvedBaseArchive"
Write-Host "Artifact: $ArtifactPath"

python scripts/build_windows_from_base.py `
    --version $Version `
    --base-archive $ResolvedBaseArchive `
    --project-root $ProjectRoot `
    --package-dir $PackageDir `
    --output $ArtifactPath
if ($LASTEXITCODE -ne 0) {
    throw "Windows v0.4.9-baseline build failed (python exit code $LASTEXITCODE)."
}

& cscript.exe //nologo scripts/verify_windows_shell_zip.js $ArtifactPath
if ($LASTEXITCODE -ne 0) {
    throw "Windows Shell ZIP verification failed (cscript exit code $LASTEXITCODE)."
}

Get-ChildItem -LiteralPath $ArtifactPath | Select-Object FullName, Length
