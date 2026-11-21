param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PublishDir = Join-Path $Root "publish"
$DistDir = Join-Path $Root "dist"

Write-Host "[1/4] Nettoyage du dossier publish..." -ForegroundColor Cyan
if (Test-Path $PublishDir) { Remove-Item -Recurse -Force $PublishDir }
New-Item -ItemType Directory -Force $PublishDir | Out-Null

Write-Host "[2/4] Publication de Lumino (WinUI 3 / .NET 10, self-contained win-x64)..." -ForegroundColor Cyan
dotnet publish "src\Lumino.App\Lumino.App.csproj" `
    -c Release -r win-x64 --self-contained true `
    -p:Platform=x64 `
    -p:WindowsAppSDKSelfContained=true `
    -p:DebugType=none -p:DebugSymbols=false `
    -o $PublishDir
if ($LASTEXITCODE -ne 0) { throw "Echec publication Lumino.App" }

Write-Host "[3/4] Nettoyage des langues satellites et copie des ressources..." -ForegroundColor Cyan
$KeepDirs = @("fr", "fr-FR", "en", "en-US", "Microsoft.UI.Xaml")
Get-ChildItem -Path $PublishDir -Directory | Where-Object { $KeepDirs -notcontains $_.Name } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Copy-Item -Force "lumino.ico" (Join-Path $PublishDir "lumino.ico")
if (Test-Path "README.md") { Copy-Item -Force "README.md" (Join-Path $PublishDir "README.md") }
if (Test-Path "CHANGELOG.md") { Copy-Item -Force "CHANGELOG.md" (Join-Path $PublishDir "CHANGELOG.md") }

if (-not $SkipInstaller) {
    Write-Host "[4/4] Compilation de l'installateur Inno Setup 6..." -ForegroundColor Cyan
    $IsccCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        throw "Inno Setup 6 (ISCC.exe) introuvable. Installe-le via : winget install JRSoftware.InnoSetup"
    }
    New-Item -ItemType Directory -Force $DistDir | Out-Null
    & $Iscc (Join-Path $Root "installer\Lumino.iss")
    if ($LASTEXITCODE -ne 0) { throw "Echec compilation Inno Setup" }
    Write-Host "Installateur pret dans dist\" -ForegroundColor Green
}
