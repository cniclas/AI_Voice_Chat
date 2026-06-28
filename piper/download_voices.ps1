# Download Piper voice models for EN and ES (Windows / PowerShell)

$VoicesDir = Join-Path $PSScriptRoot "voices"
New-Item -ItemType Directory -Force -Path $VoicesDir | Out-Null

$BaseUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

$Files = @(
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
    "es/es_MX/claude/high/es_MX-claude-high.onnx",
    "es/es_MX/claude/high/es_MX-claude-high.onnx.json"
)

foreach ($RelPath in $Files) {
    $Url = "$BaseUrl/$RelPath"
    $FileName = Split-Path $RelPath -Leaf
    $Dest = Join-Path $VoicesDir $FileName
    Write-Host "Downloading $FileName ..."
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
}

Write-Host "Done. Voice models saved to $VoicesDir"
