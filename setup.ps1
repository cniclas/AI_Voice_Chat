<#
.SYNOPSIS
    One-shot setup for AI Voice Chat on native Windows.

.DESCRIPTION
    Downloads and installs everything the app needs to run:
      1. Python dependencies via `uv sync` (includes the CUDA build of PyTorch).
      2. Kokoro TTS model weights (English + Spanish), cached from Hugging Face.
      3. The Ollama LLM used by the tutor (llama3.1:8b).

    The script is idempotent: it skips steps whose output already exists, so it
    is safe to re-run. Missing prerequisites (uv, Ollama) are auto-installed via
    winget when available; otherwise install links are printed.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1

    Runs setup without changing your machine's execution policy.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

# Must match OLLAMA_MODEL in main.py
$OllamaModel = "llama3.1:8b"

function Write-Step($msg)    { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)      { Write-Host "  [ok] $msg"    -ForegroundColor Green }
function Write-Skip($msg)    { Write-Host "  [skip] $msg"  -ForegroundColor DarkGray }
function Write-Warn2($msg)   { Write-Host "  [warn] $msg"  -ForegroundColor Yellow }

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Ensure-Tool {
    param(
        [string]$Command,     # command to probe on PATH
        [string]$WingetId,    # winget package id (empty to skip auto-install)
        [string]$InstallUrl   # manual install link for the fallback message
    )
    if (Test-Command $Command) {
        Write-Ok "$Command found"
        return $true
    }
    Write-Warn2 "$Command not found."
    if ($WingetId -and (Test-Command "winget")) {
        Write-Host "  Installing $Command via winget ($WingetId)..."
        winget install --id $WingetId --accept-source-agreements --accept-package-agreements -e
        # winget updates PATH for new shells; make the tool usable in this one too.
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")
        if (Test-Command $Command) {
            Write-Ok "$Command installed"
            return $true
        }
        Write-Warn2 "Installed $Command but it is not on PATH yet. Open a new terminal and re-run setup.ps1."
        return $false
    }
    Write-Warn2 "Please install $Command manually: $InstallUrl"
    return $false
}

Write-Host "AI Voice Chat - Windows setup" -ForegroundColor White

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
Write-Step "Checking prerequisites"
$haveUv     = Ensure-Tool -Command "uv"     -WingetId "astral-sh.uv" -InstallUrl "https://docs.astral.sh/uv/getting-started/installation/"
$haveOllama = Ensure-Tool -Command "ollama" -WingetId "Ollama.Ollama" -InstallUrl "https://ollama.com/download"

if (-not $haveUv) {
    throw "uv is required to install Python dependencies. Install it and re-run setup.ps1."
}

# ---------------------------------------------------------------------------
# 2. Python dependencies (installs the CUDA build of torch per pyproject.toml)
# ---------------------------------------------------------------------------
Write-Step "Installing Python dependencies (uv sync)"
Push-Location $RepoRoot
try {
    uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit $LASTEXITCODE)." }
    Write-Ok "Dependencies installed"

    # Report whether the GPU build of torch is active.
    $gpu = (uv run python -c "import torch; print('YES' if torch.cuda.is_available() else 'NO', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')" 2>$null)
    if ($gpu -like "YES*") {
        Write-Ok "PyTorch CUDA available: $($gpu.Substring(4))"
    } else {
        Write-Warn2 "PyTorch is running on CPU (no CUDA GPU detected). Transcription will be slower."
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# 3. Kokoro TTS model weights
# ---------------------------------------------------------------------------
Write-Step "Caching Kokoro TTS model weights"
# Kokoro pulls its weights (and per-language phonemizer data) from Hugging
# Face on first use, caching them under the user's HF cache dir. Warm the
# cache here so the first real session doesn't stall on a download.
Push-Location $RepoRoot
try {
    uv run python -c "from kokoro import KPipeline; KPipeline(lang_code='a'); KPipeline(lang_code='e')" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Could not pre-cache Kokoro model weights; it will download on first run instead."
    } else {
        Write-Ok "Kokoro model weights cached"
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# 4. Ollama model
# ---------------------------------------------------------------------------
Write-Step "Setting up the Ollama model ($OllamaModel)"
if (-not $haveOllama) {
    Write-Warn2 "Ollama is not installed - skipping model pull. Install Ollama, then run: ollama pull $OllamaModel"
} else {
    $installed = (ollama list 2>$null) -match [regex]::Escape($OllamaModel)
    if ($installed) {
        Write-Skip "$OllamaModel already pulled"
    } else {
        Write-Host "  Pulling $OllamaModel (~4.7 GB, this can take a while)..."
        ollama pull $OllamaModel
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 "ollama pull failed. Make sure the Ollama service is running, then retry: ollama pull $OllamaModel"
        } else {
            Write-Ok "$OllamaModel pulled"
        }
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Step "Setup complete"
Write-Host "Run the app with:" -ForegroundColor White
Write-Host "  uv run main.py" -ForegroundColor White
