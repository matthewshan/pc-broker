<#
.SYNOPSIS
    Install the pc-broker shutdown agent as an always-on Windows scheduled task.

.DESCRIPTION
    Registers a scheduled task that runs agent.py at system startup as SYSTEM
    (so it is reachable after Wake-on-LAN, before anyone logs in), stores the
    shared token as a machine environment variable, and opens an inbound
    firewall rule for the agent port scoped to the LAN subnet.

    Run this from an elevated (Administrator) PowerShell prompt.

.PARAMETER Token
    Shared secret. Must match SHUTDOWN_AGENT_TOKEN in the broker's k8s secret.

.PARAMETER Port
    TCP port the agent listens on (default 8001).

.PARAMETER Subnet
    LAN subnet allowed to reach the agent through the firewall (default
    192.168.1.0/24).

.PARAMETER DryRun
    If set, the agent logs shutdown/restart requests but does NOT power the
    machine off. Use this for the first end-to-end test, then re-run install
    without -DryRun to arm it for real.

.EXAMPLE
    ./install.ps1 -Token 's3cr3t' -Subnet 192.168.1.0/24

.EXAMPLE
    ./install.ps1 -Token 's3cr3t' -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Token,
    [int]    $Port   = 8001,
    [string] $Subnet = "192.168.1.0/24",
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"
$TaskName  = "pc-broker-agent"
$AgentPath = Join-Path $PSScriptRoot "agent.py"

# Locate Python
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "Python not found on PATH. Install Python 3 first." }
if (-not (Test-Path $AgentPath)) { throw "agent.py not found next to this script." }

Write-Host "Python:     $python"
Write-Host "Agent:      $AgentPath"
Write-Host "Port:       $Port"
Write-Host "Subnet:     $Subnet"
Write-Host "Dry run:    $($DryRun.IsPresent)"

# Persist config as machine-level env vars (visible to the SYSTEM account).
[Environment]::SetEnvironmentVariable("SHUTDOWN_AGENT_TOKEN", $Token, "Machine")
[Environment]::SetEnvironmentVariable("AGENT_PORT", "$Port", "Machine")
# Set (or clear) dry-run so re-running install without -DryRun arms it for real.
[Environment]::SetEnvironmentVariable("AGENT_DRY_RUN", $(if ($DryRun) { "1" } else { $null }), "Machine")

# (Re)register the scheduled task.
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action    = New-ScheduledTaskAction -Execute $python -Argument "`"$AgentPath`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "pc-broker shutdown agent" | Out-Null

# Firewall rule (idempotent).
if (Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $TaskName
}
New-NetFirewallRule -DisplayName $TaskName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress $Subnet | Out-Null

# Start it now so you don't have to reboot to test.
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
if ($DryRun) {
    Write-Host "Installed in DRY RUN mode: shutdown requests are logged, NOT executed." -ForegroundColor Yellow
    Write-Host "Re-run without -DryRun to arm real shutdowns." -ForegroundColor Yellow
} else {
    Write-Host "Installed (ARMED): authorized shutdown requests WILL power off the PC." -ForegroundColor Green
}
Write-Host "Test with:" -ForegroundColor Green
Write-Host "  curl http://localhost:$Port/health"
Write-Host "Uninstall with:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "  Remove-NetFirewallRule -DisplayName $TaskName"
