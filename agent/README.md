# pc-broker shutdown agent

A tiny, dependency-free HTTP service that runs **on the Windows gaming PC**.
Wake-on-LAN can only power the PC *on*; this agent lets the broker shut it
*down*. The broker's `POST /api/power/off` calls `SHUTDOWN_AGENT_URL/shutdown`,
which is this service.

Uses only the Python standard library — no `pip install` required on the PC.

## Endpoints

| Method | Path        | Auth         | Action                              |
|--------|-------------|--------------|-------------------------------------|
| GET    | `/health`   | none         | liveness check                      |
| GET    | `/activity` | Bearer token | user-activity report (idle shutdown)|
| POST   | `/shutdown` | Bearer token | `shutdown /s /t 0`                  |
| POST   | `/restart`  | Bearer token | `shutdown /r /t 0`                  |

`/activity` reports per-session state/lock/idle (WTS API), console idle time
(via a logon-triggered in-session reporter task, since WTS omits last-input
for the local console), and GPU utilization. The agent only reports raw
facts; the broker applies thresholds. Anything unknown reads as "user is
active" so the broker never shuts down on ambiguous data.

Auth is `Authorization: Bearer <SHUTDOWN_AGENT_TOKEN>`. The token must match the
`SHUTDOWN_AGENT_TOKEN` key in the broker's `pc-broker-secrets` k8s secret.

The agent **never** shuts the PC down on startup — the only path to a real
`shutdown` is an authenticated `POST /shutdown` or `/restart`.

## Dry run (test safely first)

Install with `-DryRun` so authorized shutdown/restart requests are **logged but
not executed** — the machine stays on. Use this to confirm the whole
phone → broker → agent path works, then re-install without `-DryRun` to arm it.

```powershell
cd agent
./install.ps1 -Token '<token>' -DryRun        # safe: logs, never powers off
# ...verify end-to-end, then arm it:
./install.ps1 -Token '<token>'                # real shutdowns
```

In dry-run the `/shutdown` response includes `"dry_run": true` and the agent log
shows `DRY RUN: would execute: shutdown /s /t 0 (machine NOT affected)`.
(Equivalent env var: `AGENT_DRY_RUN=1`.)

## Install (run as Administrator)

```powershell
cd agent
./install.ps1 -Token '<same-token-as-the-k8s-secret>' -Subnet 192.168.1.0/24
```

This registers a scheduled task that starts the agent at boot as `SYSTEM` (so it
answers even before anyone logs in), stores the token as a machine env var, and
opens an inbound firewall rule for the port (default 8001) scoped to the LAN.

Verify (returns `{"status":"ok"}` and the PC stays on):

```powershell
curl http://localhost:8001/health
```

If you install from a separate admin account, pass `-ReporterUser <daily-user>`
so the console idle reporter runs for the account that actually uses the PC
(otherwise idle auto-shutdown silently never arms — fail-safe, but useless).

Uninstall (run as Administrator — removes both tasks, the firewall rule, and
the machine env vars the installer set, including the token):

```powershell
Unregister-ScheduledTask -TaskName pc-broker-agent -Confirm:$false
Unregister-ScheduledTask -TaskName pc-broker-idle-reporter -Confirm:$false
Remove-NetFirewallRule -DisplayName pc-broker-agent
[Environment]::SetEnvironmentVariable("SHUTDOWN_AGENT_TOKEN", $null, "Machine")
[Environment]::SetEnvironmentVariable("AGENT_PORT", $null, "Machine")
[Environment]::SetEnvironmentVariable("AGENT_DRY_RUN", $null, "Machine")
```

## Headless Ollama (LLM chat)

The broker's chat panel talks to Ollama on this PC directly (port 11434).
Ollama's standard installer only autostarts per-user at login, which never
happens on a machine woken remotely — so `install-ollama.ps1` registers it as
a startup scheduled task running as `SYSTEM`, mirroring the agent:

```powershell
winget install Ollama.Ollama    # or the standalone zip
cd agent
./install-ollama.ps1 -AllowFrom 192.168.1.163   # broker / k3s node IP
```

The script binds Ollama to the LAN (`OLLAMA_HOST=0.0.0.0:11434`), points it at
a shared model directory (`OLLAMA_MODELS=C:\ollama\models` — SYSTEM would
otherwise use its own profile and miss interactively pulled models), disables
the per-user autostart so the two instances don't fight over the port, opens a
firewall rule scoped to **only** the `-AllowFrom` hosts (Ollama's API has no
auth and includes destructive endpoints, so never open it to the whole LAN),
and pre-pulls `qwen3:8b` and `gemma3:4b`.

Verify after a reboot **without logging in**, from the broker host:

```powershell
curl http://<pc-ip>:11434/api/tags
```

Uninstall (run as Administrator):

```powershell
Unregister-ScheduledTask -TaskName pc-broker-ollama -Confirm:$false
Remove-NetFirewallRule -DisplayName pc-broker-ollama
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", $null, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $null, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", $null, "Machine")
```

## Wake-on-LAN prerequisites (one-time, on the PC)

Power-*on* (priority 1) depends on these — the agent is not involved in waking:

1. **BIOS/UEFI:** enable "Wake-on-LAN" / "Power On by PCIE/PCI" / "Power On by LAN".
2. **NIC adapter** (Device Manager → network adapter → Properties):
   - Power Management: check *Allow this device to wake the computer*.
   - Advanced: enable *Wake on Magic Packet*.
3. **Disable Fast Startup** (Control Panel → Power Options → Choose what the power
   buttons do → uncheck *Turn on fast startup*). Windows' hybrid shutdown
   commonly disables WoL after a full shutdown; disabling Fast Startup fixes it.
   (Alternatively, sleep/hibernate instead of full shutdown.)
4. **Static DHCP reservation** for this PC so `PC_HOST` stays stable.
5. Record the NIC **MAC address** (`getmac /v`) for the broker's `PC_MAC` secret.
