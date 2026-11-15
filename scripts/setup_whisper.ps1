# Downloads the whisper.cpp Windows CPU build and an English Whisper model.
# Usage (from the project folder):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1                 # base.en (~57 MB)
#   powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1 -Model small.en-q5_1
param(
    [string]$Model = "base.en-q5_1"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster

$root    = Split-Path -Parent $PSScriptRoot
$tools   = Join-Path $root "tools\whisper"
$models  = Join-Path $root "models"
New-Item -ItemType Directory -Force $tools, $models | Out-Null

# 1. whisper.cpp binaries (official GitHub release, CPU x64 build)
if (-not (Test-Path (Join-Path $tools "whisper-cli.exe"))) {
    Write-Host "Finding latest whisper.cpp release..."
    # Version tags (v1.x.y) have no files; binaries are attached to build releases (bNNNN).
    $releases = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=10" -Headers @{ "User-Agent" = "setup-script" }
    $rel = $null; $asset = $null
    foreach ($r in $releases) {
        $a = $r.assets | Where-Object { $_.name -eq "whisper-bin-x64.zip" } | Select-Object -First 1
        if ($a) { $rel = $r; $asset = $a; break }
    }
    if (-not $asset) { throw "whisper-bin-x64.zip not found in the 10 most recent releases" }
    Write-Host "Downloading $($asset.name) from $($rel.tag_name) ($([math]::Round($asset.size/1MB,1)) MB)..."
    $zip = Join-Path $env:TEMP "whisper-bin-x64.zip"
    $tmp = Join-Path $env:TEMP "whisper-bin-x64"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive $zip -DestinationPath $tmp
    $cli = Get-ChildItem $tmp -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
    if (-not $cli) { throw "whisper-cli.exe not found inside the zip" }
    Copy-Item (Join-Path $cli.DirectoryName "*") $tools -Recurse -Force
    Remove-Item -Recurse -Force $tmp, $zip
    Write-Host "whisper.cpp installed to $tools"
} else {
    Write-Host "whisper-cli.exe already present."
}

# 2. Model (official ggml models on Hugging Face)
$file = "ggml-$Model.bin"
$dest = Join-Path $models $file
if (-not (Test-Path $dest)) {
    Write-Host "Downloading model $file ..."
    Invoke-WebRequest "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$file" -OutFile "$dest.part"
    Move-Item "$dest.part" $dest -Force
    Write-Host "Model saved to $dest"
} else {
    Write-Host "Model $file already present."
}
Write-Host "Done."
