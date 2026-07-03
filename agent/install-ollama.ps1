<#
.SYNOPSIS
    Run Ollama headless at startup so the broker can reach it after Wake-on-LAN.

.DESCRIPTION
    The standard Ollama installer registers a per-user login app, which is
    useless on a machine that gets woken remotely and never logged into. This
    script instead:

      1. Disables the per-user Ollama autostart (it would fight the SYSTEM
         instance for port 11434).
      2. Sets machine-level env vars: OLLAMA_HOST (LAN bind), OLLAMA_MODELS
         (shared model dir - without this, SYSTEM would use its own profile
         and models pulled interactively would be invisible), OLLAMA_KEEP_ALIVE.
      3. Registers a scheduled task that runs `ollama serve` at system startup
         as SYSTEM, mirroring the pc-broker-agent task.
      4. Opens an inbound firewall rule for the Ollama port scoped to the LAN.
      5. Pre-pulls the requested models into the shared dir.

    Run this from an elevated (Administrator) PowerShell prompt, with Ollama
    already installed (winget install Ollama.Ollama, or the standalone zip).

.PARAMETER Port
    TCP port Ollama listens on (default 11434).

.PARAMETER Subnet
    LAN subnet allowed to reach Ollama through the firewall (default
    192.168.1.0/24).

.PARAMETER ModelsDir
    Shared model directory readable by SYSTEM and interactive users
    (default C:\ollama\models).

.PARAMETER KeepAlive
    How long a model stays loaded in VRAM after the last request (default 30m).

.PARAMETER Models
    Models to pre-pull (default qwen3:8b, gemma3:4b).

.PARAMETER OllamaExe
    Explicit path to ollama.exe if it is not on PATH or in a standard location.

.EXAMPLE
    ./install-ollama.ps1 -Subnet 192.168.1.0/24
#>
[CmdletBinding()]
param(
    [int]      $Port      = 11434,
    [string]   $Subnet    = "192.168.1.0/24",
    [string]   $ModelsDir = "C:\ollama\models",
    [string]   $KeepAlive = "30m",
    [string[]] $Models    = @("qwen3:8b", "gemma3:4b"),
    [string]   $OllamaExe = ""
)

$ErrorActionPreference = "Stop"
$TaskName = "pc-broker-ollama"

# Locate ollama.exe
if (-not $OllamaExe) {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { $OllamaExe = $cmd.Source }
}
if (-not $OllamaExe) {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "C:\Program Files\Ollama\ollama.exe"
    )) {
        if (Test-Path $candidate) { $OllamaExe = $candidate; break }
    }
}
if (-not $OllamaExe -or -not (Test-Path $OllamaExe)) {
    throw "ollama.exe not found. Install Ollama first (winget install Ollama.Ollama) or pass -OllamaExe."
}

Write-Host "Ollama:     $OllamaExe"
Write-Host "Port:       $Port"
Write-Host "Subnet:     $Subnet"
Write-Host "Models dir: $ModelsDir"
Write-Host "Keep alive: $KeepAlive"
Write-Host "Models:     $($Models -join ', ')"

# 1. Disable the per-user autostart so it doesn't race the SYSTEM instance
#    for the port after an interactive login.
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $runKey -Name "Ollama" -ErrorAction SilentlyContinue) {
    Write-Host "Removing per-user Ollama autostart (HKCU Run key)"
    Remove-ItemProperty -Path $runKey -Name "Ollama"
}
$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "Ollama.lnk"
if (Test-Path $startupLnk) {
    Write-Host "Removing per-user Ollama startup shortcut"
    Remove-Item $startupLnk -Force
}
Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping running Ollama process PID $($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

# 2. Shared model dir + machine env vars (visible to SYSTEM).
if (-not (Test-Path $ModelsDir)) {
    New-Item -ItemType Directory -Force $ModelsDir | Out-Null
}
icacls $ModelsDir /grant "SYSTEM:(OI)(CI)F" /grant "Users:(OI)(CI)M" | Out-Null

[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:$Port", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $ModelsDir, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", $KeepAlive, "Machine")

# 3. (Re)register the scheduled task; stop and kill stale instances first so
#    the fresh task can bind the port (same pattern as install.ps1).
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Get-Process -Name "ollama*" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }

$action    = New-ScheduledTaskAction -Execute $OllamaExe -Argument "serve"
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "Headless Ollama for pc-broker" | Out-Null

# 4. Firewall rule (idempotent), scoped to the LAN subnet.
if (Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $TaskName
}
New-NetFirewallRule -DisplayName $TaskName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress $Subnet | Out-Null

# 5. Start now and pre-pull models into the shared dir.
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_HOST = "127.0.0.1:$Port"
foreach ($model in $Models) {
    Write-Host "Pulling $model ..." -ForegroundColor Cyan
    & $OllamaExe pull $model
    if ($LASTEXITCODE -ne 0) { Write-Warning "Pull failed for $model - retry manually: ollama pull $model" }
}

Write-Host ""
Write-Host "Installed: Ollama runs headless at startup as SYSTEM." -ForegroundColor Green
Write-Host "Verify locally:"
Write-Host "  curl http://localhost:$Port/api/version"
Write-Host "Verify from the broker's LAN:"
Write-Host "  curl http://<this-pc-ip>:$Port/api/tags"
Write-Host "Uninstall with (run as Administrator):"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "  Remove-NetFirewallRule -DisplayName $TaskName"
Write-Host "  [Environment]::SetEnvironmentVariable('OLLAMA_HOST', `$null, 'Machine')"
Write-Host "  [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', `$null, 'Machine')"
Write-Host "  [Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', `$null, 'Machine')"
